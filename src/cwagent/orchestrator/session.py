"""组装一局对局:模型、知识、记忆、记录目录。CLI 和网页都从这里启动。"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from .. import paths, proc
from ..brain.screen_agent import ScreenAgent, load_rules
from ..config import AppConfig, Effort, ModelEndpoint
from ..knowledge.companions import CompanionBook
from ..knowledge.equipment import EquipmentBook
from ..knowledge.guides import GuideAdvisor
from ..knowledge.index import NameIndex
from ..llm.external import EXTERNAL
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


def endpoints(cfg: AppConfig, model: str | None, effort: Effort | None = None) -> dict[str, ModelEndpoint]:
    """各决策层用哪个模型。指定了模型(网页上选的)时,备战、战略、三选一都用它,便于对比模型;
    没指定时按 config.yaml 的 roles。导航层配置了就用配置的(找路不需要强模型)。
    选外部 agent 时所有决策都交给它(用户可能根本没配 API)。
    effort(开局时选的思考强度)覆盖备战、战略、三选一的档案设置,导航层仍按它自己的档案。"""
    if model == EXTERNAL:
        return {"default": ModelEndpoint(name=EXTERNAL, format="external")}
    main = cfg.endpoint(model) if model else cfg.role_endpoint("tactical")
    out = {"default": main}
    if not model:
        for role in ("strategic", "chooser"):
            if getattr(cfg.roles, role):
                out[role] = cfg.endpoint(getattr(cfg.roles, role))
    if effort:
        out = {k: v.model_copy(update={"reasoning_effort": effort}) for k, v in out.items()}
    if cfg.roles.navigator:
        out["navigator"] = cfg.endpoint(cfg.roles.navigator)
    return out


def load_equipment() -> EquipmentBook | None:
    """装备知识(配方、攻略统计)和图标。知识库没同步时返回 None(不识别装备)。"""
    if not paths.knowledge_db().exists() or not (paths.templates_dir() / "equipment").exists():
        return None
    return EquipmentBook(paths.knowledge_db())


def load_guides() -> GuideAdvisor | None:
    """攻略(按局面挑方向用)。知识库没同步时返回 None。"""
    if not paths.knowledge_db().exists():
        return None
    return GuideAdvisor(paths.knowledge_db())


def build_runner(cfg: AppConfig, device, *, model: str | None = None, effort: Effort | None = None,
                 log=print) -> tuple[Runner, Path]:
    eps = endpoints(cfg, model, effort)
    endpoint = eps["default"]
    memories = [m for m, _ in MemoryStore().all()]
    guides = load_guides() if cfg.agent.use_guides else None
    index = NameIndex(paths.knowledge_db(), guide_stats=cfg.agent.use_guides)
    brain = ScreenAgent(eps, index, memories, load_rules(paths.root() / "knowledge" / "rules.md"), guides)
    # 目录名只留字母数字和 . - _(档案名可能带空格,对局记录接口不认)
    run_dir = paths.runs_dir() / f"{time.strftime('%Y%m%d-%H%M%S')}_{re.sub(r'[^\w.-]+', '-', endpoint.name)}"
    trace = Trace(run_dir)
    (run_dir / "system.md").write_text(brain.system, encoding="utf-8")
    book = CompanionBook()
    runner = Runner(device, brain, trace, log=log, difficulty=cfg.agent.difficulty, portraits=load_portraits(),
                    on_companion=lambda name, desc: book.record(name, desc=desc, screenshot=str(run_dir / "shots")),
                    prepare_turns=cfg.agent.max_tactical_calls_per_stage, equipment=load_equipment(),
                    guides=guides, trailblazer=cfg.agent.trailblazer_name)
    trace.meta(profile=endpoint.name, model=endpoint.model, effort=endpoint.reasoning_effort,
               difficulty=cfg.agent.difficulty, roles={k: v.name for k, v in eps.items() if k != "default"},
               started_at=time.strftime("%Y-%m-%d %H:%M:%S"),
               # 跑这局的进程:网页据此判断对局还在不在跑(崩溃、被结束时写不进结束时间)
               pid=os.getpid(), pid_started=proc.started(os.getpid()))
    return runner, run_dir


def run_session(runner: Runner, opts: RunOptions, *, log=print) -> str:
    """跑完一局并把结果写进 meta.json。"""
    result = "error"
    try:
        result = runner.run(opts)
    finally:
        runner.trace.meta(result=result, outcome=runner.outcome, ended_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        runner.trace.close()
        try:
            from ..replay import report

            log(f"报表:{report.write(runner.trace.root)}")
        except Exception as e:  # noqa: BLE001 - 报表出错不影响对局结果
            log(f"生成报表出错:{type(e).__name__}: {e}")
    log(f"结束:{result}")
    return result


def play(cfg: AppConfig, device, *, model: str | None = None, effort: Effort | None = None, max_steps: int = 600,
         log=print, stop=None) -> str:
    runner, run_dir = build_runner(cfg, device, model=model, effort=effort, log=log)
    log(f"对局记录:{run_dir}")
    opts = RunOptions(max_steps=max_steps, max_minutes=cfg.agent.max_run_minutes)
    if stop is not None:
        opts.stop = stop
    return run_session(runner, opts, log=log)
