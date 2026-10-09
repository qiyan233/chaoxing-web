from tests.support import IsolatedWebTestCase
from webapp.db import AsyncSessionLocal
from webapp.models.account import ChaoxingAccount
from webapp.models.task import StudyTask, TaskLog
from webapp.models.user import PlatformUser
from webapp.services.credential import hash_admin_password


class LogBrowserTests(IsolatedWebTestCase):
    async def seed(self):
        async with AsyncSessionLocal() as db:
            user = PlatformUser(username='log-reader', password_hash=hash_admin_password('test1234'))
            db.add(user)
            await db.flush()
            mine = ChaoxingAccount(phone='13800000001', nickname='Mine', password_enc=b'unused', user_id=user.id)
            other = ChaoxingAccount(phone='13800000002', nickname='Other', password_enc=b'unused')
            db.add_all([mine, other])
            await db.flush()
            tasks = [StudyTask(account_id=mine.id), StudyTask(account_id=other.id)]
            db.add_all(tasks)
            await db.flush()
            db.add_all([TaskLog(task_id=tasks[0].id, message='visible', level='warning'),
                        TaskLog(task_id=tasks[1].id, message='private', level='error'),
                        TaskLog(task_id=tasks[0].id, message='new visible', level='info')])
            await db.commit()

    async def test_anonymous_cannot_browse_logs(self):
        self.assertEqual((await self.client.get('/api/tasks/logs')).status_code, 401)

    async def test_admin_pagination_filters_and_real_new_task_route(self):
        await self.seed()
        await self.login_admin()
        data = (await self.client.get('/api/tasks/logs?limit=2')).json()
        self.assertEqual(data['total'], 3)
        self.assertEqual([row['message'] for row in data['items']], ['new visible', 'private'])
        data = (await self.client.get('/api/tasks/logs?limit=2&offset=2')).json()
        self.assertEqual([row['message'] for row in data['items']], ['visible'])
        data = (await self.client.get('/api/tasks/logs?level=warning&q=Mine')).json()
        self.assertEqual(data['total'], 1)
        self.assertEqual((await self.client.get('/logs')).status_code, 200)
        self.assertIn('选择学习通账号', (await self.client.get('/tasks/new')).text)

    async def test_user_only_sees_owned_logs_and_route(self):
        await self.seed()
        response = await self.client.post('/api/user/login', json={'username': 'log-reader', 'password': 'test1234'})
        self.assertEqual(response.status_code, 200)
        data = (await self.client.get('/api/tasks/logs')).json()
        self.assertEqual(data['total'], 2)
        self.assertNotIn('private', str(data))
        self.assertEqual((await self.client.get('/api/tasks/logs?q=private')).json()['total'], 0)
        self.assertEqual((await self.client.get('/user/logs')).status_code, 200)
        self.assertIn('/user/accounts/', (await self.client.get('/user/tasks/new')).text)
