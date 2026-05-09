# -*- coding: utf-8 -*-
"""本地代理池 ORM 模型"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from webapp.db import Base


class ProxyEntry(Base):
    """本地代理池条目"""

    __tablename__ = "proxy_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proxy_url: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False, index=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_tested_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    fail_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
