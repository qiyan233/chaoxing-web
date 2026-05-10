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
    ProxyBulkActionRequest,
    ProxyBulkDeleteResponse,
    ProxyBulkTestRequest,
    ProxyBulkTestResponse,
    ProxyResponse,
    ScdnProxyImportRequest,
    ProxyTestRequest,
    ProxyTestResult,
)
from webapp.services.proxy_pool import fetch_scdn_proxies, normalize_proxy, parse_proxy_lines, test_proxy

router = APIRouter(prefix="/api/proxies", tags=["proxies"], dependencies=[Depends(require_admin)])


async def _test_and_upsert(
    *,
    db: AsyncSession,
    raw_proxy: str,
    test_mode: str,
    test_url: str,
    timeout_seconds: float,
    default_scheme: str = "http",
) -> tuple[ProxyEntry | None, ProxyTestResult]:
    result = await run_in_threadpool(
        test_proxy,
        raw_proxy,
        test_mode=test_mode,
        test_url=test_url,
        timeout_seconds=timeout_seconds,
        default_scheme=default_scheme,
    )
    test_result = ProxyTestResult(**result)
    if not test_result.ok:
        return None, test_result

    proxy_url = normalize_proxy(raw_proxy, default_scheme=default_scheme)
    existing_result = await db.execute(select(ProxyEntry).where(ProxyEntry.proxy_url == proxy_url))
    entry = existing_result.scalar_one_or_none()
    if entry is None:
        entry = ProxyEntry(proxy_url=proxy_url)
        db.add(entry)

    entry.status = "active"
    entry.latency_ms = test_result.latency_ms
    entry.last_tested_at = datetime.utcnow()
    entry.fail_reason = None
    return entry, test_result


@router.get("", response_model=List[ProxyResponse])
async def list_proxies(db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(select(ProxyEntry).order_by(ProxyEntry.id.desc()))
    return list(result.scalars().all())


@router.post("/test", response_model=ProxyTestResult)
async def test_proxy_once(payload: ProxyTestRequest):
    result = await run_in_threadpool(
        test_proxy,
        payload.proxy_url,
        test_mode=payload.test_mode,
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
        entry, test_result = await _test_and_upsert(
            db=db,
            raw_proxy=raw_proxy,
            test_mode=payload.test_mode,
            test_url=payload.test_url,
            timeout_seconds=payload.timeout_seconds,
        )
        if entry is None:
            failed.append(test_result)
            continue
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


@router.post("/import/scdn", response_model=ProxyAddResponse, status_code=status.HTTP_201_CREATED)
async def import_scdn_proxies(
    payload: ScdnProxyImportRequest,
    db: AsyncSession = Depends(get_db_session),
):
    try:
        raw_proxies = await run_in_threadpool(
            fetch_scdn_proxies,
            protocol=payload.protocol,
            count=payload.count,
            country_code=payload.country_code,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"SCDN 拉取失败: {exc}")

    if not raw_proxies:
        return ProxyAddResponse(status=False, msg="SCDN 未返回代理", added=[], failed=[])

    # all 返回的 host:port 无法知道具体协议；按项目首选 HTTP 测试，失败即过滤。
    default_scheme = "http" if payload.protocol == "all" else payload.protocol
    added: List[ProxyEntry] = []
    failed: List[ProxyTestResult] = []

    for raw_proxy in raw_proxies:
        entry, test_result = await _test_and_upsert(
            db=db,
            raw_proxy=raw_proxy,
            test_mode=payload.test_mode,
            test_url=payload.test_url,
            timeout_seconds=payload.timeout_seconds,
            default_scheme=default_scheme,
        )
        if entry is None:
            failed.append(test_result)
            continue
        added.append(entry)

    await db.commit()
    for entry in added:
        await db.refresh(entry)

    return ProxyAddResponse(
        status=bool(added),
        msg=(
            f"SCDN 返回 {len(raw_proxies)} 个代理，"
            f"新增/更新 {len(added)} 个可用代理，过滤 {len(failed)} 个不可用代理"
        ),
        added=[ProxyResponse.model_validate(entry) for entry in added],
        failed=failed,
    )


@router.post("/bulk/test", response_model=ProxyBulkTestResponse)
async def bulk_test_proxies(
    payload: ProxyBulkTestRequest,
    db: AsyncSession = Depends(get_db_session),
):
    if not payload.ids:
        raise HTTPException(status_code=400, detail="请选择要测试的代理")

    result = await db.execute(select(ProxyEntry).where(ProxyEntry.id.in_(payload.ids)))
    entries = list(result.scalars().all())
    if not entries:
        raise HTTPException(status_code=404, detail="没有找到可测试的代理")

    results: List[ProxyTestResult] = []
    passed = 0
    failed = 0
    for entry in entries:
        test_result_raw = await run_in_threadpool(
            test_proxy,
            entry.proxy_url,
            test_mode=payload.test_mode,
            test_url=payload.test_url,
            timeout_seconds=payload.timeout_seconds,
        )
        test_result = ProxyTestResult(**test_result_raw)
        results.append(test_result)

        entry.last_tested_at = datetime.utcnow()
        entry.latency_ms = test_result.latency_ms
        if test_result.ok:
            entry.status = "active"
            entry.fail_reason = None
            passed += 1
        else:
            entry.status = "failed"
            entry.fail_reason = test_result.error
            failed += 1

    await db.commit()
    return ProxyBulkTestResponse(
        status=True,
        msg=f"已测试 {len(entries)} 个代理，通过 {passed} 个，失败 {failed} 个",
        tested=len(entries),
        passed=passed,
        failed=failed,
        results=results,
    )


@router.post("/bulk/delete", response_model=ProxyBulkDeleteResponse)
async def bulk_delete_proxies(
    payload: ProxyBulkActionRequest,
    db: AsyncSession = Depends(get_db_session),
):
    stmt = select(ProxyEntry)
    if payload.ids:
        stmt = stmt.where(ProxyEntry.id.in_(payload.ids))
    elif payload.status:
        stmt = stmt.where(ProxyEntry.status == payload.status)
    else:
        raise HTTPException(status_code=400, detail="请选择要删除的代理或状态")

    result = await db.execute(stmt)
    entries = list(result.scalars().all())
    for entry in entries:
        await db.delete(entry)
    await db.commit()
    return ProxyBulkDeleteResponse(
        status=True,
        msg=f"已删除 {len(entries)} 个代理",
        deleted=len(entries),
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

    test_mode = payload.test_mode if payload else "chaoxing"
    test_url = payload.test_url if payload else ""
    timeout_seconds = payload.timeout_seconds if payload else 8.0
    result = await run_in_threadpool(
        test_proxy,
        entry.proxy_url,
        test_mode=test_mode,
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
