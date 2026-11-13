"""后台任务:知识同步在线程里跑,前端轮询进度。"""

from __future__ import annotations

import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from typing import Any

MAX_LOG = 200


class Job:
    def __init__(self, name: str):
        self.name = name
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._state: dict[str, Any] = {"running": False, "stage": "", "message": "", "log": [],
                                       "result": None, "error": None, "started_at": None, "finished_at": None}

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {**self._state, "log": list(self._state["log"])}

    def progress(self, stage: str, message: str) -> None:
        with self._lock:
            self._state["stage"], self._state["message"] = stage, message
            log = self._state["log"]
            log.append(f"{time.strftime('%H:%M:%S')} [{stage}] {message}")
            del log[:-MAX_LOG]

    def start(self, fn: Callable[[Callable[[str, str], None]], Any]) -> bool:
        """在后台线程执行 fn(progress)。已在运行时返回 False。"""
        with self._lock:
            if self._state["running"]:
                return False
            self._state.update(running=True, stage="start", message="", log=[], result=None, error=None,
                               started_at=time.time(), finished_at=None)

        def run() -> None:
            try:
                result = fn(self.progress)
                if is_dataclass(result):
                    result = asdict(result)
                with self._lock:
                    self._state["result"] = result
            except Exception as e:  # noqa: BLE001 - 错误写进状态给前端看
                with self._lock:
                    self._state["error"] = f"{type(e).__name__}: {e}"
                    self._state["log"].append(traceback.format_exc(limit=3))
            finally:
                with self._lock:
                    self._state["running"] = False
                    self._state["finished_at"] = time.time()

        self._thread = threading.Thread(target=run, name=f"job-{self.name}", daemon=True)
        self._thread.start()
        return True

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)
