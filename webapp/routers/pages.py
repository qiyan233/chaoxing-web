# -*- coding: utf-8 -*-
"""页面路由。"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from webapp.config import APP_NAME, TEMPLATES_DIR
from webapp.deps import get_db_session, is_admin_initialized
from webapp.models.account import ChaoxingAccount
from webapp.models.task import StudyTask, TaskStatus
from webapp.models.user import PlatformUser

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _ctx(request: Request, **extra) -> dict:
    role = request.session.get("role") or ("admin" if request.session.get("user") == "admin" else None)
    ctx = {
        "request": request,
        "app_name": APP_NAME,
        "user": request.session.get("user"),
        "role": role,
        "is_admin": role == "admin" or request.session.get("user") == "admin",
        "authenticated": request.session.get("authenticated", False),
    }
    ctx.update(extra)
    return ctx


def _template(request: Request, name: str, **extra):
    return templates.TemplateResponse(request, name, _ctx(request, **extra))


def _home_for_role(request: Request) -> str:
    return "/user/dashboard" if request.session.get("role") == "user" else "/dashboard"


async def _landing_stats(db: AsyncSession) -> dict:
    platform_total = await db.scalar(select(func.count(PlatformUser.id)))
    platform_active = await db.scalar(
        select(func.count(PlatformUser.id)).where(PlatformUser.status == "active")
    )
    chaoxing_total = await db.scalar(select(func.count(ChaoxingAccount.id)))
    chaoxing_bound = await db.scalar(
        select(func.count(ChaoxingAccount.id)).where(ChaoxingAccount.user_id.is_not(None))
    )
    completed_tasks = await db.scalar(
        select(func.count(StudyTask.id)).where(StudyTask.status == TaskStatus.COMPLETED.value)
    )
    completed_chapters = await db.scalar(
        select(func.coalesce(func.sum(StudyTask.done_chapters), 0)).where(
            StudyTask.status == TaskStatus.COMPLETED.value
        )
    )
    return {
        "platform_total": platform_total or 0,
        "platform_active": platform_active or 0,
        "chaoxing_total": chaoxing_total or 0,
        "chaoxing_bound": chaoxing_bound or 0,
        "completed_tasks": completed_tasks or 0,
        "completed_chapters": completed_chapters or 0,
    }


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, db: AsyncSession = Depends(get_db_session)):
    if not await is_admin_initialized(db):
        return RedirectResponse("/setup", status_code=302)
    if request.session.get("authenticated"):
        return RedirectResponse(_home_for_role(request), status_code=302)
    return _template(request, "home.html", landing_stats=await _landing_stats(db))


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request, db: AsyncSession = Depends(get_db_session)):
    if await is_admin_initialized(db):
        return RedirectResponse("/admin/login", status_code=302)
    return _template(request, "setup.html")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: AsyncSession = Depends(get_db_session)):
    if not await is_admin_initialized(db):
        return RedirectResponse("/setup", status_code=302)
    if request.session.get("authenticated"):
        return RedirectResponse(_home_for_role(request), status_code=302)
    return _template(request, "user_login.html")


@router.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request, db: AsyncSession = Depends(get_db_session)):
    if not await is_admin_initialized(db):
        return RedirectResponse("/setup", status_code=302)
    if request.session.get("authenticated"):
        return RedirectResponse(_home_for_role(request), status_code=302)
    return _template(request, "login.html")


def _require_html_login(request: Request) -> RedirectResponse | None:
    if not request.session.get("authenticated"):
        return RedirectResponse("/login", status_code=302)
    return None


def _require_admin_page(request: Request) -> RedirectResponse | None:
    redirect = _require_html_login(request)
    if redirect:
        return redirect
    if request.session.get("role") != "admin" and request.session.get("user") != "admin":
        return RedirectResponse("/user/dashboard", status_code=302)
    return None


@router.get("/user/dashboard", response_class=HTMLResponse)
async def user_dashboard_page(request: Request):
    redirect = _require_html_login(request)
    if redirect:
        return redirect
    return _template(request, "user_dashboard.html", page="user_dashboard")


@router.get("/user/accounts", response_class=HTMLResponse)
async def user_accounts_page(request: Request):
    redirect = _require_html_login(request)
    if redirect:
        return redirect
    if request.session.get("role") != "user":
        return RedirectResponse("/dashboard", status_code=302)
    return _template(request, "user_accounts.html", page="user_accounts")


@router.get("/user/accounts/{account_id}/courses", response_class=HTMLResponse)
async def user_courses_page(request: Request, account_id: int):
    redirect = _require_html_login(request)
    if redirect:
        return redirect
    if request.session.get("role") != "user":
        return RedirectResponse(f"/accounts/{account_id}/courses", status_code=302)
    return _template(request, "courses.html", page="user_accounts", account_id=account_id, user_scope=True)


@router.get("/user/tasks", response_class=HTMLResponse)
async def user_tasks_page(request: Request):
    redirect = _require_html_login(request)
    if redirect:
        return redirect
    if request.session.get("role") != "user":
        return RedirectResponse("/tasks", status_code=302)
    return _template(request, "user_tasks.html", page="user_tasks")


@router.get("/user/tasks/{task_id}", response_class=HTMLResponse)
async def user_task_detail_page(request: Request, task_id: int):
    redirect = _require_html_login(request)
    if redirect:
        return redirect
    if request.session.get("role") != "user":
        return RedirectResponse(f"/tasks/{task_id}", status_code=302)
    return _template(request, "task_detail.html", page="user_tasks", task_id=task_id, user_scope=True)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    redirect = _require_admin_page(request)
    if redirect:
        return redirect
    return _template(request, "dashboard.html", page="dashboard")


@router.get("/accounts", response_class=HTMLResponse)
async def accounts_page(request: Request):
    redirect = _require_admin_page(request)
    if redirect:
        return redirect
    return _template(request, "accounts.html", page="accounts")


@router.get("/accounts/{account_id}/courses", response_class=HTMLResponse)
async def courses_page(request: Request, account_id: int):
    redirect = _require_admin_page(request)
    if redirect:
        return redirect
    return _template(request, "courses.html", page="accounts", account_id=account_id)


@router.get("/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request):
    redirect = _require_admin_page(request)
    if redirect:
        return redirect
    return _template(request, "tasks.html", page="tasks")


@router.get("/tasks/{task_id}", response_class=HTMLResponse)
async def task_detail_page(request: Request, task_id: int):
    redirect = _require_admin_page(request)
    if redirect:
        return redirect
    return _template(request, "task_detail.html", page="tasks", task_id=task_id)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    redirect = _require_admin_page(request)
    if redirect:
        return redirect
    return _template(request, "settings.html", page="settings")


@router.get("/users", response_class=HTMLResponse)
async def users_page(request: Request):
    redirect = _require_admin_page(request)
    if redirect:
        return redirect
    return _template(request, "users.html", page="users")


@router.get("/proxies", response_class=HTMLResponse)
async def proxies_page(request: Request):
    redirect = _require_admin_page(request)
    if redirect:
        return redirect
    return _template(request, "proxies.html", page="proxies")


@router.get("/update", response_class=HTMLResponse)
async def update_page(request: Request):
    redirect = _require_admin_page(request)
    if redirect:
        return redirect
    return _template(request, "update.html", page="update")
