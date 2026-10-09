from unittest.mock import patch

from tests.support import IsolatedWebTestCase
from webapp.db import AsyncSessionLocal
from webapp.models.task import StudyTask
from webapp.services.task_runner import task_runner


class ButtonEffectsTests(IsolatedWebTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.login_admin()

    async def account(self):
        response = await self.client.post('/api/accounts', json={
            'phone': '13800000123', 'password': 'test-pass', 'verify_login': False})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['id']

    async def test_account_create_relogin_courses_and_delete_have_real_effects(self):
        account_id = await self.account()
        with patch('webapp.routers.accounts.ChaoxingService.verify_login', return_value={
            'status': True, 'nickname': 'Verified'}):
            self.assertTrue((await self.client.post(f'/api/accounts/{account_id}/login')).json()['status'])
        accounts = (await self.client.get('/api/accounts')).json()
        self.assertEqual(accounts[0]['nickname'], 'Verified')
        with patch('webapp.routers.accounts.ChaoxingService.fetch_courses', return_value=[{'id': 'course-1'}]) as fetch:
            courses = (await self.client.get(f'/api/accounts/{account_id}/courses?refresh=true')).json()
            self.assertEqual(courses['courses'][0]['id'], 'course-1')
            self.assertTrue(fetch.call_args.args[1])
        self.assertEqual((await self.client.delete(f'/api/accounts/{account_id}')).status_code, 204)
        self.assertEqual((await self.client.get('/api/accounts')).json(), [])

    async def test_user_create_bind_unbind_status_password_delete(self):
        account_id = await self.account()
        response = await self.client.post('/api/users', json={'username': 'button-user', 'password': 'test1234'})
        self.assertEqual(response.status_code, 201, response.text)
        user_id = response.json()['id']
        self.assertEqual((await self.client.put(f'/api/users/{user_id}/accounts/{account_id}')).status_code, 200)
        self.assertEqual((await self.client.get('/api/users/account-options')).json()[0]['user_id'], user_id)
        self.assertEqual((await self.client.put(f'/api/users/{user_id}', json={'status': 'disabled'})).status_code, 200)
        overview = (await self.client.get('/api/users/overview')).json()
        self.assertEqual(overview[0]['status'], 'disabled')
        self.assertEqual((await self.client.put(f'/api/users/{user_id}', json={'password': 'new1234', 'status': 'active'})).status_code, 200)
        self.assertEqual((await self.client.delete(f'/api/users/{user_id}/accounts/{account_id}')).status_code, 200)
        self.assertIsNone((await self.client.get('/api/users/account-options')).json()[0]['user_id'])
        await self.client.post('/api/logout')
        self.assertEqual((await self.client.post('/api/user/login', json={'username': 'button-user', 'password': 'test1234'})).status_code, 401)
        self.assertEqual((await self.client.post('/api/user/login', json={'username': 'button-user', 'password': 'new1234'})).status_code, 200)
        await self.login_admin()
        self.assertEqual((await self.client.delete(f'/api/users/{user_id}')).status_code, 204)
        self.assertEqual((await self.client.get('/api/users/overview')).json(), [])

    async def test_task_submission_persists_selection_and_cancel_changes_status(self):
        account_id = await self.account()
        with patch.object(task_runner, 'submit') as submit:
            response = await self.client.post('/api/tasks', json={
                'account_id': account_id, 'course_ids': ['selected-course'],
                'jobs': 1, 'speed': 1.5, 'mode': 'normal', 'notopen_action': 'continue'})
            self.assertEqual(response.status_code, 201, response.text)
            task_id = response.json()['id']
            submit.assert_called_once_with(task_id)
        async with AsyncSessionLocal() as db:
            task = await db.get(StudyTask, task_id)
            self.assertEqual(task.course_ids, '["selected-course"]')
            self.assertEqual(task.speed, 1.5)
        response = await self.client.post(f'/api/tasks/{task_id}/cancel')
        self.assertTrue(response.json()['status'])
        self.assertEqual((await self.client.get(f'/api/tasks/{task_id}')).json()['status'], 'cancelled')
        self.assertFalse((await self.client.post(f'/api/tasks/{task_id}/cancel')).json()['status'])

    async def test_settings_save_round_trip_and_validation(self):
        for section, payload in [('tiku', {'provider': 'TikuAdapter', 'url': 'https://example.com/test', 'submit': False}),
                                 ('proxy', {'source': 'local', 'enabled': True, 'strategy': 'round_robin'})]:
            self.assertTrue((await self.client.post(f'/api/settings/{section}', json=payload)).json()['status'])
            persisted = (await self.client.get(f'/api/settings/{section}')).json()
            for key, value in payload.items():
                self.assertEqual(persisted[key], value)
        self.assertEqual((await self.client.post('/api/settings/tiku', json={'cover_rate': 2})).status_code, 422)

    async def test_proxy_add_test_enable_disable_bulk_delete(self):
        passed = {'ok': True, 'proxy_url': 'http://127.0.0.1:9999', 'latency_ms': 12}
        with patch('webapp.routers.proxies.test_proxy', return_value=passed):
            response = await self.client.post('/api/proxies', json={'proxy_url': passed['proxy_url']})
            self.assertEqual(response.status_code, 201, response.text)
            proxy_id = response.json()['added'][0]['id']
            self.assertEqual((await self.client.post(f'/api/proxies/{proxy_id}/disable')).json()['status'], 'disabled')
            self.assertEqual((await self.client.post(f'/api/proxies/{proxy_id}/enable')).json()['status'], 'active')
            self.assertTrue((await self.client.post(f'/api/proxies/{proxy_id}/test')).json()['ok'])
            self.assertEqual((await self.client.post('/api/proxies/bulk/test', json={'ids': [proxy_id]})).json()['passed'], 1)
        response = await self.client.post('/api/proxies/bulk/delete', json={'ids': [proxy_id]})
        self.assertEqual(response.json()['deleted'], 1)
        self.assertEqual((await self.client.get('/api/proxies')).json(), [])
