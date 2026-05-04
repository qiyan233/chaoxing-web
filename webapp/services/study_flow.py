# -*- coding: utf-8 -*-
"""Web 端学习任务流程。

这里保留原 CLI 中可复用的任务点处理逻辑，但不引入命令行参数、
配置文件读取或交互输入，保证新项目是纯 Web 入口。
"""
from __future__ import annotations

import contextvars
import enum
import threading
from concurrent.futures.thread import ThreadPoolExecutor
from typing import Any

from api.base import Chaoxing, StudyResult
from api.live import Live
from api.live_process import LiveProcessor
from api.logger import logger


class ChapterResult(enum.Enum):
    SUCCESS = 0
    ERROR = 1
    NOT_OPEN = 2
    PENDING = 3


def process_job(
    chaoxing: Chaoxing,
    course: dict[str, Any],
    job: dict[str, Any],
    job_info: dict[str, Any],
    speed: float,
) -> StudyResult:
    """处理单个任务点。"""
    if job["type"] == "video":
        logger.trace(f"识别到视频任务, 任务章节: {course['title']} 任务ID: {job['jobid']}")
        video_result = chaoxing.study_video(course, job, job_info, _speed=speed, _type="Video")
        if video_result.is_failure():
            logger.warning("当前任务非视频任务, 正在尝试音频任务解码")
            video_result = chaoxing.study_video(course, job, job_info, _speed=speed, _type="Audio")
        if video_result.is_failure():
            logger.warning(
                f"出现异常任务 -> 任务章节: {course['title']} 任务ID: {job['jobid']}, 已跳过"
            )
        return video_result

    if job["type"] == "document":
        logger.trace(f"识别到文档任务, 任务章节: {course['title']} 任务ID: {job['jobid']}")
        return chaoxing.study_document(course, job)

    if job["type"] == "workid":
        logger.trace(f"识别到章节检测任务, 任务章节: {course['title']}")
        return chaoxing.study_work(course, job, job_info)

    if job["type"] == "read":
        logger.trace(f"识别到阅读任务, 任务章节: {course['title']}")
        return chaoxing.study_read(course, job, job_info)

    if job["type"] == "live":
        logger.trace(f"识别到直播任务, 任务章节: {course['title']} 任务ID: {job['jobid']}")
        try:
            defaults = {
                "userid": chaoxing.get_uid(),
                "clazzId": course.get("clazzId"),
                "knowledgeid": job_info.get("knowledgeid"),
            }
            live = Live(
                attachment=job,
                defaults=defaults,
                course_id=course.get("courseId"),
            )
            thread = threading.Thread(
                target=LiveProcessor.run_live,
                args=(live, speed),
                daemon=True,
            )
            thread.start()
            thread.join()
            return StudyResult.SUCCESS
        except Exception as exc:
            logger.error(f"处理直播任务时出错: {exc}")
            return StudyResult.ERROR

    logger.error(f"未知任务类型: {job['type']}")
    return StudyResult.ERROR


def process_chapter(
    chaoxing: Chaoxing,
    course: dict[str, Any],
    point: dict[str, Any],
    speed: float,
) -> ChapterResult:
    """处理单个章节。"""
    logger.info(f'当前章节: {point["title"]}')
    if point["has_finished"]:
        logger.info(f'章节：{point["title"]} 已完成所有任务点')
        return ChapterResult.SUCCESS

    chaoxing.rate_limiter.limit_rate(random_time=True, random_min=0, random_max=0.2)
    jobs, job_info = chaoxing.get_job_list(course, point)

    if job_info.get("notOpen", False):
        return ChapterResult.NOT_OPEN

    job_contexts = [(job, contextvars.copy_context()) for job in jobs]

    def _run_in_context(item: tuple[dict[str, Any], contextvars.Context]) -> StudyResult:
        job, ctx = item
        return ctx.run(process_job, chaoxing, course, job, job_info, speed)

    job_results: list[StudyResult] = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        for result in executor.map(_run_in_context, job_contexts):
            job_results.append(result)

    for result in job_results:
        if result.is_failure():
            return ChapterResult.ERROR

    return ChapterResult.SUCCESS
