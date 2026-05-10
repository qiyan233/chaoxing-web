# -*- coding: utf-8 -*-
"""代理池工具：格式化、测速、文本解析"""
from __future__ import annotations

import json
import time
from typing import Any, List, Optional

import requests
from requests import RequestException


GENERIC_PROXY_TEST_URL = "http://httpbin.org/ip"
CHAOXING_PROXY_TEST_URL = "https://passport2.chaoxing.com/"


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
    test_url: str = "",
    test_mode: str = "chaoxing",
    timeout_seconds: float = 8.0,
    default_scheme: str = "http",
) -> dict:
    proxy = normalize_proxy(proxy_url, default_scheme=default_scheme)
    mode = (test_mode or "chaoxing").lower()
    url = (test_url or "").strip()
    if not url:
        url = CHAOXING_PROXY_TEST_URL if mode == "chaoxing" else GENERIC_PROXY_TEST_URL

    started = time.perf_counter()
    try:
        response = requests.get(
            url,
            proxies={"http": proxy, "https": proxy},
            timeout=timeout_seconds,
            allow_redirects=False,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)

        if mode == "chaoxing":
            if response.status_code in {403, 429}:
                return {
                    "ok": False,
                    "proxy_url": proxy,
                    "latency_ms": latency_ms,
                    "status_code": response.status_code,
                    "error": f"学习通风控/限流 HTTP {response.status_code}",
                }
            if response.status_code >= 500:
                return {
                    "ok": False,
                    "proxy_url": proxy,
                    "latency_ms": latency_ms,
                    "status_code": response.status_code,
                    "error": f"学习通服务异常 HTTP {response.status_code}",
                }
            if response.status_code not in {200, 301, 302, 303, 307, 308, 404}:
                return {
                    "ok": False,
                    "proxy_url": proxy,
                    "latency_ms": latency_ms,
                    "status_code": response.status_code,
                    "error": f"学习通返回异常 HTTP {response.status_code}",
                }
            return {
                "ok": True,
                "proxy_url": proxy,
                "latency_ms": latency_ms,
                "status_code": response.status_code,
                "origin": "chaoxing-reachable",
            }

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
    except requests.exceptions.TooManyRedirects as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False,
            "proxy_url": proxy,
            "latency_ms": latency_ms,
            "error": f"学习通重定向过多: {exc}" if mode == "chaoxing" else f"重定向过多: {exc}",
        }
    except requests.exceptions.Timeout as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False,
            "proxy_url": proxy,
            "latency_ms": latency_ms,
            "error": f"学习通访问超时: {exc}" if mode == "chaoxing" else f"访问超时: {exc}",
        }
    except RequestException as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        msg = str(exc)
        if "Connection reset" in msg or "ConnectionResetError" in msg:
            msg = "学习通连接被重置" if mode == "chaoxing" else "连接被重置"
        elif "ProxyError" in msg:
            msg = f"代理连接失败: {exc}"
        elif "SSLError" in msg:
            msg = f"TLS/SSL 握手失败: {exc}"
        else:
            msg = f"{type(exc).__name__}: {exc}"
        return {
            "ok": False,
            "proxy_url": proxy,
            "latency_ms": latency_ms,
            "error": msg,
        }
