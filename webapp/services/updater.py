# -*- coding: utf-8 -*-
"""GitHub 在线更新服务"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List

from webapp.config import BASE_DIR
from webapp.schemas.update import UpdateApplyResult, UpdateStatus


class Updater:
    """基于 git + uv 的轻量在线更新器。"""

    def __init__(self, repo_dir: Path = BASE_DIR):
        self.repo_dir = Path(repo_dir)

    def _run(self, args: List[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            cwd=str(self.repo_dir),
            text=True,
            capture_output=True,
            timeout=timeout,
            shell=False,
        )

    @staticmethod
    def _short(text: str, limit: int = 4000) -> str:
        text = text or ""
        return text if len(text) <= limit else text[-limit:]

    def _git_ok(self) -> bool:
        return (self.repo_dir / ".git").exists()

    def _git_text(self, args: List[str], *, timeout: int = 30) -> str:
        result = self._run(["git", *args], timeout=timeout)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "git command failed").strip())
        return result.stdout.strip()

    def status(self, *, fetch: bool = True) -> UpdateStatus:
        if not self._git_ok():
            return UpdateStatus(supported=False, error="当前目录不是 Git 仓库，无法在线更新")

        try:
            branch = self._git_text(["branch", "--show-current"]) or "master"
            remote_ref = f"origin/{branch}"

            if fetch:
                fetch_result = self._run(["git", "fetch", "origin"], timeout=90)
                if fetch_result.returncode != 0:
                    return UpdateStatus(
                        supported=True,
                        branch=branch,
                        error=self._short(fetch_result.stderr or fetch_result.stdout),
                    )

            current_commit = self._git_text(["rev-parse", "HEAD"])
            current_message = self._git_text(["log", "-1", "--pretty=%h %s"])

            remote_commit = None
            remote_message = None
            recent_commits: List[str] = []
            ahead = 0
            behind = 0
            try:
                remote_commit = self._git_text(["rev-parse", remote_ref])
                remote_message = self._git_text(["log", "-1", "--pretty=%h %s", remote_ref])
                counts = self._git_text(["rev-list", "--left-right", "--count", f"HEAD...{remote_ref}"])
                parts = counts.split()
                if len(parts) == 2:
                    ahead, behind = int(parts[0]), int(parts[1])
                log_text = self._git_text(["log", "--oneline", f"HEAD..{remote_ref}"])
                recent_commits = [line for line in log_text.splitlines() if line][:10]
            except Exception:
                # 远程分支不存在或尚未 fetch 时，不影响基础状态展示。
                pass

            dirty_files = [line for line in self._git_text(["status", "--porcelain"]).splitlines() if line]
            return UpdateStatus(
                supported=True,
                branch=branch,
                current_commit=current_commit,
                remote_commit=remote_commit,
                current_message=current_message,
                remote_message=remote_message,
                has_update=bool(remote_commit and current_commit != remote_commit and behind > 0),
                dirty=bool(dirty_files),
                dirty_files=dirty_files[:50],
                ahead=ahead,
                behind=behind,
                recent_commits=recent_commits,
            )
        except Exception as exc:
            return UpdateStatus(supported=True, error=str(exc))

    def apply(self) -> UpdateApplyResult:
        status = self.status(fetch=True)
        if not status.supported:
            return UpdateApplyResult(status=False, msg=status.error or "当前环境不支持在线更新")
        if status.error:
            return UpdateApplyResult(status=False, msg=status.error)
        if status.dirty:
            return UpdateApplyResult(
                status=False,
                msg="工作区存在未提交改动，已取消在线更新，避免覆盖本地文件",
                stderr="\n".join(status.dirty_files),
            )
        if not status.has_update:
            return UpdateApplyResult(status=True, msg="当前已是最新版本", need_restart=False)

        branch = status.branch or "master"
        pull = self._run(["git", "pull", "--ff-only", "origin", branch], timeout=120)
        if pull.returncode != 0:
            return UpdateApplyResult(
                status=False,
                msg="git pull 失败",
                stdout=self._short(pull.stdout),
                stderr=self._short(pull.stderr),
            )

        uv = self._run(["uv", "sync"], timeout=300)
        if uv.returncode != 0:
            return UpdateApplyResult(
                status=False,
                msg="代码已拉取，但 uv sync 失败，请手动检查依赖",
                stdout=self._short((pull.stdout or "") + "\n" + (uv.stdout or "")),
                stderr=self._short(uv.stderr),
                need_restart=True,
            )

        return UpdateApplyResult(
            status=True,
            msg="更新完成，请重启服务使新版本生效",
            stdout=self._short((pull.stdout or "") + "\n" + (uv.stdout or "")),
            stderr=self._short((pull.stderr or "") + "\n" + (uv.stderr or "")),
            need_restart=True,
        )

    @staticmethod
    def restart_later(delay_seconds: float = 1.0) -> None:
        def _restart() -> None:
            time.sleep(delay_seconds)
            os.execv(sys.executable, [sys.executable, *sys.argv])

        threading.Thread(target=_restart, daemon=True).start()


updater = Updater()
