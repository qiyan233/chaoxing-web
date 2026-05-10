# -*- coding: utf-8 -*-
"""超星账号 ORM 模型"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from webapp.db import Base


class ChaoxingAccount(Base):
    """超星学习通账号"""

    __tablename__ = "chaoxing_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("platform_users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    phone: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    password_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nickname: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    cookies_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="idle", nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<ChaoxingAccount id={self.id} phone={self.phone} status={self.status}>"
