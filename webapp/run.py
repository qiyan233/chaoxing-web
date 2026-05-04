# -*- coding: utf-8 -*-
"""Web 端启动入口

使用：
    python -m webapp.run                    # 默认 0.0.0.0:3000
    python -m webapp.run --host 127.0.0.1 --port 8080
    python -m webapp.run --reload           # 开发模式（自动重载）
"""
import argparse
import os
import sys
from pathlib import Path

# 让模块能直接 python -m webapp.run 运行
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description="超星学习通自动化平台 Web 端")
    parser.add_argument("--host", default=os.getenv("CHAOXING_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("CHAOXING_PORT", "3000")))
    parser.add_argument("--reload", action="store_true", help="开发模式自动重载")
    parser.add_argument("--workers", type=int, default=1, help="工作进程数（reload 时强制 1）")
    parser.add_argument("--debug", action="store_true", help="启用调试日志")
    args = parser.parse_args()

    if args.debug:
        os.environ["CHAOXING_DEBUG"] = "true"

    import uvicorn

    print(f"\n{'=' * 60}")
    print(f"  超星学习通自动化平台 Web 端")
    print(f"  访问地址: http://{args.host if args.host != '0.0.0.0' else 'localhost'}:{args.port}")
    print(f"  数据目录: {ROOT / 'data'}")
    print(f"{'=' * 60}\n")

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
