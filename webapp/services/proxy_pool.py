# -*- coding: utf-8 -*-
"""代理池工具：格式化、测速、文本解析"""
from __future__ import annotations

import json
import time
from typing import Any, List, Optional

import requests


def normalize_proxy(proxy: str, default_scheme: str = "http") -> str:
    proxy = (proxy or "").strip()
    if not proxy:
        return ""
    if "://" in proxy:
        return proxy
    return f"{default_scheme}://{proxy}"


def parse_proxy_lines(proxy_text: str) -> List[str]:
    proxies: List[str] = []
    for line in (proxy_text or "").splitlines():
        proxy = line.strip()
        if not proxy or proxy.startswith("#"):
            continue
        proxies.append(proxy)
    return proxies


def parse_proxy_lines_with_scheme(proxy_text: str, *, default_scheme: str = "http") -> List[str]:
    return [
        normalized
        for proxy in parse_proxy_lines(proxy_text)
        if (normalized := normalize_proxy(proxy, default_scheme))
    ]


def parse_proxy_response(text: str, *, default_scheme: str = "http") -> List[str]:
    text = (text or "").strip()
    if not text:
        return []

    if text[:1] in "[{":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if data is not None:
            return extract_proxies_from_json(data, default_scheme=default_scheme)

    return parse_proxy_lines_with_scheme(text, default_scheme=default_scheme)


def extract_proxies_from_json(data: Any, *, default_scheme: str) -> List[str]:
    found: List[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            if ":" in value:
                found.extend(parse_proxy_lines_with_scheme(value, default_scheme=default_scheme))
            return
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if isinstance(value, dict):
            host = value.get("host") or value.get("ip") or value.get("address")
            port = value.get("port")
            scheme = value.get("type") or value.get("protocol") or default_scheme
            if host and port:
                found.append(normalize_proxy(f"{host}:{port}", str(scheme)))
            for item in value.values():
                walk(item)

    walk(data)
    return list(dict.fromkeys(found))


def fetch_scdn_proxies(
    *,
    protocol: str = "http",
    count: int = 10,
    country_code: str = "",
    timeout_seconds: float = 15.0,
) -> List[str]:
    """从 SCDN 官方 API 拉取代理列表。"""
    protocol = (protocol or "http").lower()
    params = {
        "protocol": protocol,
        "count": max(1, min(int(count or 1), 20)),
    }
    country_code = (country_code or "").strip().upper()
    if country_code:
        params["country_code"] = country_code

    response = requests.get(
        "https://proxy.scdn.io/api/get_proxy.php",
        params=params,
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 200:
        raise RuntimeError(payload.get("message") or "SCDN API 返回失败")

    proxies = payload.get("data", {}).get("proxies") or []
    if not isinstance(proxies, list):
        return []
    return [str(proxy).strip() for proxy in proxies if str(proxy).strip()]


def test_proxy(
    proxy_url: str,
    *,
    test_url: str = "http://httpbin.org/ip",
    timeout_seconds: float = 8.0,
    default_scheme: str = "http",
) -> dict:
    proxy = normalize_proxy(proxy_url, default_scheme=default_scheme)
    started = time.perf_counter()
    try:
        response = requests.get(
            test_url,
            proxies={"http": proxy, "https": proxy},
            timeout=timeout_seconds,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code != 200:
            return {
                "ok": False,
                "proxy_url": proxy,
                "latency_ms": latency_ms,
                "status_code": response.status_code,
                "error": f"测试地址返回 HTTP {response.status_code}",
            }

        origin: Optional[str] = None
        try:
            payload = response.json()
            origin = payload.get("origin")
        except Exception:
            origin = response.text[:120]

        return {
            "ok": True,
            "proxy_url": proxy,
            "latency_ms": latency_ms,
            "status_code": response.status_code,
            "origin": origin,
        }
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False,
            "proxy_url": proxy,
            "latency_ms": latency_ms,
            "error": f"{type(exc).__name__}: {exc}",
        }
