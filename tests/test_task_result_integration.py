"""Regression coverage for legacy migrations and task-result presentation."""
from tests.support import IsolatedWebTestCase, PROJECT_ROOT

import shutil
import subprocess
import unittest
import asyncio
import json
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from webapp.db import _run_lightweight_migrations
from webapp.db import AsyncSessionLocal
from webapp.models.account import ChaoxingAccount
from webapp.models.task import StudyTask
from webapp.routers.pages import _landing_stats
from webapp.services.progress_bus import ProgressBus


class LegacyTaskResultMigrationTests(unittest.TestCase):
    def test_adds_zeroed_counters_without_inventing_historical_success(self):
        engine = create_engine("sqlite://")
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE study_tasks (id INTEGER PRIMARY KEY, "
                    "done_chapters INTEGER, total_chapters INTEGER, status TEXT)"
                )
                connection.exec_driver_sql(
                    "CREATE TABLE chaoxing_accounts (id INTEGER PRIMARY KEY)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO study_tasks VALUES (1, 3, 3, 'completed')"
                )
                _run_lightweight_migrations(connection)
                columns = {
                    row[1]
                    for row in connection.exec_driver_sql("PRAGMA table_info(study_tasks)")
                }
                expected = {"succeeded_chapters", "failed_chapters", "skipped_chapters"}
                self.assertTrue(expected.issubset(columns), columns)
                row = connection.exec_driver_sql(
                    "SELECT done_chapters, succeeded_chapters, failed_chapters, "
                    "skipped_chapters, status FROM study_tasks WHERE id=1"
                ).one()
                self.assertEqual(tuple(row), (3, 0, 0, 0, "completed"))
                connection.exec_driver_sql(
                    "UPDATE study_tasks SET succeeded_chapters=1, failed_chapters=2"
                )
                _run_lightweight_migrations(connection)
                counts = connection.exec_driver_sql(
                    "SELECT succeeded_chapters, failed_chapters FROM study_tasks"
                ).one()
                self.assertEqual(tuple(counts), (1, 2))
        finally:
            engine.dispose()


class TaskResultFrontendTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required to exercise page event handlers")
    def test_partial_event_updates_counters_and_closes_stream(self):
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const context = {
  window: {}, document: {addEventListener() {}}, console, setTimeout,
};
// In the browser, window and the global scope are the same object.
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync('webapp/static/js/app.js', 'utf8'), context);
const page = fs.readFileSync('webapp/templates/task_detail.html', 'utf8');
const script = page.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
vm.runInContext(script, context);
const view = context.taskDetail(1);
view.autoScroll = false;
let closed = false;
view.evtSource = {close() {closed = true;}};
view.connected = true;
view.handleEvent({type: 'task_partial', status: 'partial', done: 3, total: 3,
  succeeded_chapters: 1, failed_chapters: 1, skipped_chapters: 1,
  message: 'one failure and one unopened chapter'});
assert.equal(view.task.status, 'partial');
assert.equal(view.task.succeeded_chapters, 1);
assert.equal(view.task.failed_chapters, 1);
assert.equal(view.task.skipped_chapters, 1);
assert.equal(view.task.done_chapters, 3);
assert.equal(closed, true);
assert.equal(view.connected, false);
assert.equal(view.task.error_message, 'one failure and one unopened chapter');
assert.match(context.window.taskResultSummary({done_chapters: 3}), /3.*未分类/);
assert.match(context.window.taskStatusLabel('partial'), /未全部完成/);
for (const type of ['task_done', 'task_failed', 'task_cancelled', 'task_replay_end']) {
  closed = false;
  view.connected = true;
  view.handleEvent({type, status: 'cancelled', done: 1, total: 3});
  assert.equal(closed, true, type);
  assert.equal(view.connected, false, type);
}
for (const [file, factory] of [
  ['tasks.html', 'tasksPage'], ['user_tasks.html', 'userTasksPage'],
  ['dashboard.html', 'dashboard'], ['user_dashboard.html', 'userDashboard'],
]) {
  const html = fs.readFileSync(`webapp/templates/${file}`, 'utf8');
  const source = html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
  vm.runInContext(source, context);
  const model = context[factory]();
  assert.equal(model.tagClass('partial'), 'tag-warn', file);
}
const dashboard = context.userDashboard();
dashboard.tasks = [
  {status: 'partial', done_chapters: 3, succeeded_chapters: 1, failed_chapters: 1, skipped_chapters: 1},
  {status: 'completed', done_chapters: 7},
];
assert.equal(dashboard.failedCount(), 1);
assert.match(context.window.taskResultSummary(dashboard.chapterResults()), /7.*未分类/);
"""
        result = subprocess.run(
            [shutil.which("node"), "-e", script], cwd=PROJECT_ROOT,
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class TaskResultAPITests(IsolatedWebTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.login_admin()
        async with AsyncSessionLocal() as db:
            account = ChaoxingAccount(phone="13800000003", password_enc=b"unused")
            db.add(account)
            await db.flush()
            historical = StudyTask(account_id=account.id, status="completed",
                                   total_chapters=7, done_chapters=7)
            partial = StudyTask(account_id=account.id, status="partial",
                                total_chapters=3, done_chapters=3,
                                succeeded_chapters=1, failed_chapters=1, skipped_chapters=1,
                                error_message="存在失败和未开放章节")
            running = StudyTask(account_id=account.id, status="running",
                                total_chapters=3, done_chapters=2, succeeded_chapters=2)
            db.add_all([historical, partial, running])
            await db.flush()
            self.partial_id, self.running_id = partial.id, running.id
            await db.commit()

    async def test_partial_detail_and_replay_include_classified_counts(self):
        detail = await self.client.get(f"/api/tasks/{self.partial_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["status"], "partial")
        self.assertEqual(detail.json()["succeeded_chapters"], 1)
        with patch("webapp.routers.stream.progress_bus.subscribe", new=AsyncMock()) as subscribe:
            response = await self.client.get(f"/api/tasks/{self.partial_id}/stream")
            self.assertEqual(response.status_code, 200)
            event = json.loads(response.text.removeprefix("data: ").strip())
            self.assertEqual(event["type"], "task_replay_end")
            self.assertEqual(event["status"], "partial")
            self.assertEqual(event["failed_chapters"], 1)
            self.assertEqual(event["skipped_chapters"], 1)
            self.assertEqual(event["done"], 3)
            subscribe.assert_not_awaited()

    async def test_active_stream_closes_on_partial_event(self):
        queue = asyncio.Queue()
        queue.put_nowait({"type": "task_partial", "status": "partial",
                          "done": 3, "total": 3, "succeeded_chapters": 2,
                          "failed_chapters": 1, "skipped_chapters": 0})
        with patch("webapp.routers.stream.progress_bus.subscribe", new=AsyncMock(return_value=queue)), \
             patch("webapp.routers.stream.progress_bus.unsubscribe", new=AsyncMock()) as unsubscribe:
            response = await asyncio.wait_for(
                self.client.get(f"/api/tasks/{self.running_id}/stream"), timeout=2
            )
            self.assertEqual(response.status_code, 200)
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            self.assertEqual([e["type"] for e in events], ["snapshot", "task_partial"])
            self.assertEqual(events[0]["succeeded_chapters"], 2)
            unsubscribe.assert_awaited_once()

    async def test_landing_counts_success_without_guessing_old_results(self):
        async with AsyncSessionLocal() as db:
            stats = await _landing_stats(db)
        self.assertEqual(stats["completed_chapters"], 3)
        self.assertEqual(stats["completed_tasks"], 1)

    async def test_progress_bus_stream_treats_partial_as_terminal(self):
        bus = ProgressBus()
        bus.attach_loop(asyncio.get_running_loop())
        stream = bus.stream(42)
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        bus.publish(42, {"type": "task_partial"})
        self.assertEqual(await asyncio.wait_for(pending, 1), {"type": "task_partial"})
        with self.assertRaises(StopAsyncIteration):
            await asyncio.wait_for(anext(stream), 1)
        self.assertNotIn(42, bus._subscribers)

    async def test_result_pages_render_after_schema_upgrade(self):
        for path in ("/dashboard", "/tasks", f"/tasks/{self.partial_id}"):
            with self.subTest(path=path):
                response = await self.client.get(path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn("window.taskStatusLabel", response.text)
                self.assertIn("window.taskResultSummary", response.text)


if __name__ == "__main__":
    unittest.main()
