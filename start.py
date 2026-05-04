# -*- coding: utf-8 -*-
"""一键启动超星学习通 Web 控制台。

推荐：
    uv run python start.py

也兼容：
    python start.py
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启动超星学习通自动化平台 Web 端")
    parser.add_argument("--host", default=os.getenv("CHAOXING_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("CHAOXING_PORT", "3000")))
    parser.add_argument("--reload", action="store_true", help="开发模式自动重载")
    parser.add_argument("--workers", type=int, default=1, help="工作进程数（reload 时强制 1）")
    parser.add_argument("--debug", action="store_true", help="启用调试日志")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.debug:
        os.environ["CHAOXING_DEBUG"] = "true"

    try:
        import uvicorn
    except ModuleNotFoundError:
        print("缺少依赖 uvicorn。请先执行：uv sync", file=sys.stderr)
        raise SystemExit(1)

    display_host = "localhost" if args.host == "0.0.0.0" else args.host
    print()
    print("=" * 60)
    print("  超星学习通自动化平台 Web 端")
    print(f"  访问地址: http://{display_host}:{args.port}")
    print(f"  数据目录: {ROOT / 'data'}")
    print("  按 Ctrl+C 停止服务")
    print("=" * 60)
    print()

    uvicorn.run(
        "webapp.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=1 if args.reload else args.workers,
        log_level="debug" if args.debug else "info",
    )


if __name__ == "__main__":
    main()
