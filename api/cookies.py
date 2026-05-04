# -*- coding: utf-8 -*-
"""Web 端 Cookies 持久化。

通过 SessionContext 绑定的 CookiesProvider 写入数据库。没有绑定
Provider 时保持无副作用，避免纯 Web 项目意外生成 cookies.txt。
"""

import requests

from api.session_context import SessionContext


def save_cookies(session: requests.Session):
    """保存 cookies。Web 端走 CookiesProvider。"""
    provider = SessionContext.get_cookies_provider()
    if provider is not None:
        try:
            provider.save(session)
        except Exception:
            # 持久化失败时不影响登录流程
            pass

    return


def use_cookies() -> dict:
    """加载 cookies。Web 端走 CookiesProvider。"""
    provider = SessionContext.get_cookies_provider()
    if provider is not None:
        try:
            return provider.load()
        except Exception:
            return {}

    return {}
