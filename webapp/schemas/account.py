# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class AccountCreate(BaseModel):
    phone: str = Field(..., min_length=5, max_length=32, description="手机号")
    password: str = Field(..., min_length=1, max_length=128, description="密码")
    nickname: Optional[str] = Field(None, max_length=64, description="备注昵称")
    verify_login: bool = Field(default=True, description="是否立即测试登录")


class AccountUpdate(BaseModel):
    password: Optional[str] = Field(None, min_length=1, max_length=128)
    nickname: Optional[str] = Field(None, max_length=64)


class AccountResponse(BaseModel):
    id: int
    phone: str
    nickname: Optional[str] = None
    status: str
    last_login_at: Optional[datetime] = None
    last_error: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True
