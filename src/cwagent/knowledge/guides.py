"""按当前局面挑攻略:开局先定一篇作为初始方向,之后每个备战阶段按场上、备战席、商店里的角色和投资环境重新排,
别的攻略明显更合适(例如商店刷出了它的主 C)时提示可以转向(维护者要求:动态选攻略,如 6 级时商店刷出刃就可以
转去刃 C 的攻略)。攻略只给方向,买谁、怎么站、给谁装备仍由模型判断(设计文档 1.6)。
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
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
                return {n for side in ("front", "back") for n in b.get(side) or [] if isinstance(n, str)}

            self.guides.append(Guide(
                id=g["id"], title=g.get("title", ""), carries=list(g.get("carries") or []),
                final=g.get("final_board") or {}, early=names("early"), middle=names("middle"),
                traits=[t["name"] for t in g.get("key_traits") or [] if isinstance(t, dict)],
                environments=list(g.get("environments") or []), tempo=list(g.get("tempo_labels") or []),
                equip_order=g.get("equip_order") or {}, idea=(g.get("core_idea") or "").strip(),
                augments=list(g.get("recommended_augments") or []), use_count=int(g.get("use_count") or 0),
                hard=bool(g.get("support_hard"))))
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


def card(g: Guide, *, full: bool = True) -> str:
    """给模型看的攻略卡片。full=False 时只给要点(候选攻略用)。"""
    def board(b: dict) -> str:
        sides = []
        for side, label in (("front", "前台"), ("back", "后台")):
            cells = []
            for r in b.get(side) or []:
                if isinstance(r, dict):
                    star = f"{r.get('star')}星" if r.get("star") and r.get("star") > 1 else ""
                    equip = f"[{'、'.join(r['equipment'])}]" if full and r.get("equipment") else ""
                    cells.append(f"{r['name']}{star}{equip}")
                else:
                    cells.append(str(r))
            if cells:
                sides.append(f"{label} " + "、".join(cells))
        return ";".join(sides)

    lines = [f"「{g.title}」 主 C:{'、'.join(g.carries) or '?'}" + (f";节奏:{'、'.join(g.tempo)}" if g.tempo else "")
             + (f";适合投资环境:{'、'.join(g.environments)}" if g.environments else ""),
             f"  最终阵容:{board(g.final)}", f"  核心羁绊:{'、'.join(g.traits[:4])}"]
    if full:
        if g.early or g.middle:
            lines.append(f"  前期:{'、'.join(sorted(g.early))};中期:{'、'.join(sorted(g.middle))}")
        eq = g.equip_order
        if eq.get("compose"):
            lines.append(f"  装备:先合 {'、'.join(eq['compose'])}(基础件 {'、'.join(eq.get('basic') or [])})")
        if g.augments:
            lines.append(f"  推荐投资策略:{'、'.join(g.augments)}")
        if g.idea:
            lines.append(f"  思路:{g.idea[:160]}")
    return "\n".join(lines)
