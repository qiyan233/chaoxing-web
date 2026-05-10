# -*- coding: utf-8 -*-
"""线程级会话上下文（基于 contextvars，可被 ThreadPoolExecutor 通过 copy_context 传播）

Web 端在启动刷课任务前，调用 ``SessionContext.scope()`` 进入上下文，
``api.base.SessionManager`` 与 ``api.cookies`` 会自动切换到隔离会话。
未进入 scope 时不会持久化 cookies，避免多账号任务互相污染。

子线程通过 ``contextvars.copy_context().run(...)`` 即可继承父线程的隔离会话。
"""
from __future__ import annotations

import contextvars
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator, Optional

if TYPE_CHECKING:
    import requests


class CookiesProvider:
    """Cookies 持久化抽象接口"""

    def load(self) -> dict:
        raise NotImplementedError

    def save(self, session: "requests.Session") -> None:
        raise NotImplementedError


class IsolatedSession:
    """独立的 HTTP 会话容器（每账号一个）"""

    def __init__(self, cookies_provider: Optional[CookiesProvider] = None):
        import functools

        import requests
        from requests.adapters import HTTPAdapter

        from api.config import GlobalConst as gc

        self._session = requests.Session()
        self._session.mount("https://", HTTPAdapter(max_retries=10))
        self._session.mount("http://", HTTPAdapter(max_retries=10))
        self._session.request = functools.partial(self._session.request, timeout=15)
        self._session.headers.clear()
        self._session.headers.update(gc.HEADERS)

        self._cookies_provider = cookies_provider
        if cookies_provider is not None:
            try:
                self._session.cookies.update(cookies_provider.load())
            except Exception:
                pass

    @property
    def session(self) -> "requests.Session":
        return self._session

    def update_cookies(self) -> None:
        if self._cookies_provider is not None:
            try:
                self._session.cookies.update(self._cookies_provider.load())
            except Exception:
                pass

    def save_cookies(self) -> None:
        if self._cookies_provider is not None:
            self._cookies_provider.save(self._session)


# ---------- ContextVar 槽位 ----------
_session_holder_var: contextvars.ContextVar[Optional[IsolatedSession]] = contextvars.ContextVar(
    "session_holder", default=None
)
_cookies_provider_var: contextvars.ContextVar[Optional[CookiesProvider]] = contextvars.ContextVar(
    "cookies_provider", default=None
)
_progress_callback_var: contextvars.ContextVar = contextvars.ContextVar(
    "progress_callback", default=None
)
_cancel_event_var: contextvars.ContextVar[Optional[threading.Event]] = contextvars.ContextVar(
    "cancel_event", default=None
)


class SessionContext:
    """基于 contextvars 的会话上下文（可跨线程传播）"""

    # ---------- 进入 / 退出 ----------
    @classmethod
    @contextmanager
    def scope(
        cls,
        session_holder: IsolatedSession,
        cookies_provider: Optional[CookiesProvider] = None,
        progress_callback=None,
        cancel_event: Optional[threading.Event] = None,
    ) -> Iterator[None]:
        tokens = []
        tokens.append(_session_holder_var.set(session_holder))
        tokens.append(
            _cookies_provider_var.set(cookies_provider or session_holder._cookies_provider)
        )
        if progress_callback is not None:
            tokens.append(_progress_callback_var.set(progress_callback))
        if cancel_event is not None:
            tokens.append(_cancel_event_var.set(cancel_event))
        try:
            yield
        finally:
            for token in reversed(tokens):
                try:
                    token.var.reset(token)
                except (LookupError, ValueError):
                    pass

    # ---------- 访问器 ----------
    @classmethod
    def get_session_holder(cls) -> Optional[IsolatedSession]:
        return _session_holder_var.get()

    @classmethod
    def get_cookies_provider(cls) -> Optional[CookiesProvider]:
        return _cookies_provider_var.get()

    @classmethod
    def get_progress_callback(cls):
        return _progress_callback_var.get()

    @classmethod
    def get_cancel_event(cls) -> Optional[threading.Event]:
        return _cancel_event_var.get()

    @classmethod
    def is_cancelled(cls) -> bool:
        evt = cls.get_cancel_event()
        return bool(evt and evt.is_set())

    @classmethod
    def report_progress(cls, event_type: str, **payload) -> None:
        cb = cls.get_progress_callback()
        if cb is not None:
            try:
                cb({"type": event_type, **payload})
            except Exception:
                pass


class CancelledError(Exception):
    """任务被取消"""
