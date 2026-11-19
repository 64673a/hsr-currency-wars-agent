"""整局逐节点报表(设计文档第 20.9 节):每个节点进场时的数字、出战时的阵容、战斗结果、模型调用和 token。

从对局记录 steps.jsonl 算出来,不依赖对局时的内存状态,所以旧对局也能重算(`cwagent runs report`)。
用来对比不同模型、不同改动的效果,也让「钱不花、前台只站一人、全是 1 星」这类问题一眼看得出来。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..brain.ledger import node_types
from ..llm.external import EXTERNAL

RESULT_BUTTONS = ("继续挑战", "前往结算")   # 每场战斗的结算画面;最终首领打完是「前往结算」(画面分类认成 other)
NAVIGATION = r"点击「(前往结算|下一步|继续挑战|返回|关闭)」"   # 没见过的界面上只是翻页的点击,不算选择
CHOICE_NAMES = {"environment_choice": "投资环境", "strategy_choice": "投资策略", "supply": "补给",
                "encounter_choice": "遭遇", "spotlight_choice": "主角光环", "tea_break": "幕后茶歇",
                "spotlight_replace": "替换主角光环", "hack": "骇入效果", "dance": "梦境交织共舞", "ranger": "巡海游侠任命",
                "garden": "奇兽花园", "clara_mod": "机心巧匠改装", "mentor": "从师", "equipment_reward": "选择装备",
                "other": "其他界面"}


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
    progress: str = ""                  # 战斗进度,如 0%→33%;打赢了终点记 100%,结算画面有「挑战进度」时按它
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


def _plain(texts: list[str]) -> list[str]:
    """记录里的文字带坐标前缀「(0.499,0.212) 挑战结束」,去掉坐标。"""
    return [re.sub(r"^\([\d.]+,[\d.]+\) ", "", t) for t in texts]


def _result_progress(texts: list[str]) -> int | None:
    """结算画面上「挑战进度」后面的数字(最终首领的结算画面有,普通节点的没有)。"""
    plain = _plain(texts)
    for i, t in enumerate(plain):
        if t.startswith("挑战进度"):
            m = re.search(r"(\d{1,3})", t[4:]) or (re.fullmatch(r"(\d{1,3})%?", plain[i + 1]) if i + 1 < len(plain) else None)
            if m:
                return int(m.group(1))
    return None


def outcome(steps: list[dict]) -> str | None:
    """整局结果:开局之后的整局结算画面写「挑战成功」是通关、「挑战失败」是失败(和网页对局列表同一个判法)。"""
    started, out = False, None
    for r in steps:
        started = started or r.get("screen") in ("run_intro", "environment_choice", "prepare", "battle")
        if started and r.get("screen") == "settlement":
            joined = " ".join(r.get("texts") or [])
            out = "失败" if "挑战失败" in joined else "通关" if "挑战成功" in joined else out
    return out


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
    battled: set[str] = set()            # 有战斗记录的节点
    kinds: dict[str, set[str]] = {}      # 位面调查读到的节点类型
    shown: dict[str, int] = {}           # 结算画面上读到的「挑战进度」

    def row(node: str) -> NodeRow:
        if node not in rows:
            rows[node] = NodeRow(node)
            order.append(node)
        return rows[node]

    for r in steps:
        if r.get("plane_nodes"):
            kinds.update(node_types(str(r["plane_nodes"]).splitlines()))
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
        actions = [str(a) for a in (r.get("actions") or [])]
        picks: list[str] = []
        if screen in CHOICE_NAMES and decision:
            # 没见过的界面(茶歇之后的选项等)只记点了哪段文字,按坐标点的看不出选了什么
            clicked = [a for a in actions if ("点击「" if screen == "other" else "点击") in a]
            if screen == "other" and all(re.search(NAVIGATION, a) for a in clicked):
                clicked = []
            picked = "、".join(clicked)[:80]
            if picked or screen != "other":
                picks.append(f"{CHOICE_NAMES[screen]}:{picked or decision.get('summary') or ''}".rstrip(":"))
        # 备战里从武装箱、聘用书等道具里拿的东西也会改变阵容
        picks += [f"道具:{a.split(':', 1)[1]}" for a in actions if "的道具里拿" in a and ":" in a]
        for text in picks:
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
            battled.add(current)
            m = re.search(r"进度 (\d+)% → (?:最后读到 )?(\d+)%", summary)
            if m:
                rw.progress = f"{m.group(1)}%→{m.group(2)}%"
            deaths = re.search(r"阵亡(?:横幅)? (\d+) 次", summary)   # 只数认出的横幅;旧记录里「另有 N 次行动值骤降」不算
            rw.deaths = int(deaths.group(1)) if deaths else (0 if re.search(r"没有看到阵亡提示|没认出阵亡横幅", summary) else None)
        texts = r.get("texts") or []
        joined = " ".join(texts)
        if screen == "node_result" or (screen != "settlement" and any(b in joined for b in RESULT_BUTTONS)):
            if "挑战成功" in joined:
                rw.result = "胜"
            elif "挑战结束" in joined or "挑战失败" in joined:
                rw.result = "负"
            shown_progress = _result_progress(texts)
            if shown_progress is not None:
                shown[current] = shown_progress
            hp = _hp_from_texts(texts)
            if hp is not None:
                rw.hp_after = hp
        if screen == "settlement" and rw.hp_after is None:
            rw.hp_after = _hp_from_texts(texts)     # 最终首领的结算画面没有血量,整局结算画面有
    if pending_choices and order:
        rows[order[-1]].choices.extend(pending_choices)
    out = [rows[n] for n in order]
    # 战斗中最后一次读到的进度不是终点(大招特效常挡住进度条,2026-11-19 那局 2-7、3-2 打赢了只读到 94%、89%):
    # 结算画面有「挑战进度」时按它,打赢了终点就是 100%;奖励节点不会输(不限行动值、阵亡无限复活,维护者告知),
    # 走过去了就记 100% 胜(那局 2-6 十几秒就打完,程序没看到战斗);别的没看到战斗的标出来
    for i, rw in enumerate(out):
        start = rw.progress.split("→")[0] if rw.progress else "0%"
        if "reward" in kinds.get(rw.node, ()) and (rw.result or i + 1 < len(out)):
            rw.progress, rw.result = "0%→100%", "胜"
        elif rw.node in shown:
            rw.progress = f"{start}→{shown[rw.node]}%"
        elif rw.result == "胜" and rw.node in battled:
            rw.progress = f"{start}→100%"
        elif rw.result and rw.node not in battled:
            rw.progress = "没看到战斗"
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
    # 外部 agent 用的是它自己工具里的模型,请求次数和 token 程序看不到;「调用」只是交上来的回答次数
    external = (meta.get("profile") or meta.get("model")) == EXTERNAL
    usage = f"交来回答 {calls} 次,token 用量程序看不到" if external else f"模型调用 {calls} 次,输入 {tokens} token"
    ended = outcome(steps)
    result = f"{ended}({meta['result']})" if ended and meta.get("result") else ended or meta.get("result") or "?"
    lines = [f"# 对局报表 {run.name}", "",
             f"- 模型 {meta.get('profile') or meta.get('model') or '?'},难度 {meta.get('difficulty') or '?'},"
             f"结果 {result}",
             f"- 走到 {rows[-1].node if rows else '?'},战斗 {len(fights)} 场,胜 {sum(r.result.startswith('胜') for r in fights)} 场;"
             f"{usage}", "",
             "| 节点 | 进场血量 | 进场金币 | 等级 | 出战时上场 | 前台 | 场上2星+ | 出战时金币 | 战斗进度 | 阵亡横幅 | 胜负 | 战后血量 | 调用 | 输入token |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        level = _cell(r.level_in) + (f"→{r.level_out}" if r.level_out and r.level_out != r.level_in else "")
        stars = f"{r.stars2}/{r.on_field}" if r.on_field else ""
        lines.append(f"| {r.node} | {_cell(r.hp_in)} | {_cell(r.gold_in)} | {level} | {_cell(r.deployed)} | "
                     f"{_cell(r.front)} | {stars} | {_cell(r.gold_out)} | {r.progress} | {_cell(r.deaths)} | {r.result} | "
                     f"{_cell(r.hp_after)} | {r.calls} | {'-' if external else r.tokens} |")
    picks = [(r.node, c) for r in rows for c in r.choices]
    if picks:
        lines += ["", "## 选择", ""] + [f"- {n} {c}" for n, c in picks]
    # 模型自己查的资料(2026-11-18 起攻略和经验由模型 lookup):查了几次、查了什么,用来看模型会不会去查
    looks = [(r.get("node") or "", x.get("what"), x.get("query")) for r in steps
             for x in (r.get("decision") or {}).get("lookups") or []]
    pinned = [(r.get("node") or "", (r.get("decision") or {}).get("guide")) for r in steps
              if (r.get("decision") or {}).get("guide")]
    if looks:
        names = {"guides": "攻略", "guide": "攻略全文", "memory": "经验", "equipment": "常配装备"}
        lines += ["", f"## 查资料({len(looks)} 次)", ""] + [f"- {n} 查{names.get(w, w)}:{q}" for n, w, q in looks]
    if pinned:
        lines += ["", "## 挂攻略", ""] + [f"- {n} {g}" for n, g in pinned]
    return "\n".join(lines) + "\n"


def write(run: Path) -> Path:
    path = run / "report.md"
    path.write_text(render(run), encoding="utf-8")
    return path
