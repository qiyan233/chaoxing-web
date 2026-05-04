# -*- coding: utf-8 -*-
"""数据库 cookies 提供器：从 chaoxing_accounts.cookies_json 字段读写"""
import json
from typing import Any, Dict

import requests

from api.session_context import CookiesProvider
from webapp.db import SyncSessionLocal
from webapp.models.account import ChaoxingAccount


class DBCookiesProvider(CookiesProvider):
    """基于 SQLite 的 cookies 持久化"""

    def __init__(self, account_id: int):
        self.account_id = account_id

    def load(self) -> Dict[str, Any]:
        with SyncSessionLocal() as session:
            account = session.get(ChaoxingAccount, self.account_id)
            if not account or not account.cookies_json:
                return {}
            try:
                data = json.loads(account.cookies_json)
                return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                return {}

    def save(self, http_session: "requests.Session") -> None:
        cookies_dict = {k: v for k, v in http_session.cookies.items()}
        payload = json.dumps(cookies_dict, ensure_ascii=False)
        with SyncSessionLocal() as session:
            account = session.get(ChaoxingAccount, self.account_id)
            if account is None:
                return
            account.cookies_json = payload
            session.commit()
