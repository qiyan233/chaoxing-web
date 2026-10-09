"""SSE must enforce the same task ownership rules as task details."""
import asyncio
import json
from unittest.mock import AsyncMock, patch

from tests.support import IsolatedWebTestCase

from webapp.db import AsyncSessionLocal
from webapp.models.account import ChaoxingAccount
from webapp.models.task import StudyTask, TaskStatus
from webapp.models.user import PlatformUser
from webapp.routers.tasks import _is_admin
from webapp.services.credential import hash_admin_password
from starlette.requests import Request


class SSEAccessTests(IsolatedWebTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        async with AsyncSessionLocal() as db:
            owner = PlatformUser(username="stream-owner", password_hash=hash_admin_password("owner-pass"))
            other = PlatformUser(username="stream-other", password_hash=hash_admin_password("other-pass"))
            admin_named_user = PlatformUser(username="admin", role="user", password_hash=hash_admin_password("ordinary-admin-pass"))
            db.add_all([owner, other, admin_named_user])
            await db.flush()
            account = ChaoxingAccount(phone="13800000001", user_id=owner.id, password_enc=b"unused")
            db.add(account)
            await db.flush()
            self.task_ids = {}
            for state in (TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.COMPLETED, TaskStatus.PARTIAL,
                          TaskStatus.FAILED, TaskStatus.CANCELLED):
                task = StudyTask(account_id=account.id, status=state.value,
                                 total_chapters=3, done_chapters=1)
                db.add(task)
                await db.flush()
                self.task_ids[state.value] = task.id
            orphan_account = ChaoxingAccount(phone="13800000002", password_enc=b"unused")
            db.add(orphan_account)
            await db.flush()
            orphan_task = StudyTask(account_id=orphan_account.id, status=TaskStatus.COMPLETED.value)
            db.add(orphan_task)
            await db.flush()
            self.orphan_task_id = orphan_task.id
            await db.commit()

    async def login_user(self, username="stream-owner", password="owner-pass"):
        response = await self.client.post("/api/user/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.text)

    def mock_bus(self):
        queue = asyncio.Queue()
        queue.put_nowait({"type": "task_done", "status": "completed"})
        return patch("webapp.routers.stream.progress_bus.subscribe", new=AsyncMock(return_value=queue))

    async def test_unauthenticated_cannot_subscribe(self):
        with self.mock_bus() as subscribe:
            for task_id in self.task_ids.values():
                response = await self.client.get(f"/api/tasks/{task_id}/stream")
                self.assertEqual(response.status_code, 401)
            subscribe.assert_not_awaited()

    async def test_other_user_cannot_access_active_or_terminal_tasks(self):
        await self.login_user("stream-other", "other-pass")
        with self.mock_bus() as subscribe:
            for state, task_id in self.task_ids.items():
                with self.subTest(state=state):
                    queue = asyncio.Queue()
                    queue.put_nowait({"type": "task_done", "status": "completed"})
                    subscribe.return_value = queue
                    response = await self.client.get(f"/api/tasks/{task_id}/stream")
                    self.assertEqual(response.status_code, 403, response.text)
                    self.assertEqual(response.json()["detail"], "无权访问该任务")
            subscribe.assert_not_awaited()

    async def test_user_named_admin_cannot_access_other_users_stream(self):
        await self.login_user("admin", "ordinary-admin-pass")
        with self.mock_bus() as subscribe:
            for state, task_id in self.task_ids.items():
                with self.subTest(state=state):
                    queue = asyncio.Queue()
                    queue.put_nowait({"type": "task_done", "status": "completed"})
                    subscribe.return_value = queue
                    response = await self.client.get(f"/api/tasks/{task_id}/stream")
                    self.assertEqual(response.status_code, 403, response.text)
            subscribe.assert_not_awaited()

    async def test_user_named_admin_cannot_access_other_users_task_details(self):
        await self.login_user("admin", "ordinary-admin-pass")
        for state, task_id in self.task_ids.items():
            with self.subTest(state=state):
                response = await self.client.get(f"/api/tasks/{task_id}")
                self.assertEqual(response.status_code, 403, response.text)

    def test_admin_role_takes_precedence_over_username_and_legacy_fallback(self):
        for session, expected in (
            ({"role": "user", "user": "admin"}, False),
            ({"role": "", "user": "admin"}, False),
            ({"role": "admin", "user": "someone"}, True),
            ({"user": "admin"}, True),
            ({"user": "someone"}, False),
        ):
            with self.subTest(session=session):
                request = Request({"type": "http", "session": session})
                self.assertEqual(_is_admin(request), expected)

    async def test_owner_can_access_active_and_terminal_tasks(self):
        await self.login_user()
        with self.mock_bus() as subscribe, patch("webapp.routers.stream.progress_bus.unsubscribe", new=AsyncMock()) as unsubscribe:
            for state, task_id in self.task_ids.items():
                with self.subTest(state=state):
                    # Every active request needs its own completion event.
                    queue = asyncio.Queue()
                    queue.put_nowait({"type": "task_done", "status": "completed"})
                    subscribe.return_value = queue
                    response = await self.client.get(f"/api/tasks/{task_id}/stream")
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
                    first_event = json.loads(response.text.split("data: ", 1)[1].split("\n", 1)[0])
                    expected_type = "snapshot" if state in ("pending", "running") else "task_replay_end"
                    self.assertEqual(first_event["type"], expected_type)
                    self.assertEqual(first_event["status"], state)
            self.assertEqual(subscribe.await_count, 2)
            self.assertEqual(unsubscribe.await_count, 2)

    async def test_admin_can_access_all_task_states(self):
        await self.login_admin()
        with self.mock_bus() as subscribe, patch("webapp.routers.stream.progress_bus.unsubscribe", new=AsyncMock()):
            for task_id in [*self.task_ids.values(), self.orphan_task_id]:
                queue = asyncio.Queue()
                queue.put_nowait({"type": "task_done", "status": "completed"})
                subscribe.return_value = queue
                response = await self.client.get(f"/api/tasks/{task_id}/stream")
                self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(subscribe.await_count, 2)

    async def test_owner_cannot_access_unassigned_account_task(self):
        await self.login_user()
        with self.mock_bus() as subscribe:
            response = await self.client.get(f"/api/tasks/{self.orphan_task_id}/stream")
            self.assertEqual(response.status_code, 403)
            subscribe.assert_not_awaited()

    async def test_authenticated_missing_task_returns_404_without_subscription(self):
        await self.login_user()
        with self.mock_bus() as subscribe:
            response = await self.client.get("/api/tasks/999999/stream")
            self.assertEqual(response.status_code, 404)
            subscribe.assert_not_awaited()
