"""Explicit user roles must never inherit privileges from the name admin."""
from unittest.mock import patch

from tests.support import IsolatedWebTestCase

from fastapi import HTTPException
from starlette.requests import Request

from webapp.db import AsyncSessionLocal
from webapp.deps import require_admin
from webapp.models.account import ChaoxingAccount
from webapp.models.task import StudyTask, TaskStatus
from webapp.models.user import PlatformUser
from webapp.routers import accounts, pages, tasks
from webapp.services.credential import hash_admin_password


class RoleIdentityTests(IsolatedWebTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        async with AsyncSessionLocal() as db:
            user = PlatformUser(username="admin", role="user", password_hash=hash_admin_password("ordinary-pass"))
            owner = PlatformUser(username="other-owner", password_hash=hash_admin_password("other-pass"))
            db.add_all([user, owner])
            await db.flush()
            self.user_id, self.owner_id = user.id, owner.id
            account = ChaoxingAccount(user_id=owner.id, phone="13800000003", password_enc=b"unused", nickname="original")
            db.add(account)
            await db.flush()
            self.account_id = account.id
            task = StudyTask(account_id=account.id, status=TaskStatus.COMPLETED.value)
            db.add(task)
            await db.flush()
            self.task_id = task.id
            await db.commit()

    async def login_ordinary_admin(self):
        response = await self.client.post("/api/user/login", json={"username": "admin", "password": "ordinary-pass"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["role"], "user")

    async def test_require_admin_rejects_ordinary_admin_username(self):
        await self.login_ordinary_admin()
        response = await self.client.get("/api/users")
        self.assertEqual(response.status_code, 403, response.text)

    async def test_user_cannot_reassign_account_and_bypass_stream_ownership(self):
        await self.login_ordinary_admin()
        response = await self.client.put(f"/api/users/{self.user_id}/accounts/{self.account_id}")
        self.assertEqual(response.status_code, 403, response.text)
        async with AsyncSessionLocal() as db:
            account = await db.get(ChaoxingAccount, self.account_id)
            self.assertEqual(account.user_id, self.owner_id)
        response = await self.client.get(f"/api/tasks/{self.task_id}/stream")
        self.assertEqual(response.status_code, 403, response.text)

    async def test_account_list_is_scoped_to_ordinary_admin_username(self):
        await self.login_ordinary_admin()
        response = await self.client.get("/api/accounts")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    async def test_account_edit_rejects_ordinary_admin_username(self):
        await self.login_ordinary_admin()
        response = await self.client.put(f"/api/accounts/{self.account_id}", json={"nickname": "changed"})
        self.assertEqual(response.status_code, 403, response.text)
        async with AsyncSessionLocal() as db:
            account = await db.get(ChaoxingAccount, self.account_id)
            self.assertEqual(account.nickname, "original")

    async def test_account_network_operations_are_rejected_before_network(self):
        await self.login_ordinary_admin()
        with patch("webapp.routers.accounts.ChaoxingService.verify_login", return_value={"status": True}) as verify, patch("webapp.routers.accounts.ChaoxingService.fetch_courses", return_value=[]) as fetch:
            response = await self.client.post(f"/api/accounts/{self.account_id}/login")
            self.assertEqual(response.status_code, 403, response.text)
            response = await self.client.get(f"/api/accounts/{self.account_id}/courses")
            self.assertEqual(response.status_code, 403, response.text)
            verify.assert_not_called()
            fetch.assert_not_called()

    async def test_created_account_belongs_to_ordinary_admin_username(self):
        await self.login_ordinary_admin()
        response = await self.client.post("/api/accounts", json={"phone": "13800000004", "password": "unused", "verify_login": False})
        self.assertEqual(response.status_code, 201, response.text)
        async with AsyncSessionLocal() as db:
            account = await db.get(ChaoxingAccount, response.json()["id"])
            self.assertEqual(account.user_id, self.user_id)

    async def test_admin_pages_redirect_ordinary_admin_username(self):
        await self.login_ordinary_admin()
        for path in ("/dashboard", "/accounts", "/tasks", "/settings", "/users", "/proxies", "/update"):
            with self.subTest(path=path):
                response = await self.client.get(path)
                self.assertEqual(response.status_code, 302, response.text[:200])
                self.assertEqual(response.headers["location"], "/user/dashboard")

    async def test_role_identity_consistent_with_legacy_fallback(self):
        for session, expected in (
            ({"role": "user", "user": "admin"}, False),
            ({"role": "", "user": "admin"}, False),
            ({"role": None, "user": "admin"}, False),
            ({"role": "admin", "user": "someone"}, True),
            ({"user": "admin"}, True),
            ({"user": "someone"}, False),
        ):
            request = Request({"type": "http", "session": {"authenticated": True, **session}})
            with self.subTest(session=session):
                self.assertEqual(accounts._is_admin(request), expected)
                self.assertEqual(tasks._is_admin(request), expected)
                self.assertEqual(pages._ctx(request)["is_admin"], expected)
                expected_role = session["role"] if "role" in session else ("admin" if expected else None)
                self.assertEqual(pages._ctx(request)["role"], expected_role)
                self.assertEqual(pages._require_admin_page(request) is None, expected)
                if expected:
                    await require_admin(request)
                else:
                    with self.assertRaises(HTTPException) as rejected:
                        await require_admin(request)
                    self.assertEqual(rejected.exception.status_code, 403)

    async def test_admin_retains_user_account_and_page_access(self):
        await self.login_admin()
        self.assertEqual((await self.client.get("/api/users")).status_code, 200)
        response = await self.client.get("/api/accounts")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()], [self.account_id])
        response = await self.client.put(f"/api/accounts/{self.account_id}", json={"nickname": "admin-change"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((await self.client.get("/dashboard")).status_code, 200)

    async def test_unauthenticated_admin_api_and_page_are_rejected(self):
        self.assertEqual((await self.client.get("/api/users")).status_code, 401)
        response = await self.client.get("/dashboard")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/login")
