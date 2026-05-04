# -*- coding: utf-8 -*-
"""学习任务 ORM 模型"""
import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from webapp.db import Base


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskMode(str, enum.Enum):
    """刷课模式
    - normal：正常模式，按真实进度模拟播放（兜底安全）
    - quick：快速通过，直接以 playingTime=duration 一次性上报视频/音频进度
    """

    NORMAL = "normal"
    QUICK = "quick"


class StudyTask(Base):
    """刷课任务"""

    __tablename__ = "study_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chaoxing_accounts.id", ondelete="CASCADE"), nullable=False
    )
    course_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON array
    speed: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    jobs: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    notopen_action: Mapped[str] = mapped_column(String(16), default="retry", nullable=False)
    mode: Mapped[str] = mapped_column(String(16), default=TaskMode.NORMAL.value, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=TaskStatus.PENDING.value, nullable=False)
    total_chapters: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    done_chapters: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_course: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    current_chapter: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    logs: Mapped[list["TaskLog"]] = relationship(
        "TaskLog", back_populates="task", cascade="all, delete-orphan"
    )


class TaskLog(Base):
    """任务日志（用于 SSE 历史回放）"""

    __tablename__ = "task_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("study_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    level: Mapped[str] = mapped_column(String(16), default="info", nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, index=True
    )

    task: Mapped["StudyTask"] = relationship("StudyTask", back_populates="logs")
