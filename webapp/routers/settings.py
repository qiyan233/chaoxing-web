# -*- coding: utf-8 -*-
"""全局设置路由（题库 / 通知）"""
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from webapp.deps import get_db_session, require_admin
from webapp.models.settings import AppSetting
from webapp.schemas.settings import NotificationConfig, ProxyConfig, TikuConfig

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_admin)])


async def _load_json(db: AsyncSession, key: str) -> dict:
    result = await db.execute(select(AppSetting).where(AppSetting.key == key))
    setting = result.scalar_one_or_none()
    if not setting or not setting.value:
        return {}
    try:
        return json.loads(setting.value)
    except json.JSONDecodeError:
        return {}


async def _save_json(db: AsyncSession, key: str, data: dict) -> None:
    payload = json.dumps(data, ensure_ascii=False)
    result = await db.execute(select(AppSetting).where(AppSetting.key == key))
    setting = result.scalar_one_or_none()
    if setting is None:
        db.add(AppSetting(key=key, value=payload))
    else:
        setting.value = payload
    await db.commit()


@router.get("/tiku", response_model=TikuConfig)
async def get_tiku(db: AsyncSession = Depends(get_db_session)):
    data = await _load_json(db, AppSetting.KEY_TIKU_CONFIG)
    # 用默认值兜底
    return TikuConfig(**data) if data else TikuConfig()


@router.post("/tiku")
async def save_tiku(
    payload: TikuConfig,
    db: AsyncSession = Depends(get_db_session),
):
    # 序列化时把 bool/数值都转成字符串，因为底层 Tiku.config_set 假设是字符串
    data = payload.model_dump()
    serialized = {k: ("true" if v is True else "false" if v is False else str(v)) for k, v in data.items()}
    await _save_json(db, AppSetting.KEY_TIKU_CONFIG, serialized)
    return {"status": True, "msg": "题库配置已保存"}


@router.get("/notification", response_model=NotificationConfig)
async def get_notification(db: AsyncSession = Depends(get_db_session)):
    data = await _load_json(db, AppSetting.KEY_NOTIFICATION_CONFIG)
    return NotificationConfig(**data) if data else NotificationConfig()


@router.post("/notification")
async def save_notification(
    payload: NotificationConfig,
    db: AsyncSession = Depends(get_db_session),
):
    await _save_json(db, AppSetting.KEY_NOTIFICATION_CONFIG, payload.model_dump())
    return {"status": True, "msg": "通知配置已保存"}


@router.get("/proxy", response_model=ProxyConfig)
async def get_proxy(db: AsyncSession = Depends(get_db_session)):
    data = await _load_json(db, AppSetting.KEY_PROXY_CONFIG)
    return ProxyConfig(**data) if data else ProxyConfig()


@router.post("/proxy")
async def save_proxy(
    payload: ProxyConfig,
    db: AsyncSession = Depends(get_db_session),
):
    await _save_json(db, AppSetting.KEY_PROXY_CONFIG, payload.model_dump())
    return {"status": True, "msg": "代理池配置已保存，新任务生效"}
