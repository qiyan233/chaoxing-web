# -*- coding: utf-8 -*-
"""任务进度事件总线

后台线程向 ``ProgressBus.publish(task_id, event)`` 推事件；
FastAPI SSE 端点通过 ``ProgressBus.subscribe(task_id)`` 拿到一个 asyncio.Queue
做实时转发。

使用 asyncio.Queue + 主事件循环引用，保证后台线程能跨线程派发事件到协程。
"""
from __future__ import annotations

import asyncio
import threading
from collections import defaultdict
from typing import AsyncIterator, Dict, List, Optional


class ProgressBus:
    """任务级 pub/sub 事件总线（线程安全）"""

    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers: Dict[int, List[asyncio.Queue]] = defaultdict(list)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """FastAPI 启动时调用，捕获主事件循环引用"""
        self._loop = loop

    # ---------- 订阅端 (协程) ----------
    async def subscribe(self, task_id: int) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        with self._lock:
            self._subscribers[task_id].append(queue)
        return queue

    async def unsubscribe(self, task_id: int, queue: asyncio.Queue) -> None:
        with self._lock:
            if task_id in self._subscribers:
                try:
                    self._subscribers[task_id].remove(queue)
                except ValueError:
                    pass
                if not self._subscribers[task_id]:
                    del self._subscribers[task_id]

    async def stream(self, task_id: int) -> AsyncIterator[dict]:
        """便捷封装：作为异步生成器使用"""
        queue = await self.subscribe(task_id)
        try:
            while True:
                event = await queue.get()
                yield event
                if event.get("type") in {"task_done", "task_failed", "task_cancelled"}:
                    break
        finally:
            await self.unsubscribe(task_id, queue)

    # ---------- 发布端 (任意线程) ----------
    def publish(self, task_id: int, event: dict) -> None:
        """从任意线程发布事件到该 task 的所有订阅者"""
        with self._lock:
            queues = list(self._subscribers.get(task_id, []))

        if not queues or self._loop is None or self._loop.is_closed():
            return

        for q in queues:
            try:
                self._loop.call_soon_threadsafe(self._safe_put, q, event)
            except RuntimeError:
                # 事件循环已关闭
                continue

    @staticmethod
    def _safe_put(queue: asyncio.Queue, event: dict) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            # 队列满则丢弃最旧的，给新事件让位
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass


# 全局单例
progress_bus = ProgressBus()
