# -*- coding: utf-8 -*-
"""平台用户管理 API（管理员专用）。"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from webapp.deps import get_db_session, require_admin
from webapp.models.account import ChaoxingAccount
from webapp.models.user import PlatformUser
from webapp.schemas.user import (
    PlatformUserCreate,
    PlatformUserResponse,
    PlatformUserUpdate,
)
from webapp.services.credential import hash_admin_password

router = APIRouter(prefix="/api/users", tags=["users"], dependencies=[Depends(require_admin)])


@router.get("", response_model=List[PlatformUserResponse])
async def list_users(db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(select(PlatformUser).order_by(PlatformUser.id.asc()))
    return list(result.scalars().all())


@router.post("", response_model=PlatformUserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: PlatformUserCreate,
    db: AsyncSession = Depends(get_db_session),
):
    user = PlatformUser(
        username=payload.username.strip(),
        password_hash=hash_admin_password(payload.password),
        role="user",
        status=payload.status,
    )
    if not user.username:
        raise HTTPException(status_code=400, detail="用户名不能为空")
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="用户名已存在")
    await db.refresh(user)
    return user


@router.get("/overview")
async def users_overview(db: AsyncSession = Depends(get_db_session)):
    """管理员用户页：平台账号 + 名下学习通账号概览。"""
    users_result = await db.execute(select(PlatformUser).order_by(PlatformUser.id.asc()))
    users = list(users_result.scalars().all())
    accounts_result = await db.execute(select(ChaoxingAccount).order_by(ChaoxingAccount.id.asc()))
    accounts = list(accounts_result.scalars().all())

    by_user: dict[int, list[ChaoxingAccount]] = {}
    for account in accounts:
        if account.user_id is not None:
            by_user.setdefault(account.user_id, []).append(account)

    return [
        {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "status": user.status,
            "created_at": user.created_at,
            "updated_at": user.updated_at,
            "account_count": len(by_user.get(user.id, [])),
            "accounts": [
                {
                    "id": account.id,
                    "phone": account.phone,
                    "nickname": account.nickname,
                    "status": account.status,
                    "last_login_at": account.last_login_at,
                    "last_error": account.last_error,
                }
                for account in by_user.get(user.id, [])
            ],
        }
        for user in users
    ]


@router.get("/account-options")
async def account_options(db: AsyncSession = Depends(get_db_session)):
    """可绑定到平台账号的学习通账号列表。"""
    accounts_result = await db.execute(select(ChaoxingAccount).order_by(ChaoxingAccount.id.asc()))
    accounts = list(accounts_result.scalars().all())
    return [
        {
            "id": account.id,
            "user_id": account.user_id,
            "phone": account.phone,
            "nickname": account.nickname,
            "status": account.status,
        }
        for account in accounts
    ]


@router.put("/{user_id}", response_model=PlatformUserResponse)
async def update_user(
    user_id: int,
    payload: PlatformUserUpdate,
    db: AsyncSession = Depends(get_db_session),
):
    user = await db.get(PlatformUser, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if payload.password:
        user.password_hash = hash_admin_password(payload.password)
    if payload.status:
        user.status = payload.status
    await db.commit()
    await db.refresh(user)
    return user


@router.put("/{user_id}/accounts/{account_id}")
async def bind_account_to_user(
    user_id: int,
    account_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """把已有学习通账号绑定到指定平台账号。"""
    user = await db.get(PlatformUser, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    account = await db.get(ChaoxingAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="学习通账号不存在")
    account.user_id = user.id
    await db.commit()
    return {"status": True, "msg": "绑定成功"}


@router.delete("/{user_id}/accounts/{account_id}")
async def unbind_account_from_user(
    user_id: int,
    account_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """解除平台账号与学习通账号绑定。"""
    account = await db.get(ChaoxingAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="学习通账号不存在")
    if account.user_id != user_id:
        raise HTTPException(status_code=400, detail="该学习通账号不属于此平台账号")
    account.user_id = None
    await db.commit()
    return {"status": True, "msg": "已解除绑定"}


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int, db: AsyncSession = Depends(get_db_session)):
    user = await db.get(PlatformUser, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    await db.delete(user)
    await db.commit()
    return None
