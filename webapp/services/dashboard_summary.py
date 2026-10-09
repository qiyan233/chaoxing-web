"""Read-only dashboard aggregates; dates use Asia/Hong_Kong (UTC+8)."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from webapp.models.account import ChaoxingAccount
from webapp.models.proxy import ProxyEntry
from webapp.models.task import StudyTask, TaskLog


async def dashboard_summary(db: AsyncSession) -> dict:
    today = datetime.now(timezone(timedelta(hours=8))).date()
    status_rows = (await db.execute(
        select(StudyTask.status, func.count()).group_by(StudyTask.status)
    )).all()
    statuses = dict(status_rows)
    account_rows = (await db.execute(
        select(ChaoxingAccount.status, func.count()).group_by(ChaoxingAccount.status)
    )).all()
    account_statuses = dict(account_rows)
    proxy_total = await db.scalar(select(func.count(ProxyEntry.id))) or 0
    proxy_active = await db.scalar(select(func.count(ProxyEntry.id)).where(ProxyEntry.status == "active")) or 0
    completed_date = func.date(StudyTask.finished_at, "+8 hours")
    created_date = func.date(StudyTask.created_at, "+8 hours")
    completed = dict((await db.execute(
        select(completed_date, func.count()).where(
            StudyTask.status == "completed", completed_date >= str(today.replace(day=1))
        ).group_by(completed_date)
    )).all())
    # Include the previous month when the 30-day chart crosses its boundary.
    chart_start = today - timedelta(days=29)
    chart_completed = dict((await db.execute(
        select(completed_date, func.count()).where(
            StudyTask.status == "completed", completed_date >= str(chart_start)
        ).group_by(completed_date)
    )).all())
    created = dict((await db.execute(
        select(created_date, func.count()).where(created_date >= str(chart_start)).group_by(created_date)
    )).all())
    trend = []
    for offset in range(30):
        day = str(chart_start + timedelta(days=offset))
        trend.append({"date": day, "created": created.get(day, 0), "completed": chart_completed.get(day, 0)})

    task_query = select(StudyTask, ChaoxingAccount.nickname, ChaoxingAccount.phone).outerjoin(
        ChaoxingAccount, ChaoxingAccount.id == StudyTask.account_id
    )
    tasks = (await db.execute(task_query.order_by(StudyTask.id.desc()).limit(20))).all()
    active = (await db.execute(task_query.where(StudyTask.status.in_(["running", "pending"]))
        .order_by(case((StudyTask.status == "running", 0), else_=1), StudyTask.id).limit(4))).all()
    queued = (await db.execute(task_query.where(StudyTask.status == "pending")
        .order_by(StudyTask.id).limit(5))).all()

    def task_data(rows):
        return [{
            "id": task.id, "account_id": task.account_id, "account_name": nickname or phone or "账号已移除",
            "status": task.status, "current_course": task.current_course, "current_chapter": task.current_chapter,
            "done_chapters": task.done_chapters, "total_chapters": task.total_chapters,
            "succeeded_chapters": task.succeeded_chapters, "failed_chapters": task.failed_chapters,
            "skipped_chapters": task.skipped_chapters,
            "updated_at": (task.finished_at or task.started_at or task.created_at).isoformat() + "Z",
        } for task, nickname, phone in rows]
    log_rows = (await db.execute(
        select(TaskLog, ChaoxingAccount.nickname, ChaoxingAccount.phone)
        .join(StudyTask, StudyTask.id == TaskLog.task_id)
        .outerjoin(ChaoxingAccount, ChaoxingAccount.id == StudyTask.account_id)
        .order_by(TaskLog.id.desc()).limit(8)
    )).all()
    logs = [{"id": log.id, "task_id": log.task_id, "level": log.level, "message": log.message,
             "account_name": nickname or phone or "账号已移除", "created_at": log.created_at.isoformat() + "Z"}
            for log, nickname, phone in log_rows]
    total_accounts = sum(account_statuses.values())
    return {
        "date": str(today), "accounts": total_accounts,
        "active_accounts": sum(account_statuses.get(s, 0) for s in ("idle", "running")),
        "running": statuses.get("running", 0), "pending": statuses.get("pending", 0),
        "completed": statuses.get("completed", 0), "today_completed": completed.get(str(today), 0),
        "month_completed": sum(n for day, n in completed.items() if day <= str(today)),
        "total_tasks": sum(statuses.values()),
        "failed": statuses.get("failed", 0) + statuses.get("partial", 0),
        "proxy_total": proxy_total, "proxy_active": proxy_active,
        "trend": trend, "recent_tasks": task_data(tasks), "active_tasks": task_data(active),
        "queue_tasks": task_data(queued), "logs": logs,
    }
