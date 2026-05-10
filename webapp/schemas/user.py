# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class PlatformUserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=4, max_length=128)
    status: str = Field(default="active", pattern="^(active|disabled)$")


class PlatformLoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class PlatformUserUpdate(BaseModel):
    password: Optional[str] = Field(default=None, min_length=4, max_length=128)
    status: Optional[str] = Field(default=None, pattern="^(active|disabled)$")


class PlatformUserResponse(BaseModel):
    id: int
    username: str
    role: str
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
