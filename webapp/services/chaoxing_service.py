# -*- coding: utf-8 -*-
"""超星账号服务层

封装：登录验证、课程列表拉取（带 TTL 缓存）、章节信息查询
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from api.base import Account, Chaoxing
from api.session_context import IsolatedSession, SessionContext
from webapp.config import COURSE_CACHE_SECONDS
from webapp.models.account import ChaoxingAccount
from webapp.services.cookies_provider import DBCookiesProvider
from webapp.services.credential import decrypt_password
from webapp.services.proxy_selector import proxy_selector


_course_cache: Dict[int, Dict[str, Any]] = {}
_cache_lock = threading.Lock()


class ChaoxingService:
    """对接 api.base.Chaoxing 的同步服务

    每次方法调用都会绑定一个独立 SessionContext，所以多账号并发安全。
    """

    @classmethod
    def _build_chaoxing(cls, account: ChaoxingAccount) -> tuple[Chaoxing, IsolatedSession]:
        """根据 ORM 账号对象构建 Chaoxing 实例和隔离会话"""
        plain_password = decrypt_password(account.password_enc)
        cx_account = Account(account.phone, plain_password)
        cookies_provider = DBCookiesProvider(account.id)
        holder = IsolatedSession(cookies_provider=cookies_provider)
        proxy_selector.apply_to_session(holder.session, seed=account.id)
        # 题库为 None 时构造一个禁用的 Tiku 占位符
        chaoxing = Chaoxing(account=cx_account, tiku=cls._build_disabled_tiku())
        return chaoxing, holder

    @staticmethod
    def _build_disabled_tiku():
        """构造一个标记为禁用的 Tiku（账号查询/登录场景不需要题库）"""
        from api.answer import Tiku
        tiku = Tiku()
        tiku.DISABLE = True
        return tiku

    @staticmethod
    def _attach_display_name(result: Dict[str, Any], chaoxing: Chaoxing) -> Dict[str, Any]:
        """Login result helper: add nickname when Chaoxing profile exposes it."""
        if not result.get("status"):
            return result
        display_name = chaoxing.get_account_display_name()
        if display_name:
            result = dict(result)
            result["nickname"] = display_name
        return result


    # ---------- 公共方法 ----------
    @classmethod
    def verify_login(cls, account: ChaoxingAccount) -> Dict[str, Any]:
        """尝试用账号密码登录，成功后保存 cookies 到 DB"""
        chaoxing, holder = cls._build_chaoxing(account)
        with SessionContext.scope(holder):
            try:
                # 优先用现有 cookies 校验
                if account.cookies_json:
                    result = chaoxing.login(login_with_cookies=True)
                    if result["status"]:
                        holder.save_cookies()
                        return cls._attach_display_name({"status": True, "msg": "Cookie 校验通过"}, chaoxing)
                # 回退到密码登录
                result = chaoxing.login(login_with_cookies=False)
                if result["status"]:
                    holder.save_cookies()
                    result = cls._attach_display_name(result, chaoxing)
                return result
            except Exception as exc:
                return {"status": False, "msg": f"登录异常: {exc}"}

    @classmethod
    def fetch_courses(
        cls,
        account: ChaoxingAccount,
        force_refresh: bool = False,
    ) -> List[Dict[str, Any]]:
        """拉取该账号的课程列表（带 TTL 缓存）"""
        now = time.time()

        if not force_refresh:
            with _cache_lock:
                cached = _course_cache.get(account.id)
                if cached and now - cached["fetched_at"] < COURSE_CACHE_SECONDS:
                    return cached["courses"]

        chaoxing, holder = cls._build_chaoxing(account)
        with SessionContext.scope(holder):
            login_result = chaoxing.login(login_with_cookies=bool(account.cookies_json))
            if not login_result["status"]:
                # 重试用密码登录
                login_result = chaoxing.login(login_with_cookies=False)
                if not login_result["status"]:
                    raise RuntimeError(f"登录失败: {login_result.get('msg')}")

            holder.save_cookies()
            courses = chaoxing.get_course_list()

        # 简化返回字段
        normalized = [
            {
                "courseId": c.get("courseId"),
                "clazzId": c.get("clazzId"),
                "cpi": c.get("cpi"),
                "title": c.get("title"),
                "teacher": c.get("teacher", ""),
                "desc": c.get("desc", ""),
            }
            for c in courses
        ]

        with _cache_lock:
            _course_cache[account.id] = {"fetched_at": now, "courses": normalized}

        return normalized

    @classmethod
    def invalidate_cache(cls, account_id: int) -> None:
        with _cache_lock:
            _course_cache.pop(account_id, None)
