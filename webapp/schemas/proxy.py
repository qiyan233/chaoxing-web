# -*- coding: utf-8 -*-
"""代理池 API schema"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class ProxyTestRequest(BaseModel):
    proxy_url: str = Field(..., min_length=3, max_length=255)
    test_url: str = Field(default="http://httpbin.org/ip", max_length=512)
    timeout_seconds: float = Field(default=8.0, ge=1.0, le=30.0)


class ProxyAddRequest(BaseModel):
    proxy_url: str = Field(..., min_length=3, description="支持单个代理或多行代理")
    test_url: str = Field(default="http://httpbin.org/ip", max_length=512)
    timeout_seconds: float = Field(default=8.0, ge=1.0, le=30.0)


class ScdnProxyImportRequest(BaseModel):
    protocol: str = Field(default="http", pattern="^(http|https|socks4|socks5|all)$")
    count: int = Field(default=10, ge=1, le=20)
    country_code: str = Field(default="", max_length=2)
    test_url: str = Field(default="http://httpbin.org/ip", max_length=512)
    timeout_seconds: float = Field(default=8.0, ge=1.0, le=30.0)


class ProxyTestResult(BaseModel):
    ok: bool
    proxy_url: str
    latency_ms: Optional[int] = None
    status_code: Optional[int] = None
    error: Optional[str] = None
    origin: Optional[str] = None


class ProxyResponse(BaseModel):
    id: int
    proxy_url: str
    status: str
    latency_ms: Optional[int] = None
    last_tested_at: Optional[datetime] = None
    fail_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProxyAddResponse(BaseModel):
    status: bool
    msg: str
    added: List[ProxyResponse] = []
    failed: List[ProxyTestResult] = []
