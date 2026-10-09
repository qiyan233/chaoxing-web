"""Load the application against temporary data without starting study jobs.

Import this module before application modules in every regression test. The
production data directory, account credentials, and log files remain untouched.
"""
from __future__ import annotations

import atexit
import sys
import tempfile
import types
import unittest
from pathlib import Path

import httpx
from loguru import logger


PROJECT_ROOT = Path(__file__).resolve().parent.parent
_temporary_data = tempfile.TemporaryDirectory(prefix="chaoxing-regression-")
atexit.register(_temporary_data.cleanup)

if "webapp.config" in sys.modules:
    raise RuntimeError("Import tests.support before loading application modules")

_config = types.ModuleType("webapp.config")
_config.__file__ = str(PROJECT_ROOT / "webapp" / "config.py")
_config_source = Path(_config.__file__).read_text(encoding="utf-8")
_base_assignment = "BASE_DIR = Path(__file__).resolve().parent.parent"
if _base_assignment not in _config_source:
    raise RuntimeError("Configuration layout changed; refusing to load production data")
_config_source = _config_source.replace(
    _base_assignment,
    f"BASE_DIR = Path({_temporary_data.name!r})",
)
exec(compile(_config_source, _config.__file__, "exec"), _config.__dict__)
_config.TEMPLATES_DIR = PROJECT_ROOT / "webapp" / "templates"
_config.STATIC_DIR = PROJECT_ROOT / "webapp" / "static"
sys.modules["webapp.config"] = _config

# api.logger otherwise writes to the production project's data/chaoxing.log.
_test_logger = types.ModuleType("api.logger")
_test_logger.logger = logger
sys.modules["api.logger"] = _test_logger

from webapp.db import Base, async_engine, init_db, sync_engine  # noqa: E402
from webapp.server import app  # noqa: E402
from webapp.services.progress_bus import progress_bus  # noqa: E402


class IsolatedWebTestCase(unittest.IsolatedAsyncioTestCase):
    """A fresh temporary database and cookie-aware ASGI client per test."""

    async def asyncSetUp(self):
        import asyncio

        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await init_db()
        progress_bus.attach_loop(asyncio.get_running_loop())
        with progress_bus._lock:
            progress_bus._subscribers.clear()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        await async_engine.dispose()
        sync_engine.dispose()

    async def login_admin(self):
        response = await self.client.post(
            "/api/admin/login", json={"password": "admin123"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response
