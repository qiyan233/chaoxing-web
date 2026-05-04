# -*- coding: utf-8 -*-
"""FastAPI 依赖注入：DB session、鉴权"""
from typing import AsyncIterator, Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from webapp.db import AsyncSessionLocal
from webapp.models.settings import AppSetting


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """异步 DB session 依赖"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def is_admin_initialized(db: AsyncSession) -> bool:
    """是否已设置管理员密码"""
    result = await db.execute(
        select(AppSetting).where(AppSetting.key == AppSetting.KEY_ADMIN_PASSWORD)
    )
    setting = result.scalar_one_or_none()
    return setting is not None and bool(setting.value)


async def require_login(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> None:
    """要求登录，否则返回 401"""
    if not request.session.get("authenticated"):
        # 区分页面与 API：页面让前端重定向
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="未登录",
                headers={"Location": "/login"},
            )
        raise HTTPException(status_code=401, detail="未登录")


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> Optional[str]:
    """获取当前已登录用户标识（单用户模式下固定为 'admin'）"""
    if request.session.get("authenticated"):
        return "admin"
    return None
