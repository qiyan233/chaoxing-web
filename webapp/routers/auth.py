# -*- coding: utf-8 -*-
"""鉴权路由：首次设置密码 / 登录 / 登出"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from webapp.deps import get_db_session, is_admin_initialized
from webapp.models.settings import AppSetting
from webapp.schemas.settings import AdminPasswordSet, LoginRequest
from webapp.services.credential import (
    hash_admin_password,
    verify_admin_password,
)

router = APIRouter(tags=["auth"])


@router.post("/api/setup")
async def setup_admin(
    payload: AdminPasswordSet,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """首次启动设置管理员密码"""
    if await is_admin_initialized(db):
        raise HTTPException(status_code=400, detail="管理员密码已初始化，请使用 /api/admin/password 修改")

    setting = AppSetting(
        key=AppSetting.KEY_ADMIN_PASSWORD,
        value=hash_admin_password(payload.new_password),
    )
    db.add(setting)
    await db.commit()

    request.session["authenticated"] = True
    request.session["user"] = "admin"
    return {"status": True, "msg": "初始化成功"}


@router.post("/api/login")
async def login(
    payload: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """登录"""
    result = await db.execute(
        select(AppSetting).where(AppSetting.key == AppSetting.KEY_ADMIN_PASSWORD)
    )
    setting = result.scalar_one_or_none()

    if setting is None or not setting.value:
        raise HTTPException(status_code=400, detail="尚未初始化管理员密码，请先访问 /setup")

    if not verify_admin_password(payload.password, setting.value):
        raise HTTPException(status_code=401, detail="密码错误")

    request.session["authenticated"] = True
    request.session["user"] = "admin"
    return {"status": True, "msg": "登录成功"}


@router.post("/api/logout")
async def logout(request: Request):
    request.session.clear()
    return {"status": True, "msg": "已登出"}


@router.post("/api/admin/password")
async def change_password(
    payload: AdminPasswordSet,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """修改管理员密码（需先登录）"""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401, detail="未登录")

    result = await db.execute(
        select(AppSetting).where(AppSetting.key == AppSetting.KEY_ADMIN_PASSWORD)
    )
    setting = result.scalar_one_or_none()

    if setting and payload.old_password:
        if not verify_admin_password(payload.old_password, setting.value):
            raise HTTPException(status_code=401, detail="旧密码错误")

    new_hash = hash_admin_password(payload.new_password)
    if setting is None:
        db.add(AppSetting(key=AppSetting.KEY_ADMIN_PASSWORD, value=new_hash))
    else:
        setting.value = new_hash
    await db.commit()

    return {"status": True, "msg": "密码已更新"}
