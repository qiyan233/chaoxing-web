# -*- coding: utf-8 -*-
"""快速通过模式

直接以 ``playingTime = duration`` 一次性命中 ``multimedia/log/a`` 接口，
让服务端把视频/音频任务点判定为 ``isPassed=True``，从而跳过真实播放等待。

适用范围：
- 视频 / 音频任务点：走快路径
- 文档 / 阅读 / 章节检测 / 直播：回退到 ``study_flow.process_job`` 的常规逻辑

风控应对：
- ``rt=0.9`` (-rt_d) 防拖动 + ``videoFaceCaptureEnc`` 强制人脸抓拍
  + ``attDurationEnc`` 注意力时长校验：服务端要求 playingTime 严格连续，
  硬伪造满时长必 403。这种章节直接降级到 ``study_video`` 正常模式，
  让它走 ``_recover_after_forbidden`` 自己兜底
- 普通章节：``playingTime=duration`` 一把过
"""
from __future__ import annotations

import contextvars
import re
from concurrent.futures.thread import ThreadPoolExecutor
from typing import Any

from api.base import Chaoxing, StudyResult, SessionManager
from api.config import GlobalConst as gc
from api.logger import logger

from webapp.services.study_flow import ChapterResult, process_job


# ---------- 风控特征检测 ----------

def _is_anti_drag(job: dict[str, Any]) -> bool:
    """检测是否是 rt=0.9 (-rt_d) 防拖动课程"""
    rt = str(job.get("rt") or "")
    if rt:
        try:
            if float(rt) < 1.0:
                return True
        except ValueError:
            pass
    other = job.get("otherinfo", "") or ""
    return bool(re.search(r"-rt_d", other))


def _has_face_capture(job: dict[str, Any]) -> bool:
    """检测是否启用人脸抓拍"""
    return bool(job.get("videoFaceCaptureEnc"))


def _has_attention_check(job: dict[str, Any]) -> bool:
    """检测是否启用注意力时长校验"""
    return bool(job.get("attDurationEnc"))


def _risk_tags(job: dict[str, Any]) -> list[str]:
    tags = []
    if _is_anti_drag(job):
        tags.append("rt=0.9")
    if _has_face_capture(job):
        tags.append("face")
    if _has_attention_check(job):
        tags.append("att")
    return tags


def _is_high_risk(job: dict[str, Any]) -> bool:
    """高风控章节：rt=0.9 且伴随人脸抓拍 / 注意力校验。

    这类章节服务端要求 playingTime 严格连续 + 真实抓拍 token，
    硬伪造满时长必 403，且渐进式上报耗时与正常模式接近，没收益。
    """
    return _is_anti_drag(job) and (_has_face_capture(job) or _has_attention_check(job))


# ---------- 任务点入口 ----------

def quick_study_video(
    chaoxing: Chaoxing,
    course: dict[str, Any],
    job: dict[str, Any],
    job_info: dict[str, Any],
    _type: str = "Video",
) -> StudyResult:
    """快速模式视频/音频任务点：仅普通章节走快路径，高风控章节返回 ERROR 让上层降级"""
    if _is_high_risk(job):
        logger.info(
            "[quick] 跳过快路径(高风控): {} | 标签={}",
            job.get("name"),
            ",".join(_risk_tags(job)),
        )
        return StudyResult.ERROR

    session = SessionManager.get_session()
    headers = gc.VIDEO_HEADERS if _type == "Video" else gc.AUDIO_HEADERS

    info_url = (
        f"https://mooc1.chaoxing.com/ananas/status/{job['objectid']}"
        f"?k={chaoxing.get_fid()}&flag=normal"
    )
    try:
        info = session.get(info_url, headers=headers).json()
    except Exception as exc:
        logger.error(f"[quick] 获取视频元信息失败: {exc}")
        return StudyResult.ERROR

    if info.get("status") != "success":
        logger.warning(f"[quick] 视频元信息状态异常: {info.get('status')}")
        return StudyResult.ERROR

    dtoken = info["dtoken"]
    duration = int(info["duration"])
    play_time_start = int(job.get("playTime", 0) or 0) // 1000

    risk = _risk_tags(job)
    logger.info(
        "[quick] {} | duration={}s start={}s 风控={}",
        job.get("name"),
        duration,
        play_time_start,
        ",".join(risk) if risk else "none",
    )

    # 起始心跳：用 job 的续播位置（而非硬编 0），更接近真实浏览器行为
    chaoxing.video_progress_log(
        session, course, job, job_info, dtoken,
        duration, play_time_start, _type, headers=headers,
    )
    # 直接 playingTime = duration，触发 isPassed
    passed, state = chaoxing.video_progress_log(
        session, course, job, job_info, dtoken,
        duration, duration, _type, headers=headers,
    )

    if passed:
        logger.success(f"[quick] 任务点闪电完成: {job.get('name')}")
        return StudyResult.SUCCESS

    if state == 403:
        logger.warning(f"[quick] 触发 403, 让上层降级: {job.get('name')}")
        return StudyResult.FORBIDDEN

    logger.warning(
        "[quick] 服务端未确认通过 (state={}): {}",
        state,
        job.get("name"),
    )
    return StudyResult.ERROR


def quick_process_job(
    chaoxing: Chaoxing,
    course: dict[str, Any],
    job: dict[str, Any],
    job_info: dict[str, Any],
    speed: float,
) -> StudyResult:
    """快速模式版的 process_job"""
    job_type = job.get("type")

    if job_type == "video":
        result = quick_study_video(chaoxing, course, job, job_info, _type="Video")
        if result == StudyResult.ERROR and not _is_high_risk(job):
            # 普通章节但元数据失败：换音频通道再试一次
            audio_result = quick_study_video(chaoxing, course, job, job_info, _type="Audio")
            if not audio_result.is_failure():
                return audio_result
            result = audio_result

        if result.is_failure():
            # 失败兜底：降级到完整正常模式（含 _recover_after_forbidden 等机制）
            logger.warning(
                f"[quick] 降级到正常模式 ({result.name}): {job.get('name')}"
            )
            return chaoxing.study_video(course, job, job_info, _speed=speed, _type="Video")
        return result

    # 文档 / 章测 / 阅读 / 直播：直接复用正常流水
    return process_job(chaoxing, course, job, job_info, speed)


def quick_process_chapter(
    chaoxing: Chaoxing,
    course: dict[str, Any],
    point: dict[str, Any],
    speed: float,
) -> ChapterResult:
    """快速模式版的 process_chapter"""
    logger.info(f'[quick] 当前章节: {point["title"]}')
    if point["has_finished"]:
        logger.info(f'[quick] 章节：{point["title"]} 已完成所有任务点')
        return ChapterResult.SUCCESS

    chaoxing.rate_limiter.limit_rate(random_time=True, random_min=0, random_max=0.2)
    jobs, job_info = chaoxing.get_job_list(course, point)

    if job_info.get("notOpen", False):
        return ChapterResult.NOT_OPEN

    job_contexts = [(job, contextvars.copy_context()) for job in jobs]

    def _run_in_context(item: tuple[dict[str, Any], contextvars.Context]) -> StudyResult:
        job, ctx = item
        return ctx.run(quick_process_job, chaoxing, course, job, job_info, speed)

    job_results: list[StudyResult] = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        for result in executor.map(_run_in_context, job_contexts):
            job_results.append(result)

    for result in job_results:
        if result.is_failure():
            return ChapterResult.ERROR

    return ChapterResult.SUCCESS
