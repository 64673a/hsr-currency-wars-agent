"""整局逐节点报表(设计文档第 20.9 节):每个节点进场时的数字、出战时的阵容、战斗结果、模型调用和 token。

从对局记录 steps.jsonl 算出来,不依赖对局时的内存状态,所以旧对局也能重算(`cwagent runs report`)。
用来对比不同模型、不同改动的效果,也让「钱不花、前台只站一人、全是 1 星」这类问题一眼看得出来。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

CHOICE_NAMES = {"environment_choice": "投资环境", "strategy_choice": "投资策略", "supply": "补给",
                "encounter_choice": "遭遇", "spotlight_choice": "主角光环", "tea_break": "幕后茶歇"}


@dataclass
class NodeRow:
    node: str
    hp_in: int | None = None
    gold_in: int | None = None
    level_in: int | None = None
    deployed: str | None = None         # 出战前最后一次读到的「已上场/上限」
    front: int | None = None            # 出战前前台站了几个
    stars2: int | None = None           # 出战前场上(前台 + 后台)2 星及以上的有几个
    on_field: int | None = None         # 出战前场上一共几个
    gold_out: int | None = None         # 出战前剩的金币
    level_out: int | None = None
    progress: str = ""                  # 战斗进度,如 0%→33%
    deaths: int | None = None
    result: str = ""                    # 胜 / 负
    hp_after: int | None = None
    calls: int = 0
    tokens: int = 0
    choices: list[str] = field(default_factory=list)


def _steps(run: Path) -> list[dict]:
    path = run / "steps.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _hp_from_texts(texts: list[str]) -> int | None:
    for t in texts:
        m = re.search(r"小队生命值\D{0,3}(\d{1,3})", t)
        if m:
            return int(m.group(1))
    return None


def node_rows(steps: list[dict]) -> list[NodeRow]:
    rows: dict[str, NodeRow] = {}
    order: list[str] = []
    current: str | None = None
    pending_choices: list[str] = []      # 还没读到节点号时做的选择(如 1-3 的投资策略在进入 1-3 备战之前)

    def row(node: str) -> NodeRow:
        if node not in rows:
            rows[node] = NodeRow(node)
            order.append(node)
        return rows[node]

    for r in steps:
        node = r.get("node")
        if node and re.fullmatch(r"[1-3]-\d{1,2}", node):
            current = node
            # 选择界面上读不到节点号,算到之后第一个读到节点号的步骤上(1-3 的投资策略在 1-3 备战之前)
            row(node).choices.extend(pending_choices)
            pending_choices = []
        screen = r.get("screen")
        decision = r.get("decision") or {}
        if decision and current:
            rw = row(current)
            rw.calls += 1
            rw.tokens += _int((decision.get("usage") or {}).get("input_tokens")) or 0
        if screen in CHOICE_NAMES and decision:
            picked = "、".join(str(a) for a in (r.get("actions") or []) if "点击" in str(a))[:80]
            text = f"{CHOICE_NAMES[screen]}:{picked or decision.get('summary') or ''}".rstrip(":")
            if r.get("node"):
                row(r["node"]).choices.append(text)
            else:
                pending_choices.append(text)
        if not current:
            continue
        rw = row(current)
        state = r.get("state") or {}
        hud = state.get("hud") or {}
        if screen == "prepare" and hud:
            rw.hp_in = rw.hp_in if rw.hp_in is not None else _int(hud.get("hp"))
            rw.gold_in = rw.gold_in if rw.gold_in is not None else _int(hud.get("gold"))
            rw.level_in = rw.level_in if rw.level_in is not None else _int(hud.get("level"))
            if hud.get("gold") is not None:
                rw.gold_out = _int(hud.get("gold"))
            if hud.get("level") is not None:
                rw.level_out = _int(hud.get("level"))
            if hud.get("deployed"):
                rw.deployed = hud["deployed"]
            board = state.get("board")
            if board:
                field_ = [b for b in board if b.get("zone") in ("front", "back") and (b.get("name") or b.get("stars"))]
                rw.front = sum(1 for b in field_ if b.get("zone") == "front")
                rw.on_field = len(field_)
                rw.stars2 = sum(1 for b in field_ if (b.get("stars") or 0) >= 2)
        if screen == "battle" and r.get("battle_summary"):
            summary = r["battle_summary"]
            m = re.search(r"进度 (\d+)% → (?:最后读到 )?(\d+)%", summary)
            if m:
                rw.progress = f"{m.group(1)}%→{m.group(2)}%"
            deaths = re.search(r"阵亡(?:横幅)? (\d+) 次", summary)   # 只数认出的横幅;旧记录里「另有 N 次行动值骤降」不算
            rw.deaths = int(deaths.group(1)) if deaths else (0 if re.search(r"没有看到阵亡提示|没认出阵亡横幅", summary) else None)
        if screen == "node_result":
            texts = r.get("texts") or []
            joined = " ".join(texts)
            if "挑战成功" in joined:
                rw.result = "胜"
            elif "挑战结束" in joined:
                rw.result = "负"
            hp = _hp_from_texts(texts)
            if hp is not None:
                rw.hp_after = hp
    if pending_choices and order:
        rows[order[-1]].choices.extend(pending_choices)
    out = [rows[n] for n in order]
    # 没截到结算界面的战斗(进度最后读到 87% 不等于输了):看下一个节点的进场血量,胜利固定回 2 点,输了扣血
    for i, rw in enumerate(out):
        if rw.result or not rw.progress or rw.hp_in is None:
            continue
        nxt = next((o.hp_in for o in out[i + 1:] if o.hp_in is not None), None)
        if nxt is not None and nxt != rw.hp_in:
            rw.result = "胜(按血量)" if nxt > rw.hp_in else "负(按血量)"
    return out


def _cell(v) -> str:
    return "" if v is None else str(v)


def render(run: Path) -> str:
    steps = _steps(run)
    meta = {}
    if (run / "meta.json").exists():
        try:
            meta = json.loads((run / "meta.json").read_text(encoding="utf-8"))
        except ValueError:
            meta = {}
    rows = node_rows(steps)
    calls = sum(1 for r in steps if r.get("decision"))
    tokens = sum(_int(((r.get("decision") or {}).get("usage") or {}).get("input_tokens")) or 0 for r in steps)
    fights = [r for r in rows if r.result]
    lines = [f"# 对局报表 {run.name}", "",
             f"- 模型 {meta.get('profile') or meta.get('model') or '?'},难度 {meta.get('difficulty') or '?'},"
             f"结果 {meta.get('result') or '?'}",
             f"- 走到 {rows[-1].node if rows else '?'},战斗 {len(fights)} 场,胜 {sum(r.result.startswith('胜') for r in fights)} 场;"
             f"模型调用 {calls} 次,输入 {tokens} token", "",
             "| 节点 | 进场血量 | 进场金币 | 等级 | 出战时上场 | 前台 | 场上2星+ | 出战时金币 | 战斗进度 | 阵亡横幅 | 胜负 | 战后血量 | 调用 | 输入token |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        level = _cell(r.level_in) + (f"→{r.level_out}" if r.level_out and r.level_out != r.level_in else "")
        stars = f"{r.stars2}/{r.on_field}" if r.on_field else ""
        lines.append(f"| {r.node} | {_cell(r.hp_in)} | {_cell(r.gold_in)} | {level} | {_cell(r.deployed)} | "
                     f"{_cell(r.front)} | {stars} | {_cell(r.gold_out)} | {r.progress} | {_cell(r.deaths)} | {r.result} | "
                     f"{_cell(r.hp_after)} | {r.calls} | {r.tokens} |")
    picks = [(r.node, c) for r in rows for c in r.choices]
    if picks:
        lines += ["", "## 选择", ""] + [f"- {n} {c}" for n, c in picks]
    # 模型自己查的资料(2026-11-18 起攻略和经验由模型 lookup):查了几次、查了什么,用来看模型会不会去查
    looks = [(r.get("node") or "", a.get("what"), a.get("query")) for r in steps
             for a in (r.get("decision") or {}).get("actions") or [] if a.get("type") == "lookup"]
    pinned = [(r.get("node") or "", (r.get("decision") or {}).get("guide")) for r in steps
              if (r.get("decision") or {}).get("guide")]
    if looks or pinned:
        names = {"guides": "攻略", "guide": "攻略全文", "memory": "经验", "equipment": "常配装备"}
        lines += ["", f"## 查资料({len(looks)} 次)", ""] + [f"- {n} 查{names.get(w, w)}:{q}" for n, w, q in looks]
        lines += [f"- {n} 挂攻略:{g}" for n, g in pinned]
    return "\n".join(lines) + "\n"


def write(run: Path) -> Path:
    path = run / "report.md"
    path.write_text(render(run), encoding="utf-8")
    return path
