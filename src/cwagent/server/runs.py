"""对局记录(runs/ 目录)的读取,以及从网页启动 / 停止对局。"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import ConfigStore
from ..llm.external import BRIDGE, EXTERNAL

RUN_ID = re.compile(r"^[\w.-]+$")
LIVE_SECONDS = 90  # steps.jsonl 在这段时间内有写入、且没有结束时间,就算进行中
NODE = re.compile(r"[1-3]-\d{1,2}(?![\d%])")  # 战斗时节点号和进度会连在一起(如「1-30%」),这种不算


def _node_from_texts(texts: list[str]) -> str | None:
    """旧记录没有 node 字段:从「(x,y) 文字」里找顶部的节点号。"""
    for t in texts:
        m = re.match(r"\(([\d.]+),([\d.]+)\) (.*)", t)
        if m and float(m.group(2)) < 0.14 and float(m.group(1)) < 0.6:
            n = NODE.match(m.group(3))
            if n:
                return n.group(0)
    return None


def _node_key(node: str) -> tuple[int, int]:
    a, b = node.split("-")
    return int(a), int(b)


def _started(run_id: str) -> str:
    m = re.match(r"(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})", run_id)
    return f"{m[1]}-{m[2]}-{m[3]} {m[4]}:{m[5]}:{m[6]}" if m else ""


class RunStore:
    def __init__(self, root: Path):
        self.root = root
        self._cache: dict[str, tuple[tuple[float, int], dict[str, Any]]] = {}

    def dir(self, run_id: str) -> Path:
        if not RUN_ID.match(run_id) or not (self.root / run_id / "steps.jsonl").is_file():
            raise KeyError(run_id)
        return self.root / run_id

    def _records(self, d: Path):
        with (d / "steps.jsonl").open(encoding="utf-8") as f:
            for line in f:
                if line.startswith("{"):
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:  # 正在写入的最后一行
                        continue

    def summary(self, d: Path) -> dict[str, Any]:
        stat = (d / "steps.jsonl").stat()
        key = (stat.st_mtime, stat.st_size)
        meta_path = d / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        cached = self._cache.get(d.name)
        if cached and cached[0] == key:
            info = cached[1]
        else:
            steps, node, outcome, decisions, started = 0, None, None, 0, False
            for r in self._records(d):
                started = started or r.get("screen") in ("run_intro", "environment_choice", "prepare", "battle")
                steps = max(steps, r.get("step", 0))
                n = r.get("node") or _node_from_texts(r.get("texts", []))
                if n and (node is None or _node_key(n) > _node_key(node)):
                    node = n
                if r.get("decision"):
                    decisions += 1
                # 单场战斗输了也会显示「挑战失败」,只看开局之后的整局结算(开头可能是上一局留下的结算界面)
                if started and r.get("screen") == "settlement":
                    texts = " ".join(r.get("texts", []))
                    if "挑战失败" in texts:
                        outcome = "失败"
                    elif "挑战成功" in texts:
                        outcome = "通关"
            info = {"steps": steps, "node": node, "outcome": outcome, "decisions": decisions}
            self._cache[d.name] = (key, info)
        live = (not meta.get("ended_at") and not info["outcome"]
                and time.time() - stat.st_mtime < LIVE_SECONDS)
        profile = meta.get("profile") or (d.name.split("_", 1)[1] if "_" in d.name else "")
        return {"id": d.name, "profile": profile, "model": meta.get("model", ""),
                "difficulty": meta.get("difficulty", ""), "started_at": meta.get("started_at") or _started(d.name),
                "ended_at": meta.get("ended_at"), "result": meta.get("result"), "live": live, **info}

    def list(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        dirs = [d for d in self.root.iterdir() if d.is_dir() and (d / "steps.jsonl").is_file()]
        return [self.summary(d) for d in sorted(dirs, key=lambda d: d.name, reverse=True)]

    def steps(self, run_id: str, after: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        out = []
        for r in self._records(self.dir(run_id)):
            if r.get("step", 0) <= after:
                continue
            if "node" not in r:
                r["node"] = _node_from_texts(r.get("texts", []))
            out.append(r)
            if len(out) >= limit:
                break
        return out

    def shot(self, run_id: str, name: str, *, folder: str = "shots") -> Path:
        d = self.dir(run_id)
        if folder not in ("shots", "vision"):
            raise KeyError(folder)
        target = (d / folder / name).resolve()
        if (d / folder).resolve() not in target.parents or not target.is_file():
            raise KeyError(name)
        return target


def default_device_factory() -> Callable[[], Any] | None:
    if sys.platform != "win32":
        return None

    def make():
        from ..device.windows import WindowsDevice

        return WindowsDevice()

    return make


class PlayController:
    """网页上的开始 / 停止。同一时间只跑一局。"""

    def __init__(self, store: ConfigStore, runs: RunStore,
                 device_factory: Callable[[], Any] | None | str = "default"):
        self.store = store
        self.runs = runs
        self.device_factory = default_device_factory() if device_factory == "default" else device_factory
        self._lock = threading.Lock()
        self._hotkey: str | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._state: dict[str, Any] = {"run_id": None, "profile": None, "difficulty": None, "round": 0,
                                       "rounds": 0, "result": None, "error": None, "log": []}

    def _log(self, message: str) -> None:
        with self._lock:
            log = self._state["log"]
            log.append(time.strftime("[%H:%M:%S] ") + message)
            del log[:-50]

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict[str, Any]:
        with self._lock:
            state = {**self._state, "log": list(self._state["log"])}
        return {"available": self.device_factory is not None, "running": self.running, "hotkey": self._hotkey,
                "stopping": self.running and self._stop.is_set(), "agent": BRIDGE.status(), **state}

    def start(self, *, model: str | None = None, difficulty: str | None = None, max_steps: int = 700,
              rounds: int = 1) -> dict[str, Any]:
        """连续打 rounds 局(每局一个记录目录),中途可以停止。"""
        from ..orchestrator.loop import RunOptions
        from ..orchestrator.session import build_runner, run_session

        if self.device_factory is None:
            raise RuntimeError("只能在装有游戏的 Windows 上开始对局")
        if self.running:
            raise RuntimeError("已有对局在运行")
        if any(r["live"] for r in self.runs.list()):
            raise RuntimeError("已有对局在运行(可能是命令行启动的)")
        cfg = self.store.load()
        if difficulty:
            cfg = cfg.model_copy(update={"agent": cfg.agent.model_copy(update={"difficulty": difficulty})})
        external = model == EXTERNAL
        if not external:
            cfg.endpoint(model) if model else cfg.role_endpoint("tactical")   # 模型配置有误时在这里就报错
        device = self.device_factory()
        if self._hotkey is None and sys.platform == "win32":
            from ..capture import windows as win

            # Ctrl+Alt+F12 被调试截图服务占着,agent 用自己的热键停止
            self._hotkey = win.register_hotkey(["ctrl+alt+f10", "ctrl+alt+f9", "ctrl+shift+f10"], self.stop) or ""
        self._stop = threading.Event()
        stop = self._stop
        if hasattr(device, "stop"):
            device.stop = stop   # 快捷键 / 停止按钮在下一个键鼠操作前就生效
        with self._lock:
            self._state.update(run_id=None, profile=None, difficulty=cfg.agent.difficulty, round=0, rounds=rounds,
                               result=None, error=None, log=[])

        def work() -> None:
            try:
                rounds_loop()
            finally:
                if external:
                    with self._lock:
                        BRIDGE.end(self._state["result"] or ("error" if self._state["error"] else "stopped"))
                close = getattr(device, "close", None)   # 拔掉虚拟手柄等
                if close:
                    close()

        def rounds_loop() -> None:
            for i in range(1, rounds + 1):
                if stop.is_set():
                    break
                runner = None
                try:
                    runner, run_dir = build_runner(cfg, device, model=model, log=self._log)
                    with self._lock:
                        self._state.update(run_id=run_dir.name, profile=runner.brain.endpoint("default").name, round=i)
                    opts = RunOptions(max_steps=max_steps, max_minutes=cfg.agent.max_run_minutes, stop=stop)
                    result = run_session(runner, opts, log=self._log)
                    runner = None
                    with self._lock:
                        self._state["result"] = result
                    if result not in ("settlement",):   # 停止、超时、暂停、步数用完都不再开下一局
                        break
                except Exception as e:  # noqa: BLE001 - 错误给网页看
                    with self._lock:
                        self._state["error"] = f"{type(e).__name__}: {e}"
                    if runner is not None:   # 记录目录建好了但没开始打:标记结束,否则会一直显示「进行中」
                        runner.trace.meta(result="error", ended_at=time.strftime("%Y-%m-%d %H:%M:%S"))
                    break

        if external:
            BRIDGE.begin(stop)   # 在线程启动前就绪,start_run 紧接着就要等局面
        self._thread = threading.Thread(target=work, name="play", daemon=True)
        self._thread.start()
        time.sleep(0.2)   # 让状态里先有本局的 run_id
        return self.status()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        return self.status()
