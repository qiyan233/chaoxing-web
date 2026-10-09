"""Regression tests for the user's mandatory serial execution policy."""
from tests.support import IsolatedWebTestCase, PROJECT_ROOT

import importlib.util
import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pydantic import ValidationError

from api.base import StudyResult
from api.session_context import CancelledError, SessionContext
from webapp.db import AsyncSessionLocal
from webapp.models.settings import AppSetting
from webapp.models.task import StudyTask
from webapp.schemas.settings import RuntimeConfig
from webapp.schemas.task import TaskCreate
from webapp.services import quick_flow, study_flow, task_runner


class SerialExecutionTests(unittest.TestCase):
    def test_scheduler_ignores_environment_and_legacy_setting(self):
        with patch("webapp.config.MAX_CONCURRENT_ACCOUNTS", 16), patch.object(
            task_runner, "SyncSessionLocal"
        ) as session:
            session.return_value.__enter__.return_value.get.return_value = SimpleNamespace(
                value=json.dumps({"max_concurrent_accounts": 16})
            )
            self.assertEqual(task_runner._load_max_concurrent_accounts(), 1)

    def test_two_tasks_execute_in_order_without_overlap(self):
        runner = task_runner.TaskRunner()
        first_started = threading.Event()
        release_first = threading.Event()
        second_finished = threading.Event()
        order = []

        def run(task_id, cancel_event):
            order.append((task_id, "start"))
            if task_id == 1:
                first_started.set()
                release_first.wait(5)
            order.append((task_id, "end"))
            if task_id == 2:
                second_finished.set()

        with patch.object(runner, "_recover_orphaned_tasks"), patch.object(
            runner, "_run_task", side_effect=run
        ), patch("webapp.config.MAX_CONCURRENT_ACCOUNTS", 16), patch.object(
            task_runner, "SyncSessionLocal"
        ) as session:
            session.return_value.__enter__.return_value.get.return_value = SimpleNamespace(
                value='{"max_concurrent_accounts":16}'
            )
            runner.start()
            try:
                runner.submit(1)
                self.assertTrue(first_started.wait(3))
                runner.submit(2)
                self.assertFalse(second_finished.wait(0.1), "second task overlapped first")
                release_first.set()
                self.assertTrue(second_finished.wait(3))
                self.assertEqual(order, [(1, "start"), (1, "end"), (2, "start"), (2, "end")])
                self.assertIsNone(runner._scheduler._job_defaults["misfire_grace_time"])
            finally:
                release_first.set()
                runner.shutdown()

    def test_task_and_runtime_parameters_allow_only_one(self):
        self.assertEqual(TaskCreate(account_id=1).jobs, 1)
        self.assertEqual(RuntimeConfig().max_concurrent_accounts, 1)
        self.assertEqual(StudyTask.__table__.c.jobs.default.arg, 1)
        for value in (0, 2, 16):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    TaskCreate(account_id=1, jobs=value)
                with self.assertRaises(ValidationError):
                    RuntimeConfig(max_concurrent_accounts=value)

    def test_normal_and_quick_jobs_share_thread_context_and_order(self):
        for module, method, processor in (
            (study_flow, "process_chapter", "process_job"),
            (quick_flow, "quick_process_chapter", "quick_process_job"),
        ):
            with self.subTest(mode=module.__name__):
                client = Mock()
                client.get_job_list.return_value = ([{"id": i} for i in range(4)], {})
                caller = threading.get_ident()
                seen = []
                active = 0
                maximum = 0
                lock = threading.Lock()
                cancel_event = threading.Event()
                holder = SimpleNamespace(_cookies_provider=None)

                def process(chaoxing, course, job, info, speed):
                    nonlocal active, maximum
                    with lock:
                        active += 1
                        maximum = max(maximum, active)
                        seen.append((job["id"], threading.get_ident(), SessionContext.get_session_holder()))
                    time.sleep(0.01)
                    with lock:
                        active -= 1
                    return StudyResult.SUCCESS

                with SessionContext.scope(holder, cancel_event=cancel_event), patch.object(
                    module, processor, side_effect=process
                ):
                    result = getattr(module, method)(client, {}, {"title": "test", "has_finished": False}, 1.0)
                self.assertEqual(result, study_flow.ChapterResult.SUCCESS)
                self.assertEqual(maximum, 1)
                self.assertEqual(seen, [(i, caller, holder) for i in range(4)])

    def test_cancel_stops_before_next_job_in_both_modes(self):
        for module, method, processor in (
            (study_flow, "process_chapter", "process_job"),
            (quick_flow, "quick_process_chapter", "quick_process_job"),
        ):
            with self.subTest(mode=module.__name__):
                client = Mock()
                client.get_job_list.return_value = ([{"id": 1}, {"id": 2}], {})
                event = threading.Event()

                def process(*args):
                    event.set()
                    return StudyResult.SUCCESS

                with SessionContext.scope(SimpleNamespace(_cookies_provider=None), cancel_event=event), patch.object(
                    module, processor, side_effect=process
                ) as process_mock:
                    with self.assertRaises(CancelledError):
                        getattr(module, method)(client, {}, {"title": "test", "has_finished": False}, 1.0)
                    self.assertEqual(process_mock.call_count, 1)

    def test_live_job_runs_in_current_session_thread(self):
        seen = []
        caller = threading.get_ident()
        with patch.object(study_flow, "Live"), patch.object(
            study_flow.LiveProcessor, "run_live", side_effect=lambda *args: seen.append(threading.get_ident())
        ):
            result = study_flow.process_job(Mock(), {"title": "test"}, {"type": "live", "jobid": "1"}, {}, 1.0)
        self.assertEqual(result, StudyResult.SUCCESS)
        self.assertEqual(seen, [caller])

    def test_serial_modes_preserve_failure_skip_and_finished_results(self):
        for module, method, processor in (
            (study_flow, "process_chapter", "process_job"),
            (quick_flow, "quick_process_chapter", "quick_process_job"),
        ):
            with self.subTest(mode=module.__name__):
                client = Mock()
                client.get_job_list.return_value = ([{"id": 1}, {"id": 2}], {})
                point = {"title": "test", "has_finished": False}
                with patch.object(module, processor, side_effect=[StudyResult.ERROR, StudyResult.SUCCESS]) as process:
                    self.assertEqual(getattr(module, method)(client, {}, point, 1.0), study_flow.ChapterResult.ERROR)
                    self.assertEqual(process.call_count, 2)
                client.get_job_list.return_value = ([], {"notOpen": True})
                with patch.object(module, processor) as process:
                    self.assertEqual(getattr(module, method)(client, {}, point, 1.0), study_flow.ChapterResult.NOT_OPEN)
                    self.assertEqual(getattr(module, method)(client, {}, {**point, "has_finished": True}, 1.0), study_flow.ChapterResult.SUCCESS)
                    process.assert_not_called()

    def test_both_launchers_reject_multiple_workers(self):
        for path in (PROJECT_ROOT / "start.py", PROJECT_ROOT / "webapp" / "run.py"):
            with self.subTest(path=path.name):
                spec = importlib.util.spec_from_file_location("serial_launcher", path)
                launcher = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(launcher)
                with patch("sys.argv", [str(path), "--workers", "2"]), patch("uvicorn.run") as run:
                    with self.assertRaises(SystemExit) as error:
                        launcher.main()
                    self.assertEqual(error.exception.code, 2)
                    run.assert_not_called()


class SerialRuntimeApiTests(IsolatedWebTestCase):
    async def test_legacy_runtime_read_reports_serial_and_rejects_concurrency(self):
        await self.login_admin()
        async with AsyncSessionLocal() as db:
            db.add(AppSetting(key=AppSetting.KEY_RUNTIME_CONFIG, value='{"max_concurrent_accounts":16}'))
            await db.commit()
        response = await self.client.get("/api/settings/runtime")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"max_concurrent_accounts": 1})
        rejected = await self.client.post("/api/settings/runtime", json={"max_concurrent_accounts": 2})
        self.assertEqual(rejected.status_code, 422)
        accepted = await self.client.post("/api/settings/runtime", json={"max_concurrent_accounts": 1})
        self.assertEqual(accepted.status_code, 200)

    async def test_task_api_rejects_parallel_jobs_before_submission(self):
        await self.login_admin()
        with patch.object(task_runner.task_runner, "submit") as submit:
            response = await self.client.post("/api/tasks", json={"account_id": 1, "jobs": 2})
        self.assertEqual(response.status_code, 422)
        submit.assert_not_called()
