# -*- coding: utf-8 -*-
"""SQLAlchemy 引擎与 Session 工厂"""
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from webapp.config import DATABASE_URL, SYNC_DATABASE_URL


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类"""
    pass


# 异步引擎（FastAPI 路由层使用）
async_engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False},
)
AsyncSessionLocal = async_sessionmaker(
    async_engine, class_=AsyncSession, expire_on_commit=False
)

# 同步引擎（APScheduler / 后台线程任务使用）
sync_engine = create_engine(
    SYNC_DATABASE_URL,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False},
)
SyncSessionLocal = sessionmaker(sync_engine, expire_on_commit=False, future=True)


@asynccontextmanager
async def get_db() -> AsyncIterator[AsyncSession]:
    """异步 DB session 上下文管理器"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """创建所有表（首次启动时调用）"""
    # 触发模型注册
    from webapp.models import account, proxy, task, settings, user  # noqa: F401

    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_run_lightweight_migrations)
        await conn.run_sync(_ensure_default_admin_password)


def _run_lightweight_migrations(sync_conn) -> None:
    """对 SQLite 做最小可用的列级迁移（仅追加缺失列）。"""
    from sqlalchemy import text

    # study_tasks.mode：旧库可能没有这列，缺失则补 default normal
    cols = sync_conn.exec_driver_sql(
        "PRAGMA table_info(study_tasks)"
    ).fetchall()
    existing = {row[1] for row in cols}
    if "mode" not in existing:
        sync_conn.exec_driver_sql(
            "ALTER TABLE study_tasks ADD COLUMN mode VARCHAR(16) NOT NULL DEFAULT 'normal'"
        )

    account_cols = sync_conn.exec_driver_sql(
        "PRAGMA table_info(chaoxing_accounts)"
    ).fetchall()
    account_existing = {row[1] for row in account_cols}
    if "user_id" not in account_existing:
        sync_conn.exec_driver_sql(
            "ALTER TABLE chaoxing_accounts ADD COLUMN user_id INTEGER"
        )
        sync_conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_chaoxing_accounts_user_id ON chaoxing_accounts (user_id)"
        )


def _ensure_default_admin_password(sync_conn) -> None:
    """初始化/迁移默认管理员密码。

    仅执行一次：把当前库的管理员密码设置为 admin123，并写入迁移标记。
    后续管理员在页面里修改密码后，启动服务不会再覆盖。
    """
    from webapp.models.settings import AppSetting
    from webapp.services.credential import hash_admin_password

    marker_key = "default_admin_password_20260510"
    marker = sync_conn.exec_driver_sql(
        "SELECT value FROM app_settings WHERE key = ?", (marker_key,)
    ).fetchone()
    if marker:
        return

    password_hash = hash_admin_password("admin123")
    existing = sync_conn.exec_driver_sql(
        "SELECT value FROM app_settings WHERE key = ?", (AppSetting.KEY_ADMIN_PASSWORD,)
    ).fetchone()
    if existing:
        sync_conn.exec_driver_sql(
            "UPDATE app_settings SET value = ? WHERE key = ?",
            (password_hash, AppSetting.KEY_ADMIN_PASSWORD),
        )
    else:
        sync_conn.exec_driver_sql(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)",
            (AppSetting.KEY_ADMIN_PASSWORD, password_hash),
        )

    sync_conn.exec_driver_sql(
        "INSERT INTO app_settings (key, value) VALUES (?, ?)",
        (marker_key, "done"),
    )
