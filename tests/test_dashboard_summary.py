from datetime import datetime, timedelta, timezone

from tests.support import IsolatedWebTestCase
from webapp.db import AsyncSessionLocal
from webapp.models.account import ChaoxingAccount
from webapp.models.proxy import ProxyEntry
from webapp.models.task import StudyTask, TaskLog
from webapp.services.dashboard_summary import dashboard_summary


class DashboardSummaryTests(IsolatedWebTestCase):
    async def test_empty_dashboard_is_zero_and_has_thirty_real_dates(self):
        async with AsyncSessionLocal() as db:
            summary = await dashboard_summary(db)
        self.assertEqual(summary['accounts'], 0)
        self.assertEqual(summary['total_tasks'], 0)
        self.assertEqual(summary['recent_tasks'], [])
        self.assertEqual(summary['logs'], [])
        self.assertEqual(len(summary['trend']), 30)
        self.assertTrue(all(day['completed'] == day['created'] == 0 for day in summary['trend']))

    async def test_counts_all_tasks_and_utc8_completion_boundary(self):
        today = datetime.now(timezone(timedelta(hours=8))).date()
        local_midnight_utc = datetime.combine(today, datetime.min.time()) - timedelta(hours=8)
        async with AsyncSessionLocal() as db:
            account = ChaoxingAccount(phone='13800000101', nickname='Test', password_enc=b'unused')
            error_account = ChaoxingAccount(phone='13800000102', password_enc=b'unused', status='error')
            db.add_all([account, error_account, ProxyEntry(proxy_url='http://127.0.0.1:9991'),
                        ProxyEntry(proxy_url='http://127.0.0.1:9992', status='failed')])
            await db.flush()
            tasks = [StudyTask(account_id=account.id, status='completed', finished_at=local_midnight_utc + timedelta(seconds=1)) for _ in range(25)]
            tasks += [StudyTask(account_id=account.id, status='completed', finished_at=local_midnight_utc - timedelta(seconds=1)),
                      StudyTask(account_id=account.id, status='completed'),
                      StudyTask(account_id=account.id, status='partial', succeeded_chapters=1, failed_chapters=1, done_chapters=2, total_chapters=2),
                      StudyTask(account_id=account.id, status='pending')]
            db.add_all(tasks)
            await db.flush()
            db.add(TaskLog(task_id=tasks[-2].id, level='warning', message='Test warning'))
            await db.commit()
            summary = await dashboard_summary(db)
        self.assertEqual(summary['today_completed'], 25)
        self.assertEqual(summary['completed'], 27)
        self.assertEqual(summary['total_tasks'], 29)
        self.assertEqual(summary['failed'], 1)
        self.assertEqual(summary['pending'], 1)
        self.assertEqual((summary['active_accounts'], summary['accounts']), (1, 2))
        self.assertEqual((summary['proxy_active'], summary['proxy_total']), (1, 2))
        self.assertEqual(summary['trend'][-1]['completed'], 25)
        self.assertEqual(summary['trend'][-2]['completed'], 1)
        self.assertEqual(len(summary['recent_tasks']), 20)
        self.assertEqual(summary['logs'][0]['message'], 'Test warning')
        self.assertNotIn('password', str(summary))

    async def test_admin_status_exposes_dashboard_without_weakening_auth(self):
        response = await self.client.get('/api/admin/server-status')
        self.assertEqual(response.status_code, 401)
        await self.login_admin()
        response = await self.client.get('/api/admin/server-status')
        self.assertEqual(response.status_code, 200)
        self.assertIn('dashboard', response.json())
        self.assertIn('resources', response.json())

    async def test_active_and_queued_tasks_survive_recent_list_limit(self):
        async with AsyncSessionLocal() as db:
            account = ChaoxingAccount(phone='13800000201', password_enc=b'unused')
            db.add(account)
            await db.flush()
            running = StudyTask(account_id=account.id, status='running')
            queued = StudyTask(account_id=account.id, status='pending')
            db.add_all([running, queued])
            await db.flush()
            running_id, queued_id = running.id, queued.id
            db.add_all([StudyTask(account_id=account.id, status='cancelled') for _ in range(25)])
            await db.commit()
            summary = await dashboard_summary(db)
        self.assertNotIn(running_id, [task['id'] for task in summary['recent_tasks']])
        self.assertEqual([task['id'] for task in summary['active_tasks']], [running_id, queued_id])
        self.assertEqual([task['id'] for task in summary['queue_tasks']], [queued_id])
