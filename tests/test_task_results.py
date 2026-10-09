"""Persisted task outcomes with local mocks; no remote study requests."""
import json
import threading
from contextlib import ExitStack, nullcontext
from unittest.mock import MagicMock, patch

from tests.support import IsolatedWebTestCase
from api.session_context import CancelledError
from webapp.db import SyncSessionLocal
from webapp.models.account import ChaoxingAccount
from webapp.models.task import StudyTask
from webapp.services.study_flow import ChapterResult
from webapp.services.task_runner import TaskRunner


class TaskResultTests(IsolatedWebTestCase):
    def run_study(self, results, *, courses=None, selected=None, mode="normal"):
        courses = courses if courses is not None else [
            {"courseId": "course-1", "clazzId": "class-1", "cpi": "cpi-1", "title": "Course"}
        ]
        with SyncSessionLocal() as db:
            account = ChaoxingAccount(phone="13800000000", password_enc=b"test")
            db.add(account)
            db.flush()
            task = StudyTask(account_id=account.id, mode=mode,
                             course_ids=json.dumps(selected) if selected is not None else None)
            db.add(task)
            db.commit()
            task_id = task.id

        runner = TaskRunner()
        cancelled = threading.Event()
        runner._cancel_events[task_id] = cancelled
        fake_cx = MagicMock()
        fake_cx.login.return_value = {"status": True}
        fake_cx.get_course_list.return_value = courses
        fake_cx.get_course_point.return_value = {
            "points": [{"title": f"Chapter {n}"} for n in range(len(results))]
        }
        with ExitStack() as stack:
            prefix = "webapp.services.task_runner."
            stack.enter_context(patch(prefix + "Tiku"))
            stack.enter_context(patch(prefix + "Chaoxing", return_value=fake_cx))
            stack.enter_context(patch(prefix + "IsolatedSession"))
            stack.enter_context(patch(prefix + "SessionContext.scope", return_value=nullcontext()))
            stack.enter_context(patch(prefix + "decrypt_password", return_value="test"))
            stack.enter_context(patch.object(runner, "_load_proxy_config", return_value={}))
            stack.enter_context(patch.object(runner, "_choose_proxy", return_value=None))
            stack.enter_context(patch.object(runner, "_log"))
            stack.enter_context(patch(prefix + "logger"))
            publisher = stack.enter_context(patch.object(runner, "_publish"))
            handler = "quick_process_chapter" if mode == "quick" else "process_chapter"
            stack.enter_context(patch(prefix + handler, side_effect=results))
            runner._run_task(task_id, cancelled)
            events = [call.args[1] for call in publisher.call_args_list]

        with SyncSessionLocal() as db:
            saved = db.get(StudyTask, task_id)
            db.expunge(saved)
        self.assertNotIn(task_id, runner._cancel_events)
        return saved, events

    async def test_mixed_results_are_partial_and_counts_are_persisted(self):
        task, events = self.run_study([
            ChapterResult.SUCCESS, ChapterResult.ERROR, ChapterResult.NOT_OPEN
        ])
        self.assertEqual(task.status, "partial")
        self.assertEqual((task.done_chapters, task.succeeded_chapters,
                          task.failed_chapters, task.skipped_chapters), (3, 1, 1, 1))
        self.assertIn("失败 1", task.error_message)
        self.assertEqual(events[-1]["type"], "task_partial")
        self.assertEqual(events[-1]["failed_chapters"], 1)
        chapters = [e for e in events if e["type"] == "chapter_finished"]
        self.assertEqual([e["result"] for e in chapters], ["ok", "error", "not_open"])
        self.assertEqual(chapters[-1]["skipped_chapters"], 1)

    async def test_success_is_completed_and_exposed_by_task_api(self):
        task, events = self.run_study([ChapterResult.SUCCESS], mode="quick")
        self.assertEqual(task.status, "completed")
        self.assertEqual(task.succeeded_chapters, 1)
        self.assertEqual(task.failed_chapters, 0)
        self.assertEqual(events[-1]["type"], "task_done")
        await self.login_admin()
        response = await self.client.get(f"/api/tasks/{task.id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["succeeded_chapters"], 1)

    async def test_unknown_and_pending_results_are_failures(self):
        task, events = self.run_study([None, ChapterResult.PENDING])
        self.assertEqual(task.status, "partial")
        self.assertEqual(task.failed_chapters, 2)
        self.assertEqual(task.succeeded_chapters, 0)
        self.assertEqual([e["result"] for e in events if e["type"] == "chapter_finished"],
                         ["error", "error"])

    async def test_chapter_exception_does_not_become_completed(self):
        task, _ = self.run_study([RuntimeError("local chapter error")])
        self.assertEqual(task.status, "partial")
        self.assertEqual(task.failed_chapters, 1)

    async def test_cancel_preserves_processed_counts(self):
        task, events = self.run_study([ChapterResult.SUCCESS, CancelledError("cancel")])
        self.assertEqual(task.status, "cancelled")
        self.assertEqual(task.done_chapters, 1)
        self.assertEqual(task.succeeded_chapters, 1)
        self.assertEqual(events[-1]["succeeded_chapters"], 1)

    async def test_no_courses_is_failed(self):
        task, events = self.run_study([], courses=[])
        self.assertEqual(task.status, "failed")
        self.assertIn("课程", task.error_message)
        self.assertEqual(events[-1]["type"], "task_failed")

    async def test_unmatched_selection_is_failed(self):
        task, _ = self.run_study([], selected=["missing-course"])
        self.assertEqual(task.status, "failed")
        self.assertIn("课程", task.error_message)

    async def test_partially_unmatched_selection_is_failed(self):
        task, _ = self.run_study([ChapterResult.SUCCESS], selected=["course-1", "missing-course"])
        self.assertEqual(task.status, "failed")
        self.assertIn("missing-course", task.error_message)
        self.assertEqual(task.done_chapters, 0)

    async def test_all_not_open_is_partial(self):
        task, events = self.run_study([ChapterResult.NOT_OPEN])
        self.assertEqual(task.status, "partial")
        self.assertEqual(task.skipped_chapters, 1)
        self.assertEqual(task.succeeded_chapters, 0)
        self.assertEqual(events[-1]["type"], "task_partial")

    async def test_courses_without_chapters_are_failed(self):
        task, _ = self.run_study([])
        self.assertEqual(task.status, "failed")
        self.assertIn("章节", task.error_message)
