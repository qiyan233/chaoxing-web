# -*- coding: utf-8 -*-
"""刷课任务管理路由"""
import json
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from webapp.deps import get_db_session, require_login
from webapp.models.account import ChaoxingAccount
from webapp.models.task import StudyTask, TaskLog, TaskStatus
from webapp.schemas.task import TaskCreate, TaskLogResponse, TaskResponse
from webapp.services.task_runner import task_runner

router = APIRouter(prefix="/api/tasks", tags=["tasks"], dependencies=[Depends(require_login)])


def _is_admin(request: Request) -> bool:
    return request.session.get("role") == "admin" or request.session.get("user") == "admin"


def _current_user_id(request: Request) -> int | None:
    value = request.session.get("user_id")
    return int(value) if value is not None else None


async def _ensure_task_access(request: Request, task: StudyTask, db: AsyncSession) -> None:
    if _is_admin(request):
        return
    account = await db.get(ChaoxingAccount, task.account_id)
    if account is None or account.user_id != _current_user_id(request):
        raise HTTPException(status_code=403, detail="无权访问该任务")


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreate,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    # 校验账号
    account = await db.get(ChaoxingAccount, payload.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    if not _is_admin(request) and account.user_id != _current_user_id(request):
        raise HTTPException(status_code=403, detail="无权使用该学习通账号创建任务")

    # 检查该账号是否已有 running 任务
    existing = await db.execute(
        select(StudyTask).where(
            StudyTask.account_id == payload.account_id,
            StudyTask.status.in_([TaskStatus.PENDING.value, TaskStatus.RUNNING.value]),
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail="该账号已有正在运行的任务")

    task = StudyTask(
        account_id=payload.account_id,
        course_ids=json.dumps(payload.course_ids) if payload.course_ids else None,
        speed=payload.speed,
        jobs=payload.jobs,
        notopen_action=payload.notopen_action,
        mode=payload.mode,
        status=TaskStatus.PENDING.value,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    # 提交到调度器
    task_runner.submit(task.id)
    return task


@router.get("", response_model=List[TaskResponse])
async def list_tasks(
    request: Request,
    account_id: int | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db_session),
):
    stmt = select(StudyTask).order_by(desc(StudyTask.id)).limit(limit)
    if account_id is not None:
        stmt = stmt.where(StudyTask.account_id == account_id)
    if not _is_admin(request):
        account_stmt = select(ChaoxingAccount.id).where(
            ChaoxingAccount.user_id == _current_user_id(request)
        )
        stmt = stmt.where(StudyTask.account_id.in_(account_stmt))
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    task = await db.get(StudyTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    await _ensure_task_access(request, task, db)
    return task


@router.get("/{task_id}/logs", response_model=List[TaskLogResponse])
async def list_task_logs(
    task_id: int,
    request: Request,
    limit: int = 200,
    after_id: int = 0,
    db: AsyncSession = Depends(get_db_session),
):
    task = await db.get(StudyTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    await _ensure_task_access(request, task, db)
    stmt = (
        select(TaskLog)
        .where(TaskLog.task_id == task_id, TaskLog.id > after_id)
        .order_by(TaskLog.id.asc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    task = await db.get(StudyTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    await _ensure_task_access(request, task, db)

    if task.status not in (TaskStatus.PENDING.value, TaskStatus.RUNNING.value):
        return {"status": False, "msg": f"任务已是 {task.status} 状态，无法取消"}

    cancelled, msg = task_runner.cancel_or_finalize_missing(task_id)
    return {
        "status": cancelled,
        "msg": msg,
    }
