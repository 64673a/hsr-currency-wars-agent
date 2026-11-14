"""组装一局对局:模型、知识、记忆、记录目录。CLI 和网页都从这里启动。"""

from __future__ import annotations

import time
from pathlib import Path

from .. import paths
from ..brain.screen_agent import ScreenAgent, load_rules
from ..config import AppConfig
from ..knowledge.companions import CompanionBook
from ..knowledge.index import NameIndex
from ..memory import MemoryStore
from .loop import Runner, RunOptions, Trace


def build_runner(cfg: AppConfig, device, *, model: str | None = None, log=print) -> tuple[Runner, Path]:
    endpoint = cfg.endpoint(model) if model else cfg.role_endpoint("tactical")
    memories = [m for m, _ in MemoryStore().all()]
    brain = ScreenAgent(endpoint, NameIndex(paths.knowledge_db()), memories, load_rules(paths.root() / "knowledge" / "rules.md"))
    run_dir = paths.runs_dir() / f"{time.strftime('%Y%m%d-%H%M%S')}_{endpoint.name}"
    trace = Trace(run_dir)
    book = CompanionBook()
    runner = Runner(device, brain, trace, log=log, difficulty=cfg.agent.difficulty,
                    on_companion=lambda name: book.record(name, screenshot=str(run_dir / "shots")))
    trace.meta(profile=endpoint.name, model=endpoint.model, difficulty=cfg.agent.difficulty,
               started_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    return runner, run_dir


def run_session(runner: Runner, opts: RunOptions, *, log=print) -> str:
    """跑完一局并把结果写进 meta.json。"""
    result = "error"
    try:
        result = runner.run(opts)
    finally:
        runner.trace.meta(result=result, ended_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        runner.trace.close()
    log(f"结束:{result}")
    return result


def play(cfg: AppConfig, device, *, model: str | None = None, max_steps: int = 600, log=print) -> str:
    runner, run_dir = build_runner(cfg, device, model=model, log=log)
    log(f"对局记录:{run_dir}")
    return run_session(runner, RunOptions(max_steps=max_steps, max_minutes=cfg.agent.max_run_minutes), log=log)
