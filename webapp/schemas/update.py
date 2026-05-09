# -*- coding: utf-8 -*-
"""在线更新 API schema"""
from typing import List, Optional

from pydantic import BaseModel


class UpdateStatus(BaseModel):
    supported: bool
    branch: Optional[str] = None
    current_commit: Optional[str] = None
    remote_commit: Optional[str] = None
    current_message: Optional[str] = None
    remote_message: Optional[str] = None
    has_update: bool = False
    dirty: bool = False
    dirty_files: List[str] = []
    ahead: int = 0
    behind: int = 0
    recent_commits: List[str] = []
    error: Optional[str] = None


class UpdateApplyResult(BaseModel):
    status: bool
    msg: str
    stdout: str = ""
    stderr: str = ""
    need_restart: bool = False


class RestartResult(BaseModel):
    status: bool
    msg: str
