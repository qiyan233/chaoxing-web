# -*- coding: utf-8 -*-
"""刷课任务管理路由"""
import json
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from webapp.deps import get_db_session, require_login
from webapp.models.account import ChaoxingAccount
from webapp.models.task import StudyTask, TaskLog, TaskStatus
from webapp.schemas.task import TaskCreate, TaskLogResponse, TaskResponse
from webapp.services.task_runner import task_runner

router = APIRouter(prefix="/api/tasks", tags=["tasks"], dependencies=[Depends(require_login)])


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreate,
    db: AsyncSession = Depends(get_db_session),
):
    # 校验账号
    account = await db.get(ChaoxingAccount, payload.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")

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
    account_id: int | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db_session),
):
    stmt = select(StudyTask).order_by(desc(StudyTask.id)).limit(limit)
    if account_id is not None:
        stmt = stmt.where(StudyTask.account_id == account_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    task = await db.get(StudyTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.get("/{task_id}/logs", response_model=List[TaskLogResponse])
async def list_task_logs(
    task_id: int,
    limit: int = 200,
    after_id: int = 0,
    db: AsyncSession = Depends(get_db_session),
):
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
    db: AsyncSession = Depends(get_db_session),
):
    task = await db.get(StudyTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task.status not in (TaskStatus.PENDING.value, TaskStatus.RUNNING.value):
        return {"status": False, "msg": f"任务已是 {task.status} 状态，无法取消"}

    cancelled, msg = task_runner.cancel_or_finalize_missing(task_id)
    return {
        "status": cancelled,
        "msg": msg,
    }
