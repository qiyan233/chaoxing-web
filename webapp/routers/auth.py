# -*- coding: utf-8 -*-
"""鉴权路由：首次设置密码 / 登录 / 登出 / 管理员状态检查"""
import ctypes
import os
import platform
import shutil
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from webapp.config import DATA_DIR
from webapp.deps import get_db_session, is_admin_initialized, require_admin
from webapp.models.account import ChaoxingAccount
from webapp.models.proxy import ProxyEntry
from webapp.models.settings import AppSetting
from webapp.models.task import StudyTask, TaskStatus
from webapp.models.user import PlatformUser
from webapp.schemas.settings import AdminPasswordSet, LoginRequest
from webapp.schemas.user import PlatformLoginRequest
from webapp.services.credential import (
    hash_admin_password,
    verify_admin_password,
)

router = APIRouter(tags=["auth"])
SERVER_STARTED_AT = time.time()


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
    request.session["role"] = "admin"
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
    request.session["role"] = "admin"
    return {"status": True, "msg": "登录成功"}


@router.post("/api/admin/login")
async def admin_login(
    payload: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """管理员登录兼容入口，复用 /api/login 逻辑。"""
    return await login(payload, request, db)


@router.post("/api/user/login")
async def user_login(
    payload: PlatformLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """普通平台用户登录。"""
    result = await db.execute(
        select(PlatformUser).where(PlatformUser.username == payload.username.strip())
    )
    user = result.scalar_one_or_none()
    if user is None or user.role != "user":
        raise HTTPException(status_code=401, detail="账号或密码错误")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="账号已被停用")
    if not verify_admin_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="账号或密码错误")

    request.session["authenticated"] = True
    request.session["user"] = user.username
    request.session["user_id"] = user.id
    request.session["role"] = "user"
    return {"status": True, "msg": "登录成功", "role": "user"}


def _memory_status() -> dict[str, Any]:
    """返回跨平台内存概览；拿不到时返回 None 值，避免引入额外依赖。"""
    if platform.system().lower() == "windows":
        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            used = status.ullTotalPhys - status.ullAvailPhys
            return {
                "total": status.ullTotalPhys,
                "used": used,
                "available": status.ullAvailPhys,
                "percent": round(status.dwMemoryLoad, 1),
            }

    meminfo = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                key, value = line.split(":", 1)
                meminfo[key] = int(value.strip().split()[0]) * 1024
        total = meminfo.get("MemTotal")
        available = meminfo.get("MemAvailable")
        if total and available is not None:
            used = total - available
            return {
                "total": total,
                "used": used,
                "available": available,
                "percent": round(used / total * 100, 1),
            }
    except (OSError, ValueError):
        pass

    return {"total": None, "used": None, "available": None, "percent": None}


async def _count(db: AsyncSession, stmt) -> int:
    result = await db.execute(stmt)
    return int(result.scalar() or 0)


@router.get("/api/admin/server-status")
async def server_status(
    db: AsyncSession = Depends(get_db_session),
    _: None = Depends(require_admin),
):
    """管理员服务器状态面板数据。"""
    disk = shutil.disk_usage(DATA_DIR)
    load_avg = os.getloadavg() if hasattr(os, "getloadavg") else None
    now = time.time()

    return {
        "status": "ok",
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "started_at": datetime.fromtimestamp(SERVER_STARTED_AT).strftime("%Y-%m-%d %H:%M:%S"),
        "uptime_seconds": int(now - SERVER_STARTED_AT),
        "runtime": {
            "python": platform.python_version(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "pid": os.getpid(),
            "cpu_count": os.cpu_count() or 0,
            "load_avg": list(load_avg) if load_avg else None,
        },
        "resources": {
            "disk": {
                "total": disk.total,
                "used": disk.used,
                "free": disk.free,
                "percent": round(disk.used / disk.total * 100, 1) if disk.total else None,
                "path": str(DATA_DIR),
            },
            "memory": _memory_status(),
        },
        "business": {
            "users": await _count(db, select(func.count(PlatformUser.id))),
            "active_users": await _count(
                db, select(func.count(PlatformUser.id)).where(PlatformUser.status == "active")
            ),
            "accounts": await _count(db, select(func.count(ChaoxingAccount.id))),
            "running_accounts": await _count(
                db, select(func.count(ChaoxingAccount.id)).where(ChaoxingAccount.status == "running")
            ),
            "tasks": await _count(db, select(func.count(StudyTask.id))),
            "pending_tasks": await _count(
                db, select(func.count(StudyTask.id)).where(StudyTask.status == TaskStatus.PENDING.value)
            ),
            "running_tasks": await _count(
                db, select(func.count(StudyTask.id)).where(StudyTask.status == TaskStatus.RUNNING.value)
            ),
            "failed_tasks": await _count(
                db, select(func.count(StudyTask.id)).where(StudyTask.status == TaskStatus.FAILED.value)
            ),
            "proxies": await _count(db, select(func.count(ProxyEntry.id))),
            "active_proxies": await _count(
                db, select(func.count(ProxyEntry.id)).where(ProxyEntry.status == "active")
            ),
        },
    }


@router.post("/api/logout")
async def logout(request: Request):
    request.session.clear()
    return {"status": True, "msg": "已登出"}


@router.post("/api/admin/password")
async def change_password(
    payload: AdminPasswordSet,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    _: None = Depends(require_admin),
):
    """修改管理员密码（需先登录）"""
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
