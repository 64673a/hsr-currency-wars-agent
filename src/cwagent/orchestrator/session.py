"""组装一局对局:模型、知识、记忆、记录目录。CLI 和网页都从这里启动。"""

from __future__ import annotations

import time
from pathlib import Path

from .. import paths
from ..brain.screen_agent import ScreenAgent, load_rules
from ..config import AppConfig, ModelEndpoint
from ..knowledge.companions import CompanionBook
from ..knowledge.equipment import EquipmentBook
from ..knowledge.index import NameIndex
from ..memory import MemoryStore
from ..vision.board import Portraits
from .loop import Runner, RunOptions, Trace


def load_portraits() -> Portraits | None:
    """头像库:知识库里的角色名 + 模板图。知识库没同步时返回 None(不识别阵容)。"""
    import sqlite3

    db = paths.knowledge_db()
    if not db.exists():
        return None
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        names = dict(con.execute("SELECT id, name FROM roles"))
    finally:
        con.close()
    return Portraits(paths.templates_dir(), names)


def endpoints(cfg: AppConfig, model: str | None) -> dict[str, ModelEndpoint]:
    """各决策层用哪个模型。指定了模型(网页上选的)时,备战、战略、三选一都用它,便于对比模型;
    没指定时按 config.yaml 的 roles。导航层配置了就用配置的(找路不需要强模型)。"""
    main = cfg.endpoint(model) if model else cfg.role_endpoint("tactical")
    out = {"default": main}
    if not model:
        for role in ("strategic", "chooser"):
            if getattr(cfg.roles, role):
                out[role] = cfg.endpoint(getattr(cfg.roles, role))
    if cfg.roles.navigator:
        out["navigator"] = cfg.endpoint(cfg.roles.navigator)
    return out


def load_equipment() -> EquipmentBook | None:
    """装备知识(配方、攻略统计)和图标。知识库没同步时返回 None(不识别装备)。"""
    if not paths.knowledge_db().exists() or not (paths.templates_dir() / "equipment").exists():
        return None
    return EquipmentBook(paths.knowledge_db())


def build_runner(cfg: AppConfig, device, *, model: str | None = None, log=print) -> tuple[Runner, Path]:
    eps = endpoints(cfg, model)
    endpoint = eps["default"]
    memories = [m for m, _ in MemoryStore().all()]
    brain = ScreenAgent(eps, NameIndex(paths.knowledge_db()), memories, load_rules(paths.root() / "knowledge" / "rules.md"))
    run_dir = paths.runs_dir() / f"{time.strftime('%Y%m%d-%H%M%S')}_{endpoint.name}"
    trace = Trace(run_dir)
    book = CompanionBook()
    runner = Runner(device, brain, trace, log=log, difficulty=cfg.agent.difficulty, portraits=load_portraits(),
                    on_companion=lambda name: book.record(name, screenshot=str(run_dir / "shots")),
                    prepare_turns=cfg.agent.max_tactical_calls_per_stage, equipment=load_equipment())
    trace.meta(profile=endpoint.name, model=endpoint.model, difficulty=cfg.agent.difficulty,
               roles={k: v.name for k, v in eps.items() if k != "default"},
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
