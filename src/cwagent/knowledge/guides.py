"""按当前局面挑攻略:开局先定一篇作为初始方向,之后每个备战阶段按场上、备战席、商店里的角色和投资环境重新排,
别的攻略明显更合适(例如商店刷出了它的主 C)时提示可以转向(维护者要求:动态选攻略,如 6 级时商店刷出刃就可以
转去刃 C 的攻略)。攻略只给方向,买谁、怎么站、给谁装备仍由模型判断(设计文档 1.6)。
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

SWITCH_MARGIN = 3.0   # 别的攻略比当前方向高出这么多分,才提示转向(大约等于多对上一个主 C)


@dataclass
class Guide:
    id: str
    title: str
    carries: list[str]
    final: dict            # {"front": [...], "back": [...]},每个角色带 cost / star / equipment
    early: set[str]
    middle: set[str]
    traits: list[str]
    environments: list[str]
    tempo: list[str]
    equip_order: dict
    idea: str
    augments: list[str]
    use_count: int
    hard: bool
    boards: dict = field(default_factory=dict)          # {"early": {"front": [...], "back": [...]}, "middle": ...}
    stage_traits: dict = field(default_factory=dict)    # {"early": [{"name", "count"}], "middle": ..., "final": ...}
    augment_groups: dict = field(default_factory=dict)  # {"first": [...], "second": [...]}
    author: str = ""
    emblems: list[str] = field(default_factory=list)    # 最终阵容首选装备里的星徽(星徽攻略)
    sub_expired: bool = False                           # 本期(子赛季)已过期:内容可能有失效的
    expired_items: list[str] = field(default_factory=list)

    @property
    def final_names(self) -> set[str]:
        return {r["name"] for side in ("front", "back") for r in self.final.get(side) or [] if isinstance(r, dict)}


@dataclass
class Match:
    guide: Guide
    score: float
    carries_have: list[str]
    carries_shop: list[str]
    have: list[str]
    shop: list[str]

    def why(self) -> str:
        parts = []
        if self.carries_have:
            parts.append(f"主 C {'、'.join(self.carries_have)} 已经有了")
        if self.carries_shop:
            parts.append(f"主 C {'、'.join(self.carries_shop)} 商店里有")
        others = [n for n in self.have if n not in self.carries_have]
        if others:
            parts.append(f"阵容里的 {'、'.join(others[:5])} 已经有了")
        shop = [n for n in self.shop if n not in self.carries_shop]
        if shop:
            parts.append(f"{'、'.join(shop[:4])} 商店里有")
        return ";".join(parts) or "现有角色都对不上,主要看投资环境和热门程度"


class GuideAdvisor:
    def __init__(self, db_path: Path):
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = con.execute("SELECT data FROM guides").fetchall()
        finally:
            con.close()
        self.guides: list[Guide] = []
        for (data,) in rows:
            g = json.loads(data)
            if g.get("expired"):
                continue
            boards = g.get("example_boards") or {}

            def names(stage: str) -> set[str]:
                b = boards.get(stage) or {}
                return {_name(n) for side in ("front", "back") for n in b.get(side) or [] if _name(n)}

            stage_traits = dict(g.get("stage_traits") or {})
            stage_traits["final"] = [t for t in g.get("key_traits") or [] if isinstance(t, dict)]
            self.guides.append(Guide(
                id=g["id"], title=g.get("title", ""), carries=list(g.get("carries") or []),
                final=g.get("final_board") or {}, early=names("early"), middle=names("middle"),
                traits=[t["name"] for t in g.get("key_traits") or [] if isinstance(t, dict)],
                environments=list(g.get("environments") or []), tempo=list(g.get("tempo_labels") or []),
                equip_order=g.get("equip_order") or {},
                # 作者原文最多 200 字(官方上限,详情接口也一样),有的写了逐节点的打法,不再截断
                idea=(g.get("description") or g.get("core_idea") or "").strip(),
                augments=list(g.get("recommended_augments") or []), use_count=int(g.get("use_count") or 0),
                hard=bool(g.get("support_hard")), boards=boards, stage_traits=stage_traits,
                augment_groups=dict(g.get("augments") or {}), author=g.get("author") or "",
                emblems=list(g.get("emblems") or []), sub_expired=bool(g.get("sub_expired")),
                expired_items=list(g.get("expired_items") or [])))
        self.by_id = {g.id: g for g in self.guides}

    def score(self, g: Guide, owned: set[str], shop: set[str], environment: str | None) -> Match:
        carries = set(g.carries)
        final = g.final_names
        carries_have = [c for c in g.carries if c in owned]
        carries_shop = [c for c in g.carries if c in shop and c not in owned]
        have = sorted((final | g.early | g.middle) & owned)
        in_shop = sorted((final - owned) & shop)
        s = (5 * len(carries_have) + 3 * len(carries_shop) + 1.0 * len((final - carries) & owned)
             + 0.6 * len(((g.early | g.middle) - final) & owned) + 0.4 * len(set(in_shop) - carries)
             + (2 if environment and environment in g.environments else 0) + (0.5 if g.hard else 0)
             + 0.3 * math.log10(1 + g.use_count))
        return Match(g, round(s, 2), carries_have, carries_shop, have, in_shop)

    def rank(self, owned: list[str], shop: list[str], environment: str | None = None, top: int = 3) -> list[Match]:
        o, s = set(owned), set(shop) - set(owned)
        return sorted((self.score(g, o, s, environment) for g in self.guides), key=lambda m: m.score, reverse=True)[:top]

    def shop_carries(self, owned: list[str], shop: list[str], environment: str | None = None) -> list[Match]:
        """商店里刷出了哪些攻略的主 C(还没有):每个主 C 给最合适的一篇,作为转向机会。"""
        o, s = set(owned), set(shop) - set(owned)
        best: dict[str, Match] = {}
        for g in self.guides:
            for c in g.carries:
                if c in s:
                    m = self.score(g, o, s, environment)
                    if c not in best or m.score > best[c].score:
                        best[c] = m
        return sorted(best.values(), key=lambda m: m.score, reverse=True)

    @property
    def environments(self) -> set[str]:
        return {e for g in self.guides for e in g.environments}

    def find(self, key: str) -> Guide | None:
        """按 id 或标题(含部分标题)找攻略。"""
        if key in self.by_id:
            return self.by_id[key]
        return next((g for g in self.guides if g.title == key), None) or next(
            (g for g in self.guides if key and key in g.title), None)


def _name(r) -> str:
    return (r.get("name") or "") if isinstance(r, dict) else str(r or "")


def _traits(items: list) -> str:
    return "、".join(f"{t.get('count') or ''}{t.get('name')}" for t in items if isinstance(t, dict) and t.get("name"))


_ROSTER = re.compile(r"试用|星魂|命座|满魂|[0-6一二三四五六]魂|练度|专武|光锥")


def roster_notes(g: Guide) -> str:
    """标题和作者原文里讲试用 / 自有练度的句子(「全试用可玩」「刚需雪衣满星魂」「有命座的砂金都能打」)。
    自己有的角色继承账号里的养成、没有的给试用版(BWIKI),同一套阵容强弱差很多,选攻略时要看。"""
    parts = re.split(r"(?<=[。!！?？;；\n])|[,，](?=[^,，]*(?:试用|星魂|命座|魂|练度))", f"{g.title}。{g.idea}")
    hits = [p.strip(" ,，。;；\n") for p in parts if _ROSTER.search(p)]
    return ";".join(dict.fromkeys(h for h in hits if h))[:160]


def card(g: Guide, *, full: bool = True) -> str:
    """给模型看的攻略卡片。full=False 时只给要点(候选攻略用)。

    完整卡片按阶段排(2026-11-16 起,设计文档第 20.4 节):作者原文(最多 200 字,有的写了逐节点打法)、前期、中期、
    最终阵容(星级、装备、次选装备、推荐光环)、各阶段羁绊人数、装备顺序、两组推荐投资策略。官方攻略里没有比这更细的
    逐节点流程;前期 / 中期对应哪个节点,接口没有说。
    """
    def unit(r, detail: bool) -> str:
        if not isinstance(r, dict):
            return str(r)
        text = r.get("name", "") + (f" {r['star']}星" if (r.get("star") or 1) > 1 else "")
        extras = []
        if detail and r.get("equipment"):
            extras.append("、".join(r["equipment"]))
        if detail and r.get("equipment2"):
            extras.append("次选 " + "、".join(r["equipment2"]))
        if detail and r.get("spotlights"):
            extras.append("光环 " + "/".join(r["spotlights"]))
        return text + (f"[{';'.join(extras)}]" if extras else "")

    def board(b: dict, detail: bool) -> str:
        sides = []
        for side, label in (("front", "前台"), ("back", "后台")):
            cells = [unit(r, detail) for r in b.get(side) or [] if _name(r)]
            if cells:
                sides.append(f"{label} " + "、".join(cells))
        return ";".join(sides)

    final_traits = _traits(g.stage_traits.get("final") or []) or "、".join(g.traits[:4])
    head = (f"「{g.title}」 主 C:{'、'.join(g.carries) or '?'}" + (f";节奏:{'、'.join(g.tempo)}" if g.tempo else "")
            + (f";适合投资环境:{'、'.join(g.environments)}" if g.environments else "")
            + (f";星徽攻略(要 {'、'.join(g.emblems)})" if g.emblems else "")
            + (";本期已过期" + (f"(失效:{'、'.join(g.expired_items)})" if g.expired_items else "") if g.sub_expired else ""))
    roster = roster_notes(g)
    if not full:
        return "\n".join([head, f"  最终阵容:{board(g.final, False)}", f"  核心羁绊:{final_traits}"]
                         + ([f"  试用 / 练度(作者原文):{roster}"] if roster else []))
    lines = [head]
    if roster:
        lines.append(f"  试用 / 练度(作者原文):{roster}")
    if g.idea:
        lines.append("  作者思路(原文):" + g.idea.replace("\n", "\n    "))
    for key, label in (("early", "前期"), ("middle", "中期")):
        b = g.boards.get(key) or {}
        text = board(b, False)
        traits = _traits(g.stage_traits.get(key) or [])
        if text or traits:
            lines.append(f"  {label}阵容:{text or '?'}" + (f";羁绊 {traits}" if traits else ""))
    lines.append(f"  最终阵容:{board(g.final, True)};羁绊 {final_traits}")
    eq = g.equip_order
    if eq.get("basic") or eq.get("compose"):
        lines.append("  装备:" + ";".join(p for p in (
            f"基础件先拿 {'、'.join(eq['basic'])}" if eq.get("basic") else "",
            f"合成顺序 {' → '.join(eq['compose'])}" if eq.get("compose") else "") if p))
    groups = g.augment_groups
    if groups.get("first") or groups.get("second"):
        lines.append("  推荐投资策略:" + ";".join(p for p in (
            f"第一组 {'、'.join(groups['first'])}" if groups.get("first") else "",
            f"第二组 {'、'.join(groups['second'])}" if groups.get("second") else "") if p))
    elif g.augments:
        lines.append(f"  推荐投资策略:{'、'.join(g.augments)}")
    return "\n".join(lines)
