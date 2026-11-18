"""官方阵容攻略:由模型自己检索(2026-11-18 维护者定)。程序不再替模型挑方向、不再在检查点列候选;
模型用 lookup 按角色、羁绊、投资环境查摘要,按编号看全文,想一直看着哪篇就在 guide 字段挂上。
攻略只是玩家分享的阵容,买谁、怎么站、给谁装备由模型判断(设计文档 1.6)。
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

SEARCH_TOP = 5          # 检索最多返回几篇
_SPLIT = re.compile(r"[\s,,、;;/|]+")


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
    code: str = ""                                      # 给模型用的编号(G1、G2……,按使用次数排,同一份知识库里固定)
    favour: int = 0
    emblems: list[str] = field(default_factory=list)    # 最终阵容首选装备里的星徽(星徽攻略)
    sub_expired: bool = False                           # 本期(子赛季)已过期:内容可能有失效的
    expired_items: list[str] = field(default_factory=list)

    @property
    def final_names(self) -> set[str]:
        return {r["name"] for side in ("front", "back") for r in self.final.get(side) or [] if isinstance(r, dict)}


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
                expired_items=list(g.get("expired_items") or []), favour=int(g.get("favour") or 0)))
        self.guides.sort(key=lambda g: (-g.use_count, -g.favour, g.title))
        for i, g in enumerate(self.guides, start=1):
            g.code = f"G{i}"
        self.by_id = {g.id: g for g in self.guides}
        self.by_code = {g.code: g for g in self.guides}

    def search(self, query: str, owned: list[str] | None = None, top: int = SEARCH_TOP) -> list[Guide]:
        """按角色、羁绊、投资环境、节奏或标题里的词查攻略:对上的词越多、越是主 C 越靠前,一样时按使用次数。"""
        words = [w for w in _SPLIT.split(query or "") if w]
        if not words:
            return []
        scored = []
        for g in self.guides:
            hit, score = 0, 0.0
            # 最终阵容的羁绊按人数加一点(5 反击比 2 反击更像「反击阵容」);只在前中期出现的羁绊分低
            final = {t["name"]: int(t.get("count") or 0) for t in g.stage_traits.get("final") or []
                     if isinstance(t, dict) and t.get("name")}
            early = {t.get("name", "") for k in ("early", "middle") for t in g.stage_traits.get(k) or [] if isinstance(t, dict)}
            for w in words:
                s = (3 if w in g.carries else 2 if w in g.final_names else 1 if w in (g.early | g.middle) else 0)
                s = max(s, max((2 + min(c, 9) / 10 for t, c in final.items() if w in t), default=0),
                        1 if any(w in t for t in early) else 0,
                        2 if any(w in e for e in g.environments) else 0,
                        1 if any(w in t for t in g.tempo) or w in g.title else 0)
                if s:
                    hit, score = hit + 1, score + s
            if hit:
                scored.append((hit, score, g.use_count, g.favour, g))
        scored.sort(key=lambda x: x[:4], reverse=True)
        return [x[4] for x in scored[:top]]

    @property
    def environments(self) -> set[str]:
        return {e for g in self.guides for e in g.environments}

    def find(self, key: str) -> Guide | None:
        """按编号(G12)、id 或标题(含部分标题)找攻略。"""
        key = key.strip().strip("「」")
        if key.upper() in self.by_code:
            return self.by_code[key.upper()]
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


def brief(g: Guide, owned: list[str] | None = None) -> str:
    """检索结果里的一篇:编号、标题、主 C、核心羁绊、节奏、投资环境、热度,以及和现在的角色重合的部分。"""
    traits = _traits(g.stage_traits.get("final") or []) or "、".join(g.traits[:4])
    hot = f"{g.use_count} 次使用" if g.use_count else f"{g.favour} 收藏"
    parts = [f"{g.code}「{g.title}」主 C:{'、'.join(g.carries) or '?'}", f"核心羁绊:{traits}" if traits else "",
             f"节奏:{'、'.join(g.tempo)}" if g.tempo else "",
             f"适合投资环境:{'、'.join(g.environments)}" if g.environments else "", hot,
             f"星徽攻略(要 {'、'.join(g.emblems)})" if g.emblems else "", "本期已过期" if g.sub_expired else ""]
    names = g.final_names | g.early | g.middle
    same = [n for n in dict.fromkeys(owned or []) if n in names]
    if same:
        parts.append(f"和你现在场上、备战席重合:{'、'.join(same)}")
    return ";".join(p for p in parts if p)


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
    head = (f"{g.code}「{g.title}」 主 C:{'、'.join(g.carries) or '?'}" + (f";节奏:{'、'.join(g.tempo)}" if g.tempo else "")
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
