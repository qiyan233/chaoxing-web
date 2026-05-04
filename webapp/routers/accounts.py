# -*- coding: utf-8 -*-
"""超星账号管理路由"""
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from webapp.deps import get_db_session, require_login
from webapp.models.account import ChaoxingAccount
from webapp.schemas.account import (
    AccountCreate,
    AccountResponse,
    AccountUpdate,
)
from webapp.services.chaoxing_service import ChaoxingService
from webapp.services.credential import encrypt_password

router = APIRouter(prefix="/api/accounts", tags=["accounts"], dependencies=[Depends(require_login)])


@router.get("", response_model=List[AccountResponse])
async def list_accounts(db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(select(ChaoxingAccount).order_by(ChaoxingAccount.id.asc()))
    return list(result.scalars().all())


@router.post("", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
async def create_account(
    payload: AccountCreate,
    db: AsyncSession = Depends(get_db_session),
):
    # 检查手机号唯一
    existing = await db.execute(
        select(ChaoxingAccount).where(ChaoxingAccount.phone == payload.phone)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="该手机号已存在")

    account = ChaoxingAccount(
        phone=payload.phone,
        password_enc=encrypt_password(payload.password),
        nickname=payload.nickname,
        status="idle",
    )
    db.add(account)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="保存失败：可能存在重复手机号")

    await db.commit()
    await db.refresh(account)

    # 立即测试登录
    if payload.verify_login:
        result = await run_in_threadpool(ChaoxingService.verify_login, account)
        if result.get("status"):
            account.status = "idle"
            account.last_login_at = datetime.utcnow()
            account.last_error = None
        else:
            account.status = "error"
            account.last_error = result.get("msg") or "登录失败"
        await db.commit()
        await db.refresh(account)

    return account


@router.put("/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: int,
    payload: AccountUpdate,
    db: AsyncSession = Depends(get_db_session),
):
    account = await db.get(ChaoxingAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")

    if payload.password is not None:
        account.password_enc = encrypt_password(payload.password)
        account.cookies_json = None  # 密码变更时清掉旧 cookies
    if payload.nickname is not None:
        account.nickname = payload.nickname

    await db.commit()
    await db.refresh(account)
    return account


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    account = await db.get(ChaoxingAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    await db.delete(account)
    await db.commit()
    return None


@router.post("/{account_id}/login")
async def relogin(
    account_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """重新测试登录"""
    account = await db.get(ChaoxingAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")

    result = await run_in_threadpool(ChaoxingService.verify_login, account)
    if result.get("status"):
        account.last_login_at = datetime.utcnow()
        account.status = "idle"
        account.last_error = None
    else:
        account.status = "error"
        account.last_error = result.get("msg") or "登录失败"
    await db.commit()
    return result


@router.get("/{account_id}/courses")
async def list_courses(
    account_id: int,
    refresh: bool = False,
    db: AsyncSession = Depends(get_db_session),
):
    """拉取账号的课程列表（带 TTL 缓存）"""
    account = await db.get(ChaoxingAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")

    try:
        courses = await run_in_threadpool(
            ChaoxingService.fetch_courses, account, refresh
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"拉取课程失败: {exc}")

    return {"account_id": account_id, "courses": courses, "count": len(courses)}
