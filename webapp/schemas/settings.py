# -*- coding: utf-8 -*-
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class TikuConfig(BaseModel):
    """题库配置（与 config_template.ini [tiku] 节对应）"""
    provider: str = Field(default="", description="题库类型 TikuYanxi/TikuLike/TikuAdapter/AI/SiliconFlow")
    submit: bool = Field(default=False)
    cover_rate: float = Field(default=0.9, ge=0, le=1)
    delay: float = Field(default=1.0, ge=0)
    tokens: str = Field(default="")
    url: str = Field(default="")
    endpoint: str = Field(default="")
    key: str = Field(default="")
    model: str = Field(default="")
    min_interval_seconds: int = Field(default=3, ge=0)
    http_proxy: str = Field(default="")
    siliconflow_key: str = Field(default="")
    siliconflow_model: str = Field(default="deepseek-ai/DeepSeek-R1")
    siliconflow_endpoint: str = Field(default="https://api.siliconflow.cn/v1/chat/completions")
    likeapi_search: bool = Field(default=False)
    likeapi_vision: bool = Field(default=True)
    likeapi_model: str = Field(default="glm-4.5-air")
    likeapi_retry: bool = Field(default=True)
    likeapi_retry_times: int = Field(default=3)
    true_list: str = Field(default="正确,对,√,是")
    false_list: str = Field(default="错误,错,×,否,不对,不正确")
    check_llm_connection: bool = Field(default=True)


class NotificationConfig(BaseModel):
    """外部通知配置"""
    provider: str = Field(default="", description="ServerChan/Qmsg/Bark/Telegram")
    url: str = Field(default="")
    tg_chat_id: str = Field(default="")


class ProxyConfig(BaseModel):
    """代理池配置（仅管理员可修改）"""
    enabled: bool = Field(default=False)
    proxies: str = Field(default="", description="代理列表，一行一个")
    strategy: str = Field(default="random", pattern="^(random|round_robin)$")
    failover: bool = Field(default=True, description="失败后是否允许后续任务切换代理")


class AdminPasswordSet(BaseModel):
    new_password: str = Field(..., min_length=4, max_length=128)
    old_password: Optional[str] = None


class LoginRequest(BaseModel):
    password: str
