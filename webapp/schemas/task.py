# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class TaskCreate(BaseModel):
    account_id: int
    course_ids: Optional[List[str]] = Field(None, description="留空表示全部课程")
    speed: float = Field(1.0, ge=1.0, le=2.0)
    jobs: int = Field(4, ge=1, le=16)
    notopen_action: str = Field("retry", pattern="^(retry|ask|continue)$")
    mode: str = Field(
        "normal",
        pattern="^(normal|quick)$",
        description="normal=正常模拟播放, quick=直接上报满时长",
    )


class TaskResponse(BaseModel):
    id: int
    account_id: int
    status: str
    course_ids: Optional[str] = None
    speed: float
    jobs: int
    notopen_action: str
    mode: str = "normal"
    total_chapters: int
    done_chapters: int
    current_course: Optional[str] = None
    current_chapter: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error_message: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class TaskLogResponse(BaseModel):
    id: int
    task_id: int
    level: str
    message: str
    created_at: datetime

    class Config:
        from_attributes = True
