# -*- coding: utf-8 -*-
"""本地代理池管理 API"""
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from webapp.deps import get_db_session, require_admin
from webapp.models.proxy import ProxyEntry
from webapp.schemas.proxy import (
    ProxyAddRequest,
    ProxyAddResponse,
    ProxyResponse,
    ProxyTestRequest,
    ProxyTestResult,
)
from webapp.services.proxy_pool import normalize_proxy, parse_proxy_lines, test_proxy

router = APIRouter(prefix="/api/proxies", tags=["proxies"], dependencies=[Depends(require_admin)])


@router.get("", response_model=List[ProxyResponse])
async def list_proxies(db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(select(ProxyEntry).order_by(ProxyEntry.id.desc()))
    return list(result.scalars().all())


@router.post("/test", response_model=ProxyTestResult)
async def test_proxy_once(payload: ProxyTestRequest):
    result = await run_in_threadpool(
        test_proxy,
        payload.proxy_url,
        test_url=payload.test_url,
        timeout_seconds=payload.timeout_seconds,
    )
    return ProxyTestResult(**result)


@router.post("", response_model=ProxyAddResponse, status_code=status.HTTP_201_CREATED)
async def add_proxy(
    payload: ProxyAddRequest,
    db: AsyncSession = Depends(get_db_session),
):
    raw_proxies = parse_proxy_lines(payload.proxy_url)
    if not raw_proxies:
        raise HTTPException(status_code=400, detail="请输入代理地址")

    added: List[ProxyEntry] = []
    failed: List[ProxyTestResult] = []

    for raw_proxy in raw_proxies:
        result = await run_in_threadpool(
            test_proxy,
            raw_proxy,
            test_url=payload.test_url,
            timeout_seconds=payload.timeout_seconds,
        )
        test_result = ProxyTestResult(**result)
        if not test_result.ok:
            failed.append(test_result)
            continue

        proxy_url = normalize_proxy(raw_proxy)
        existing_result = await db.execute(select(ProxyEntry).where(ProxyEntry.proxy_url == proxy_url))
        entry = existing_result.scalar_one_or_none()
        if entry is None:
            entry = ProxyEntry(proxy_url=proxy_url)
            db.add(entry)

        entry.status = "active"
        entry.latency_ms = test_result.latency_ms
        entry.last_tested_at = datetime.utcnow()
        entry.fail_reason = None
        added.append(entry)

    await db.commit()
    for entry in added:
        await db.refresh(entry)

    return ProxyAddResponse(
        status=bool(added),
        msg=f"新增/更新 {len(added)} 个可用代理，过滤 {len(failed)} 个不可用代理",
        added=[ProxyResponse.model_validate(entry) for entry in added],
        failed=failed,
    )


@router.post("/{proxy_id}/test", response_model=ProxyTestResult)
async def retest_proxy(
    proxy_id: int,
    payload: ProxyTestRequest | None = None,
    db: AsyncSession = Depends(get_db_session),
):
    entry = await db.get(ProxyEntry, proxy_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="代理不存在")

    test_url = payload.test_url if payload else "http://httpbin.org/ip"
    timeout_seconds = payload.timeout_seconds if payload else 8.0
    result = await run_in_threadpool(
        test_proxy,
        entry.proxy_url,
        test_url=test_url,
        timeout_seconds=timeout_seconds,
    )
    test_result = ProxyTestResult(**result)

    entry.last_tested_at = datetime.utcnow()
    entry.latency_ms = test_result.latency_ms
    if test_result.ok:
        entry.status = "active"
        entry.fail_reason = None
    else:
        entry.status = "failed"
        entry.fail_reason = test_result.error

    await db.commit()
    return test_result


@router.post("/{proxy_id}/enable", response_model=ProxyResponse)
async def enable_proxy(proxy_id: int, db: AsyncSession = Depends(get_db_session)):
    entry = await db.get(ProxyEntry, proxy_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="代理不存在")
    entry.status = "active"
    await db.commit()
    await db.refresh(entry)
    return entry


@router.post("/{proxy_id}/disable", response_model=ProxyResponse)
async def disable_proxy(proxy_id: int, db: AsyncSession = Depends(get_db_session)):
    entry = await db.get(ProxyEntry, proxy_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="代理不存在")
    entry.status = "disabled"
    await db.commit()
    await db.refresh(entry)
    return entry


@router.delete("/{proxy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_proxy(proxy_id: int, db: AsyncSession = Depends(get_db_session)):
    entry = await db.get(ProxyEntry, proxy_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="代理不存在")
    await db.delete(entry)
    await db.commit()
