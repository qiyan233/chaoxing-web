# -*- coding: utf-8 -*-
"""在线更新 API"""
from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool

from webapp.deps import require_admin
from webapp.schemas.update import RestartResult, UpdateApplyResult, UpdateStatus
from webapp.services.updater import updater

router = APIRouter(prefix="/api/update", tags=["update"], dependencies=[Depends(require_admin)])


@router.get("/status", response_model=UpdateStatus)
async def update_status():
    return await run_in_threadpool(updater.status, fetch=True)


@router.post("/apply", response_model=UpdateApplyResult)
async def apply_update():
    return await run_in_threadpool(updater.apply)


@router.post("/restart", response_model=RestartResult)
async def restart_service():
    updater.restart_later(delay_seconds=1.0)
    return RestartResult(status=True, msg="服务正在重启，请稍等几秒后刷新页面")
