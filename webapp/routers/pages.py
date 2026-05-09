# -*- coding: utf-8 -*-
"""页面渲染路由（Jinja2 模板）"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from webapp.config import APP_NAME, TEMPLATES_DIR
from webapp.deps import get_db_session, is_admin_initialized

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _ctx(request: Request, **extra) -> dict:
    """构建模板上下文"""
    ctx = {
        "request": request,
        "app_name": APP_NAME,
        "user": request.session.get("user"),
        "role": request.session.get("role") or ("admin" if request.session.get("user") == "admin" else None),
        "is_admin": request.session.get("role") == "admin" or request.session.get("user") == "admin",
        "authenticated": request.session.get("authenticated", False),
    }
    ctx.update(extra)
    return ctx


def _template(request: Request, name: str, **extra):
    """Render a Jinja2 template with the current Starlette signature."""
    return templates.TemplateResponse(request, name, _ctx(request, **extra))


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, db: AsyncSession = Depends(get_db_session)):
    if not await is_admin_initialized(db):
        return RedirectResponse("/setup", status_code=302)
    if not request.session.get("authenticated"):
        return RedirectResponse("/login", status_code=302)
    return RedirectResponse("/dashboard", status_code=302)


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request, db: AsyncSession = Depends(get_db_session)):
    if await is_admin_initialized(db):
        return RedirectResponse("/login", status_code=302)
    return _template(request, "setup.html")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: AsyncSession = Depends(get_db_session)):
    if not await is_admin_initialized(db):
        return RedirectResponse("/setup", status_code=302)
    if request.session.get("authenticated"):
        return RedirectResponse("/dashboard", status_code=302)
    return _template(request, "login.html")


def _require_html_login(request: Request) -> RedirectResponse | None:
    if not request.session.get("authenticated"):
        return RedirectResponse("/login", status_code=302)
    return None


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    redirect = _require_html_login(request)
    if redirect:
        return redirect
    return _template(request, "dashboard.html", page="dashboard")


@router.get("/accounts", response_class=HTMLResponse)
async def accounts_page(request: Request):
    redirect = _require_html_login(request)
    if redirect:
        return redirect
    return _template(request, "accounts.html", page="accounts")


@router.get("/accounts/{account_id}/courses", response_class=HTMLResponse)
async def courses_page(request: Request, account_id: int):
    redirect = _require_html_login(request)
    if redirect:
        return redirect
    return _template(request, "courses.html", page="accounts", account_id=account_id)


@router.get("/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request):
    redirect = _require_html_login(request)
    if redirect:
        return redirect
    return _template(request, "tasks.html", page="tasks")


@router.get("/tasks/{task_id}", response_class=HTMLResponse)
async def task_detail_page(request: Request, task_id: int):
    redirect = _require_html_login(request)
    if redirect:
        return redirect
    return _template(request, "task_detail.html", page="tasks", task_id=task_id)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    redirect = _require_html_login(request)
    if redirect:
        return redirect
    if request.session.get("role") != "admin" and request.session.get("user") != "admin":
        return RedirectResponse("/dashboard", status_code=302)
    return _template(request, "settings.html", page="settings")


@router.get("/proxies", response_class=HTMLResponse)
async def proxies_page(request: Request):
    redirect = _require_html_login(request)
    if redirect:
        return redirect
    if request.session.get("role") != "admin" and request.session.get("user") != "admin":
        return RedirectResponse("/dashboard", status_code=302)
    return _template(request, "proxies.html", page="proxies")


@router.get("/update", response_class=HTMLResponse)
async def update_page(request: Request):
    redirect = _require_html_login(request)
    if redirect:
        return redirect
    if request.session.get("role") != "admin" and request.session.get("user") != "admin":
        return RedirectResponse("/dashboard", status_code=302)
    return _template(request, "update.html", page="update")
