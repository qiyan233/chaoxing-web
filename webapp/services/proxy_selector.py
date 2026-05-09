# -*- coding: utf-8 -*-
"""统一代理池选择器。

开启代理池后，Web 端登录验证、获取课程、任务执行都应走同一套代理选择逻辑。
"""
from __future__ import annotations

import json
import random
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from loguru import logger
from sqlalchemy import select

from webapp.db import SyncSessionLocal
from webapp.models.proxy import ProxyEntry
from webapp.models.settings import AppSetting
from webapp.services.proxy_pool import parse_proxy_response, parse_proxy_lines_with_scheme


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


class ProxySelector:
    """从全局代理配置里选择代理，并应用到 requests.Session。"""

    def __init__(self):
        self._cache: Dict[str, tuple[float, List[str]]] = {}
        self._lock = threading.Lock()

    def load_config(self) -> Dict[str, Any]:
        """读取 app_settings 代理配置，并在本地代理池模式下附带 active 代理快照。"""
        with SyncSessionLocal() as db:
            config: Dict[str, Any] = {}
            setting = db.get(AppSetting, AppSetting.KEY_PROXY_CONFIG)
            if setting and setting.value:
                try:
                    config = json.loads(setting.value)
                except json.JSONDecodeError:
                    config = {}

            if (config.get("source") or "manual") == "local":
                rows = db.execute(
                    select(ProxyEntry.proxy_url)
                    .where(ProxyEntry.status == "active")
                    .order_by(
                        ProxyEntry.latency_ms.is_(None),
                        ProxyEntry.latency_ms.asc(),
                        ProxyEntry.id.asc(),
                    )
                ).all()
                config["local_proxies"] = [row[0] for row in rows]

            return config

    def choose_proxy(
        self,
        *,
        config: Optional[Dict[str, Any]] = None,
        seed: Optional[int] = None,
    ) -> Optional[str]:
        config = config if config is not None else self.load_config()
        if not config or not config.get("enabled"):
            return None

        proxies = self.get_candidates(config)
        if not proxies:
            return None

        strategy = config.get("strategy") or "random"
        if strategy == "round_robin" and seed is not None:
            return proxies[seed % len(proxies)]
        return random.choice(proxies)

    def apply_to_session(
        self,
        session,
        *,
        config: Optional[Dict[str, Any]] = None,
        seed: Optional[int] = None,
    ) -> Optional[str]:
        proxy = self.choose_proxy(config=config, seed=seed)
        if proxy:
            session.proxies.update({"http": proxy, "https": proxy})
        return proxy

    def get_candidates(self, config: Dict[str, Any]) -> List[str]:
        source = config.get("source") or "manual"
        default_scheme = str(config.get("scdn_protocol") or "http")

        if source == "local":
            proxies = list(config.get("local_proxies") or [])
            if proxies:
                return proxies
            logger.warning("本地代理池没有 active 代理，回退到手动代理列表")

        if source == "scdn":
            proxies = self.load_scdn_proxies(config)
            if proxies:
                return proxies
            logger.warning("SCDN 代理池为空或拉取失败，回退到手动代理列表")

        return parse_proxy_lines_with_scheme(
            str(config.get("proxies") or ""),
            default_scheme=default_scheme,
        )

    def load_scdn_proxies(self, config: Dict[str, Any]) -> List[str]:
        url = self.build_scdn_url(config)
        protocol = str(config.get("scdn_protocol") or "http").lower()
        cache_seconds = _as_int(config.get("scdn_cache_seconds"), 300)
        cache_key = f"{url}|{protocol}"

        now = time.time()
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and cache_seconds > 0 and now - cached[0] < cache_seconds:
                return cached[1]

        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
        except Exception as exc:
            logger.warning("拉取 SCDN 代理池失败: {}", exc)
            return []

        proxies = parse_proxy_response(response.text, default_scheme=protocol)
        with self._lock:
            self._cache[cache_key] = (now, proxies)
        return proxies

    @staticmethod
    def build_scdn_url(config: Dict[str, Any]) -> str:
        raw_url = str(config.get("scdn_url") or "https://proxy.scdn.io/text.php").strip()
        if raw_url.endswith("/"):
            raw_url = raw_url.rstrip("/") + "/text.php"

        parsed = urlparse(raw_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query.setdefault("type", str(config.get("scdn_protocol") or "http"))

        country = str(config.get("scdn_country") or "").strip()
        if country:
            query.setdefault("country", country)

        quantity = _as_int(config.get("scdn_quantity"), 50)
        if quantity > 0:
            query.setdefault("quantity", str(quantity))

        return urlunparse(parsed._replace(query=urlencode(query)))

    @staticmethod
    def mask_proxy(proxy: str) -> str:
        if "@" not in proxy:
            return proxy
        scheme, rest = proxy.split("://", 1) if "://" in proxy else ("", proxy)
        host = rest.rsplit("@", 1)[-1]
        return f"{scheme + '://' if scheme else ''}***:***@{host}"


proxy_selector = ProxySelector()
