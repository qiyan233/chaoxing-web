# -*- coding: utf-8 -*-
"""鉴权路由：首次设置密码 / 登录 / 登出 / 管理员状态检查"""
import ctypes
import os
import platform
import shutil
import threading
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
_SYSTEM_SAMPLE_LOCK = threading.Lock()
_LAST_CPU_SAMPLE: dict[str, float] | None = None
_LAST_NET_SAMPLE: dict[str, float] | None = None


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


def _filetime_to_int(filetime) -> int:
    return (int(filetime.dwHighDateTime) << 32) + int(filetime.dwLowDateTime)


def _cpu_counters() -> dict[str, float] | None:
    """读取累计 CPU tick，用连续两次差值计算实时利用率。"""
    if platform.system().lower() == "windows":
        class FileTime(ctypes.Structure):
            _fields_ = [
                ("dwLowDateTime", ctypes.c_ulong),
                ("dwHighDateTime", ctypes.c_ulong),
            ]

        idle = FileTime()
        kernel = FileTime()
        user = FileTime()
        if ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        ):
            idle_ticks = _filetime_to_int(idle)
            # Windows kernel time includes idle time.
            total_ticks = _filetime_to_int(kernel) + _filetime_to_int(user)
            return {"idle": float(idle_ticks), "total": float(total_ticks)}

    try:
        with open("/proc/stat", "r", encoding="utf-8") as fh:
            parts = fh.readline().split()
        if not parts or parts[0] != "cpu":
            return None
        values = [float(v) for v in parts[1:]]
        idle = values[3] + (values[4] if len(values) > 4 else 0.0)
        total = sum(values)
        return {"idle": idle, "total": total}
    except (OSError, ValueError, IndexError):
        return None


def _cpu_status() -> dict[str, Any]:
    sample = _cpu_counters()
    now = time.time()
    if sample is None:
        return {"percent": None, "sample_interval": None}

    sample["ts"] = now
    with _SYSTEM_SAMPLE_LOCK:
        global _LAST_CPU_SAMPLE
        previous = _LAST_CPU_SAMPLE
        _LAST_CPU_SAMPLE = sample

    if not previous:
        return {"percent": None, "sample_interval": None}

    total_delta = sample["total"] - previous["total"]
    idle_delta = sample["idle"] - previous["idle"]
    interval = max(0.0, sample["ts"] - previous["ts"])
    if total_delta <= 0:
        return {"percent": None, "sample_interval": round(interval, 2)}

    percent = max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0))
    return {"percent": round(percent, 1), "sample_interval": round(interval, 2)}


def _network_counters_linux() -> dict[str, int] | None:
    try:
        rx_total = 0
        tx_total = 0
        with open("/proc/net/dev", "r", encoding="utf-8") as fh:
            for line in fh.readlines()[2:]:
                if ":" not in line:
                    continue
                iface, data = line.split(":", 1)
                iface = iface.strip()
                if iface == "lo":
                    continue
                values = data.split()
                rx_total += int(values[0])
                tx_total += int(values[8])
        return {"rx_total": rx_total, "tx_total": tx_total}
    except (OSError, ValueError, IndexError):
        return None


def _network_counters_windows() -> dict[str, int] | None:
    """通过 Windows IP Helper API 读取网卡累计字节数。"""
    if platform.system().lower() != "windows":
        return None

    class MibIfRow(ctypes.Structure):
        _fields_ = [
            ("wszName", ctypes.c_wchar * 256),
            ("dwIndex", ctypes.c_ulong),
            ("dwType", ctypes.c_ulong),
            ("dwMtu", ctypes.c_ulong),
            ("dwSpeed", ctypes.c_ulong),
            ("dwPhysAddrLen", ctypes.c_ulong),
            ("bPhysAddr", ctypes.c_ubyte * 8),
            ("dwAdminStatus", ctypes.c_ulong),
            ("dwOperStatus", ctypes.c_ulong),
            ("dwLastChange", ctypes.c_ulong),
            ("dwInOctets", ctypes.c_ulong),
            ("dwInUcastPkts", ctypes.c_ulong),
            ("dwInNUcastPkts", ctypes.c_ulong),
            ("dwInDiscards", ctypes.c_ulong),
            ("dwInErrors", ctypes.c_ulong),
            ("dwInUnknownProtos", ctypes.c_ulong),
            ("dwOutOctets", ctypes.c_ulong),
            ("dwOutUcastPkts", ctypes.c_ulong),
            ("dwOutNUcastPkts", ctypes.c_ulong),
            ("dwOutDiscards", ctypes.c_ulong),
            ("dwOutErrors", ctypes.c_ulong),
            ("dwOutQLen", ctypes.c_ulong),
            ("dwDescrLen", ctypes.c_ulong),
            ("bDescr", ctypes.c_ubyte * 256),
        ]

    try:
        size = ctypes.c_ulong(0)
        ctypes.windll.iphlpapi.GetIfTable(None, ctypes.byref(size), False)
        if size.value <= ctypes.sizeof(ctypes.c_ulong):
            return None
        buffer = ctypes.create_string_buffer(size.value)
        if ctypes.windll.iphlpapi.GetIfTable(buffer, ctypes.byref(size), False) != 0:
            return None

        count = ctypes.c_ulong.from_buffer_copy(buffer.raw[: ctypes.sizeof(ctypes.c_ulong)]).value
        offset = ctypes.sizeof(ctypes.c_ulong)
        row_size = ctypes.sizeof(MibIfRow)
        rx_total = 0
        tx_total = 0
        for index in range(count):
            row_offset = offset + index * row_size
            if row_offset + row_size > len(buffer.raw):
                break
            row = MibIfRow.from_buffer_copy(buffer.raw[row_offset : row_offset + row_size])
            # IF_TYPE_SOFTWARE_LOOPBACK = 24；MIB_IF_OPER_STATUS_OPERATIONAL = 5
            if int(row.dwType) == 24 or int(row.dwOperStatus) != 5:
                continue
            rx_total += int(row.dwInOctets)
            tx_total += int(row.dwOutOctets)
        return {"rx_total": rx_total, "tx_total": tx_total}
    except Exception:
        return None


def _network_counters() -> dict[str, int] | None:
    return _network_counters_linux() or _network_counters_windows()


def _network_status() -> dict[str, Any]:
    counters = _network_counters()
    now = time.time()
    if counters is None:
        return {
            "rx_total": None,
            "tx_total": None,
            "rx_speed": None,
            "tx_speed": None,
            "sample_interval": None,
        }

    sample = {**counters, "ts": now}
    with _SYSTEM_SAMPLE_LOCK:
        global _LAST_NET_SAMPLE
        previous = _LAST_NET_SAMPLE
        _LAST_NET_SAMPLE = sample

    rx_speed = tx_speed = None
    interval = None
    if previous:
        interval = max(0.001, sample["ts"] - previous["ts"])
        rx_delta = max(0, sample["rx_total"] - previous["rx_total"])
        tx_delta = max(0, sample["tx_total"] - previous["tx_total"])
        rx_speed = round(rx_delta / interval, 1)
        tx_speed = round(tx_delta / interval, 1)

    return {
        "rx_total": sample["rx_total"],
        "tx_total": sample["tx_total"],
        "rx_speed": rx_speed,
        "tx_speed": tx_speed,
        "sample_interval": round(interval, 2) if interval else None,
    }


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
            "cpu": _cpu_status(),
            "disk": {
                "total": disk.total,
                "used": disk.used,
                "free": disk.free,
                "percent": round(disk.used / disk.total * 100, 1) if disk.total else None,
                "path": str(DATA_DIR),
            },
            "memory": _memory_status(),
            "network": _network_status(),
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
