# -*- coding: utf-8 -*-
"""全局应用设置 ORM 模型"""
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from webapp.db import Base


class AppSetting(Base):
    """全局键值对配置（题库 / 通知 / 管理员密码哈希）"""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # 预定义键名常量
    KEY_ADMIN_PASSWORD = "admin_password_hash"
    KEY_TIKU_CONFIG = "tiku_config"
    KEY_NOTIFICATION_CONFIG = "notification_config"
    KEY_RUNTIME_CONFIG = "runtime_config"  # max_concurrent_accounts 等
