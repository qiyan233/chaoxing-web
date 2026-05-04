# -*- coding: utf-8 -*-
"""SSE 实时事件流路由"""
import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from webapp.deps import get_db_session, require_login
from webapp.models.task import StudyTask, TaskStatus
from webapp.services.progress_bus import progress_bus

router = APIRouter(prefix="/api/tasks", tags=["stream"], dependencies=[Depends(require_login)])


def _sse_format(data: dict, event: str | None = None) -> str:
    """SSE 协议格式化"""
    payload = json.dumps(data, ensure_ascii=False)
    parts = []
    if event:
        parts.append(f"event: {event}")
    parts.append(f"data: {payload}")
    return "\n".join(parts) + "\n\n"


@router.get("/{task_id}/stream")
async def task_stream(
    task_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """订阅任务的实时进度事件流"""
    task = await db.get(StudyTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    # 已完成的任务不需要订阅
    if task.status in (
        TaskStatus.COMPLETED.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELLED.value,
    ):
        async def finished_stream() -> AsyncIterator[bytes]:
            yield _sse_format(
                {
                    "type": "task_replay_end",
                    "status": task.status,
                    "done": task.done_chapters,
                    "total": task.total_chapters,
                    "error": task.error_message,
                }
            ).encode("utf-8")
        return StreamingResponse(finished_stream(), media_type="text/event-stream")

    queue = await progress_bus.subscribe(task_id)

    async def event_generator() -> AsyncIterator[bytes]:
        # 首次发送当前状态快照
        yield _sse_format(
            {
                "type": "snapshot",
                "status": task.status,
                "done": task.done_chapters,
                "total": task.total_chapters,
                "current_course": task.current_course,
                "current_chapter": task.current_chapter,
            }
        ).encode("utf-8")

        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # 心跳保活
                    yield b": ping\n\n"
                    continue

                yield _sse_format(event).encode("utf-8")

                if event.get("type") in {"task_done", "task_failed", "task_cancelled"}:
                    break
        finally:
            await progress_bus.unsubscribe(task_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
