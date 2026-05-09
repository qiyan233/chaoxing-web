# -*- coding: utf-8 -*-
"""刷课任务执行器

职责：
- 接收启动请求 → 数据库登记 → 提交到 APScheduler
- 每个任务一个独立线程，绑定 SessionContext
- 进度事件推 ProgressBus
- 日志写入 task_logs 表
- 支持取消（threading.Event）
"""
from __future__ import annotations

import json
import random
import threading
import time
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.executors.pool import ThreadPoolExecutor
from loguru import logger
from sqlalchemy import select

from api.answer import Tiku
from api.base import Account, Chaoxing
from api.session_context import (
    CancelledError,
    IsolatedSession,
    SessionContext,
)
from webapp.db import SyncSessionLocal
from webapp.models.account import ChaoxingAccount
from webapp.models.task import StudyTask, TaskLog, TaskMode, TaskStatus
from webapp.models.settings import AppSetting
from webapp.services.cookies_provider import DBCookiesProvider
from webapp.services.credential import decrypt_password
from webapp.services.progress_bus import progress_bus
from webapp.services.study_flow import ChapterResult, process_chapter
from webapp.services.quick_flow import quick_process_chapter


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


class TaskRunner:
    """全局任务执行器（单例）"""

    def __init__(self):
        self._scheduler: Optional[BackgroundScheduler] = None
        self._cancel_events: Dict[int, threading.Event] = {}
        self._lock = threading.Lock()

    # ---------- 生命周期 ----------
    def start(self):
        if self._scheduler is not None:
            return
        self._recover_orphaned_tasks()
        # ThreadPoolExecutor 大小决定同时跑几个账号
        from webapp.config import MAX_CONCURRENT_ACCOUNTS

        self._scheduler = BackgroundScheduler(
            executors={"default": ThreadPoolExecutor(max_workers=MAX_CONCURRENT_ACCOUNTS)},
            job_defaults={"misfire_grace_time": 60, "coalesce": False},
        )
        self._scheduler.start()
        logger.info("TaskRunner 启动 (max_concurrent={})", MAX_CONCURRENT_ACCOUNTS)

    def shutdown(self):
        if self._scheduler is None:
            return
        # 通知所有正在跑的任务取消
        with self._lock:
            for evt in self._cancel_events.values():
                evt.set()
        try:
            self._scheduler.shutdown(wait=False)
        except Exception:
            pass
        self._scheduler = None
        logger.info("TaskRunner 已关闭")

    # ---------- 提交 / 取消 ----------
    def submit(self, task_id: int) -> None:
        """把已经入库的任务提交到调度器"""
        if self._scheduler is None:
            raise RuntimeError("TaskRunner 未启动")

        cancel_event = threading.Event()
        with self._lock:
            self._cancel_events[task_id] = cancel_event

        self._scheduler.add_job(
            self._run_task,
            args=[task_id, cancel_event],
            id=f"task-{task_id}",
            replace_existing=True,
        )

    def cancel(self, task_id: int) -> bool:
        """请求取消任务"""
        with self._lock:
            evt = self._cancel_events.get(task_id)
        if evt is None:
            return False
        evt.set()
        return True

    def cancel_or_finalize_missing(self, task_id: int) -> tuple[bool, str]:
        """取消任务；若任务只残留在数据库中，则直接标记为 cancelled。"""
        if self.cancel(task_id):
            return True, "已发送取消信号"

        with SyncSessionLocal() as db:
            task = db.get(StudyTask, task_id)
            if task is None:
                return False, "任务不存在"
            if task.status not in (TaskStatus.PENDING.value, TaskStatus.RUNNING.value):
                return False, f"任务已是 {task.status} 状态，无法取消"

            task.status = TaskStatus.CANCELLED.value
            task.finished_at = datetime.utcnow()
            task.error_message = "服务重启后恢复：任务未在调度器中，已标记为取消"
            db.add(
                TaskLog(
                    task_id=task.id,
                    level="warning",
                    message="服务重启后恢复：任务未在调度器中，已标记为取消",
                )
            )

            account = db.get(ChaoxingAccount, task.account_id)
            if account and account.status == "running":
                account.status = "idle"
            db.commit()

        self._publish(
            task_id,
            {
                "type": "task_cancelled",
                "ts": time.time(),
                "message": "任务未在调度器中，已标记为取消",
            },
        )
        return True, "任务未在调度器中，已标记为取消"

    def is_running(self, task_id: int) -> bool:
        with self._lock:
            return task_id in self._cancel_events

    def _recover_orphaned_tasks(self) -> None:
        """服务启动时恢复上次进程退出遗留的 pending/running 任务。"""
        recovered = 0
        with SyncSessionLocal() as db:
            tasks = db.execute(
                select(StudyTask).where(
                    StudyTask.status.in_(
                        [TaskStatus.PENDING.value, TaskStatus.RUNNING.value]
                    )
                )
            ).scalars().all()

            for task in tasks:
                task.status = TaskStatus.CANCELLED.value
                task.finished_at = datetime.utcnow()
                task.error_message = "服务启动恢复：上次运行被中断，任务已自动取消"
                task.current_chapter = None
                db.add(
                    TaskLog(
                        task_id=task.id,
                        level="warning",
                        message="服务启动恢复：上次运行被中断，任务已自动取消",
                    )
                )
                recovered += 1

            accounts = db.execute(
                select(ChaoxingAccount).where(ChaoxingAccount.status == "running")
            ).scalars().all()
            for account in accounts:
                account.status = "idle"

            if recovered or accounts:
                db.commit()

        if recovered:
            logger.warning("已恢复 {} 个上次中断遗留的任务", recovered)

    # ---------- 实际执行 ----------
    def _run_task(self, task_id: int, cancel_event: threading.Event):
        """后台线程入口"""
        try:
            with SyncSessionLocal() as db:
                task = db.get(StudyTask, task_id)
                if task is None:
                    return
                account = db.get(ChaoxingAccount, task.account_id)
                if account is None:
                    self._mark_failed(db, task, "账号不存在")
                    return

                # 标记 running
                task.status = TaskStatus.RUNNING.value
                task.started_at = datetime.utcnow()
                task.error_message = None
                account.status = "running"
                db.commit()

                # 解密密码
                try:
                    password = decrypt_password(account.password_enc)
                except ValueError as exc:
                    self._mark_failed(db, task, f"密码解密失败: {exc}")
                    account.status = "error"
                    db.commit()
                    return

                course_ids: Optional[List[str]] = None
                if task.course_ids:
                    try:
                        course_ids = json.loads(task.course_ids)
                    except json.JSONDecodeError:
                        course_ids = None

                speed = _as_float(task.speed, 1.0)
                jobs = _as_int(task.jobs, 4)
                notopen_action = task.notopen_action
                mode = task.mode or TaskMode.NORMAL.value
                tiku_config = self._load_tiku_config(db)
                proxy_config = self._load_proxy_config(db)

            # ----- 离开 DB session 后跑业务，避免长事务 -----
            self._publish(task_id, {"type": "task_started", "ts": time.time()})
            self._log(task_id, "info", f"任务启动 (账号={account.phone}, 模式={mode})")

            try:
                self._execute_study(
                    task_id=task_id,
                    account_id=account.id,
                    phone=account.phone,
                    password=password,
                    course_ids=course_ids,
                    speed=speed,
                    jobs=jobs,
                    notopen_action=notopen_action,
                    mode=mode,
                    tiku_config=tiku_config,
                    proxy_config=proxy_config,
                    cancel_event=cancel_event,
                )
            except CancelledError:
                self._finalize(task_id, account.id, TaskStatus.CANCELLED, "任务已取消")
                return
            except Exception as exc:
                tb = traceback.format_exc()
                logger.error("任务执行异常: {}", tb)
                self._finalize(
                    task_id,
                    account.id,
                    TaskStatus.FAILED,
                    f"执行异常: {exc}",
                )
                return

            self._finalize(task_id, account.id, TaskStatus.COMPLETED, None)
        finally:
            with self._lock:
                self._cancel_events.pop(task_id, None)

    # ---------- 实际刷课逻辑 ----------
    def _execute_study(
        self,
        *,
        task_id: int,
        account_id: int,
        phone: str,
        password: str,
        course_ids: Optional[List[str]],
        speed: float,
        jobs: int,
        notopen_action: str,
        mode: str,
        tiku_config: Dict[str, Any],
        proxy_config: Dict[str, Any],
        cancel_event: threading.Event,
    ):
        """执行 Web 端学习流程：登录 → 拉课程 → 章节循环。"""
        # 选择章节处理函数
        chapter_handler = (
            quick_process_chapter if mode == TaskMode.QUICK.value else process_chapter
        )
        # 构建 Tiku
        tiku = Tiku()
        tiku.config_set(tiku_config)
        tiku = tiku.get_tiku_from_config()
        tiku.init_tiku()

        cx_account = Account(phone, password)
        chaoxing = Chaoxing(
            account=cx_account,
            tiku=tiku,
            query_delay=_as_float(tiku_config.get("delay"), 0.0),
        )

        cookies_provider = DBCookiesProvider(account_id)
        holder = IsolatedSession(cookies_provider=cookies_provider)
        proxy = self._choose_proxy(proxy_config, task_id)
        if proxy:
            holder.session.proxies.update({"http": proxy, "https": proxy})
            self._log(task_id, "info", f"已启用代理: {self._mask_proxy(proxy)}")

        def progress_callback(event: dict):
            self._publish(task_id, {**event, "ts": time.time()})

        with SessionContext.scope(
            holder,
            cookies_provider=cookies_provider,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        ):
            # 登录
            login_result = chaoxing.login(login_with_cookies=True)
            if not login_result["status"]:
                login_result = chaoxing.login(login_with_cookies=False)
                if not login_result["status"]:
                    raise RuntimeError(f"登录失败: {login_result.get('msg')}")
            holder.save_cookies()
            self._log(task_id, "info", "登录成功")
            self._publish(task_id, {"type": "logged_in", "ts": time.time()})

            # 课程列表
            all_courses = chaoxing.get_course_list()
            if course_ids:
                course_set = set(course_ids)
                courses = [c for c in all_courses if c.get("courseId") in course_set]
            else:
                courses = all_courses

            self._log(task_id, "info", f"待处理课程: {len(courses)}")
            self._publish(task_id, {"type": "courses_loaded", "count": len(courses)})

            # 统计章节总数
            total_chapters = 0
            course_points: List[tuple] = []
            for course in courses:
                if cancel_event.is_set():
                    raise CancelledError("任务已取消")
                points = chaoxing.get_course_point(
                    course["courseId"], course["clazzId"], course["cpi"]
                )
                course_points.append((course, points["points"]))
                total_chapters += len(points["points"])

            self._update_total(task_id, total_chapters)
            self._publish(
                task_id, {"type": "total_calculated", "total_chapters": total_chapters}
            )

            # 章节循环
            done = 0
            for course, points in course_points:
                if cancel_event.is_set():
                    raise CancelledError("任务已取消")

                course_title = course.get("title", "?")
                self._log(task_id, "info", f"开始课程: {course_title}")
                self._publish(
                    task_id,
                    {"type": "course_started", "course_title": course_title},
                )
                self._update_current(task_id, course=course_title, chapter=None)

                for point in points:
                    if cancel_event.is_set():
                        raise CancelledError("任务已取消")

                    chapter_title = point.get("title", "?")
                    self._update_current(task_id, course=course_title, chapter=chapter_title)
                    self._log(task_id, "info", f"章节: {chapter_title}")
                    self._publish(
                        task_id,
                        {
                            "type": "chapter_started",
                            "course_title": course_title,
                            "chapter_title": chapter_title,
                            "done": done,
                            "total": total_chapters,
                        },
                    )

                    try:
                        result = chapter_handler(chaoxing, course, point, speed)
                    except CancelledError:
                        raise
                    except Exception as exc:
                        logger.exception("章节 {} 出错", chapter_title)
                        self._log(task_id, "error", f"章节 {chapter_title} 出错: {exc}")
                        result = ChapterResult.ERROR

                    done += 1
                    self._update_done(task_id, done)

                    status_str = "ok"
                    if result == ChapterResult.NOT_OPEN:
                        status_str = "not_open"
                        if notopen_action == "continue":
                            self._log(task_id, "warning", f"章节未开放: {chapter_title}, 跳过")
                        else:
                            self._log(task_id, "warning", f"章节未开放: {chapter_title}")
                    elif result == ChapterResult.ERROR:
                        status_str = "error"
                        self._log(task_id, "error", f"章节失败: {chapter_title}")
                    else:
                        self._log(task_id, "info", f"章节完成: {chapter_title}")

                    self._publish(
                        task_id,
                        {
                            "type": "chapter_finished",
                            "course_title": course_title,
                            "chapter_title": chapter_title,
                            "result": status_str,
                            "done": done,
                            "total": total_chapters,
                        },
                    )

                self._publish(
                    task_id,
                    {"type": "course_finished", "course_title": course_title},
                )

    # ---------- 内部辅助 ----------
    def _publish(self, task_id: int, event: dict):
        progress_bus.publish(task_id, event)

    def _log(self, task_id: int, level: str, message: str):
        logger.log(level.upper(), f"[task-{task_id}] {message}")
        try:
            with SyncSessionLocal() as db:
                db.add(TaskLog(task_id=task_id, level=level, message=message))
                db.commit()
        except Exception:
            pass
        # 同时推到 SSE
        self._publish(task_id, {"type": "log", "level": level, "message": message})

    def _update_total(self, task_id: int, total: int):
        with SyncSessionLocal() as db:
            task = db.get(StudyTask, task_id)
            if task:
                task.total_chapters = total
                db.commit()

    def _update_done(self, task_id: int, done: int):
        with SyncSessionLocal() as db:
            task = db.get(StudyTask, task_id)
            if task:
                task.done_chapters = done
                db.commit()

    def _update_current(self, task_id: int, *, course: Optional[str], chapter: Optional[str]):
        with SyncSessionLocal() as db:
            task = db.get(StudyTask, task_id)
            if task:
                if course is not None:
                    task.current_course = course
                task.current_chapter = chapter
                db.commit()

    def _mark_failed(self, db, task: StudyTask, message: str):
        task.status = TaskStatus.FAILED.value
        task.finished_at = datetime.utcnow()
        task.error_message = message
        db.commit()

    def _finalize(
        self,
        task_id: int,
        account_id: int,
        status: TaskStatus,
        message: Optional[str],
    ):
        with SyncSessionLocal() as db:
            task = db.get(StudyTask, task_id)
            if task:
                task.status = status.value
                task.finished_at = datetime.utcnow()
                if message:
                    task.error_message = message
                db.commit()

            account = db.get(ChaoxingAccount, account_id)
            if account:
                account.status = "error" if status == TaskStatus.FAILED else "idle"
                if status == TaskStatus.FAILED and message:
                    account.last_error = message
                db.commit()

        self._log(
            task_id,
            "info" if status == TaskStatus.COMPLETED else "warning",
            f"任务结束: {status.value} {message or ''}",
        )

        event_type = {
            TaskStatus.COMPLETED: "task_done",
            TaskStatus.FAILED: "task_failed",
            TaskStatus.CANCELLED: "task_cancelled",
        }.get(status, "task_done")

        self._publish(task_id, {"type": event_type, "ts": time.time(), "message": message or ""})

    def _load_tiku_config(self, db) -> Dict[str, Any]:
        """从 app_settings 加载题库配置"""
        setting = db.get(AppSetting, AppSetting.KEY_TIKU_CONFIG)
        if setting and setting.value:
            try:
                return json.loads(setting.value)
            except json.JSONDecodeError:
                return {}
        return {}

    def _load_proxy_config(self, db) -> Dict[str, Any]:
        """从 app_settings 加载代理池配置"""
        setting = db.get(AppSetting, AppSetting.KEY_PROXY_CONFIG)
        if setting and setting.value:
            try:
                return json.loads(setting.value)
            except json.JSONDecodeError:
                return {}
        return {}

    @staticmethod
    def _parse_proxy_lines(proxy_text: str) -> list[str]:
        proxies = []
        for line in (proxy_text or "").splitlines():
            proxy = line.strip()
            if not proxy or proxy.startswith("#"):
                continue
            proxies.append(proxy)
        return proxies

    def _choose_proxy(self, proxy_config: Dict[str, Any], task_id: int) -> Optional[str]:
        if not proxy_config or not proxy_config.get("enabled"):
            return None
        proxies = self._parse_proxy_lines(str(proxy_config.get("proxies") or ""))
        if not proxies:
            return None
        strategy = proxy_config.get("strategy") or "random"
        if strategy == "round_robin":
            return proxies[task_id % len(proxies)]
        return random.choice(proxies)

    @staticmethod
    def _mask_proxy(proxy: str) -> str:
        if "@" not in proxy:
            return proxy
        scheme, rest = proxy.split("://", 1) if "://" in proxy else ("", proxy)
        host = rest.rsplit("@", 1)[-1]
        return f"{scheme + '://' if scheme else ''}***:***@{host}"


# 全局单例
task_runner = TaskRunner()
