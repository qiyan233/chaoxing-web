# -*- coding: utf-8 -*-
"""Pydantic schemas 聚合"""
from webapp.schemas.account import (
    AccountCreate,
    AccountResponse,
    AccountUpdate,
)
from webapp.schemas.task import TaskCreate, TaskResponse, TaskLogResponse
from webapp.schemas.settings import (
    TikuConfig,
    NotificationConfig,
    AdminPasswordSet,
)

__all__ = [
    "AccountCreate",
    "AccountResponse",
    "AccountUpdate",
    "TaskCreate",
    "TaskResponse",
    "TaskLogResponse",
    "TikuConfig",
    "NotificationConfig",
    "AdminPasswordSet",
]
