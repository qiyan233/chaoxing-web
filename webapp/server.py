# -*- coding: utf-8 -*-
"""FastAPI 应用入口"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from webapp.config import (
    APP_NAME,
    DEBUG,
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
    SESSION_SECRET,
    STATIC_DIR,
)
from webapp.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化 DB、TaskRunner；关闭时清理"""
    import asyncio

    await init_db()

    # 让 ProgressBus 持有主事件循环引用，便于跨线程派发事件
    from webapp.services.progress_bus import progress_bus
    progress_bus.attach_loop(asyncio.get_running_loop())

    # 启动 TaskRunner
    try:
        from webapp.services.task_runner import task_runner
        task_runner.start()
    except ImportError:
        pass

    yield

    try:
        from webapp.services.task_runner import task_runner
        task_runner.shutdown()
    except ImportError:
        pass


def create_app() -> FastAPI:
    app = FastAPI(
        title=APP_NAME,
        description="超星学习通自动化任务平台",
        version="1.0.0",
        debug=DEBUG,
        lifespan=lifespan,
    )

    app.add_middleware(
        SessionMiddleware,
        secret_key=SESSION_SECRET,
        session_cookie=SESSION_COOKIE_NAME,
        max_age=SESSION_MAX_AGE,
        same_site="lax",
        https_only=False,
    )

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # 路由注册（P4 阶段实现）
    try:
        from webapp.routers import auth, accounts, proxies, tasks, settings as settings_router, pages, stream, update

        app.include_router(pages.router)
        app.include_router(auth.router)
        app.include_router(accounts.router)
        app.include_router(proxies.router)
        app.include_router(tasks.router)
        app.include_router(settings_router.router)
        app.include_router(stream.router)
        app.include_router(update.router)
    except ImportError:
        pass

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        return {"status": "ok"}

    return app


app = create_app()
