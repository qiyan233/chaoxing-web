# -*- coding: utf-8 -*-
"""Web 应用配置

环境变量优先级 > .env > 默认值。
首次运行时会自动生成 Fernet 密钥到 data/.secret_key。
"""
import os
import secrets
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet


# 项目根目录（OnlyRead/）
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# SQLite 数据库
DB_PATH = DATA_DIR / "chaoxing.db"
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"
SYNC_DATABASE_URL = f"sqlite:///{DB_PATH}"  # APScheduler / 同步初始化用

# Fernet 密钥（用于加密超星账号密码）
SECRET_KEY_PATH = DATA_DIR / ".secret_key"


def _load_or_create_fernet_key() -> bytes:
    """加载 Fernet 密钥；不存在则自动生成"""
    if SECRET_KEY_PATH.exists():
        return SECRET_KEY_PATH.read_bytes().strip()

    key = Fernet.generate_key()
    SECRET_KEY_PATH.write_bytes(key)
    # Windows 下 chmod 仅做尽力而为
    try:
        os.chmod(SECRET_KEY_PATH, 0o600)
    except OSError:
        pass
    return key


FERNET_KEY = _load_or_create_fernet_key()

# Session cookie 签名密钥（用于浏览器会话）
SESSION_SECRET_PATH = DATA_DIR / ".session_secret"


def _load_or_create_session_secret() -> str:
    if SESSION_SECRET_PATH.exists():
        return SESSION_SECRET_PATH.read_text(encoding="utf-8").strip()
    secret = secrets.token_urlsafe(48)
    SESSION_SECRET_PATH.write_text(secret, encoding="utf-8")
    try:
        os.chmod(SESSION_SECRET_PATH, 0o600)
    except OSError:
        pass
    return secret


SESSION_SECRET = _load_or_create_session_secret()
SESSION_COOKIE_NAME = "chaoxing_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 天

# 应用全局参数
APP_NAME = "超星学习通自动化平台"
APP_HOST = os.getenv("CHAOXING_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("CHAOXING_PORT", "3000"))
DEBUG = os.getenv("CHAOXING_DEBUG", "false").lower() in {"1", "true", "yes"}

# 任务运行时参数
MAX_CONCURRENT_ACCOUNTS = int(os.getenv("CHAOXING_MAX_ACCOUNTS", "1"))  # 同时跑几个账号
COURSE_CACHE_SECONDS = int(os.getenv("CHAOXING_COURSE_CACHE", "300"))   # 课程列表缓存时长

# 模板与静态资源
TEMPLATES_DIR = BASE_DIR / "webapp" / "templates"
STATIC_DIR = BASE_DIR / "webapp" / "static"


def get_admin_password_hash() -> Optional[str]:
    """从环境变量读取管理员密码哈希；返回 None 表示尚未初始化"""
    return os.getenv("CHAOXING_ADMIN_PASSWORD_HASH")
