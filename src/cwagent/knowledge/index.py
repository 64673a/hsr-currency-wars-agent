"""名字索引:从界面上的文字里认出知识库里的条目(实体链接),附上官方描述。"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .build import KIND_NAMES

LINK_KINDS = ("portals", "augments", "spotlights", "companions", "equipment", "roles", "traits")
PARTNERS = 4          # 搭配最多列几个


@dataclass(frozen=True)
class Entity:
    kind: str
    name: str
    text: str  # 给模型看的一段说明
    links: tuple[str, ...] = ()   # 相关条目:角色的羁绊(当前相关的条目说明要带上场上角色的羁绊)


def _describe(kind: str, d: dict, stats: str = "") -> str:
    if kind == "roles":
        # 官方的定位标签(输出 / 辅助 / 生存……)要写出来:模型曾把定位是「辅助」的开拓者当主 C 培养(维护者指出)
        skills = ";".join(f"{s['position']}「{s['name']}」〔{'、'.join(s.get('tags') or []) or '?'}〕:"
                          f"{s['stars'][0]['desc'] if s['stars'] else ''}" for s in d["skills"])
        tags = "、".join(d.get("tags") or []) or "未标注"
        text = f"{d['cost']}费 {d['position']},定位 {tags},羁绊 {'、'.join(t['name'] for t in d['traits'])}。{skills}"
        return text + (f"({stats})" if stats else "")
    if kind == "traits":
        layers = ";".join(f"{l['count']}人:{l['desc']}" for l in d.get("layers", []))
        return f"{d['type']}羁绊。{d.get('desc', '')} {layers}".strip()
    if kind == "equipment":
        recipe = "、".join(f"{r['a_name']}+{r['b_name']}" for r in d.get("recipes", []))
        return f"{d['category']}。{d.get('desc', '')}" + (f"(配方:{recipe})" if recipe else "")
    if kind == "augments":
        return f"{d['quality_name']}投资策略。{d['desc']}"
    if kind == "companions":
        return (d.get("desc") or "") + ("。流程影响:" + ";".join(d["flow_effects"]) if d.get("flow_effects") else "")
    return d.get("desc", "")


@dataclass
class _RoleTally:
    final: int = 0          # 最终阵容里有这个角色的攻略篇数
    carry: int = 0          # 其中当主 C 的
    use: int = 0            # 这些攻略的使用次数之和
    front: int = 0
    back: int = 0
    early: int = 0          # 前中期阵容里有这个角色的篇数
    supports: Counter = field(default_factory=Counter)   # 当主 C 时,同一篇最终阵容里的辅助(非主 C)
    carries: Counter = field(default_factory=Counter)    # 当辅助时,同一篇最终阵容里的主 C


def role_stats(con) -> dict[str, str]:
    """每个角色在官方阵容攻略里的统计,写进角色的条目说明,随场上、备战席、商店的角色一起发给模型
    (维护者 2026-11-19:不查攻略时模型不知道角色该上前台还是后台、哪些角色强,强力角色常在备战席吃灰;
    强度按攻略统计,不光看主 C,还要看主 C 搭配的辅助)。

    强度用「最终阵容有这个角色的攻略,使用次数占全部攻略使用次数的比例」和它在所有角色里的排名;使用次数为 0 的攻略
    (只按收藏留下的)不计入比例,但计入篇数。搭配按篇数加权,使用次数多的攻略权重大一些(1 + log10(1 + 使用次数))。"""
    try:
        rows = con.execute("SELECT data FROM guides").fetchall()
    except sqlite3.Error:
        return {}
    tally: dict[str, _RoleTally] = {}
    total = 0
    for (data,) in rows:
        g = json.loads(data)
        if g.get("expired"):
            continue
        use = int(g.get("use_count") or 0)
        weight = 1 + math.log10(1 + use)
        total += use
        board = g.get("final_board") or {}
        members = [(side, r.get("name") if isinstance(r, dict) else r, r) for side in ("front", "back")
                   for r in board.get(side) or []]
        members = [(side, n, r) for side, n, r in members if n]
        carries = set(g.get("carries") or []) | {n for _, n, r in members if isinstance(r, dict) and r.get("carry")}
        for side, name, _ in members:
            t = tally.setdefault(name, _RoleTally())
            t.final += 1
            t.use += use
            setattr(t, side, getattr(t, side) + 1)
            if name in carries:
                t.carry += 1
                for _, other, _ in members:
                    if other not in carries:
                        t.supports[other] += weight
            else:
                for c in carries:
                    if c != name:
                        t.carries[c] += weight
        boards = g.get("example_boards") or {}
        early = {r.get("name") if isinstance(r, dict) else r for stage in ("early", "middle")
                 for side in ("front", "back") for r in (boards.get(stage) or {}).get(side) or []}
        for name in early - {None, ""}:
            tally.setdefault(name, _RoleTally()).early += 1
    uses = [t.use for t in tally.values() if t.use]
    rank = {n: 1 + sum(u > t.use for u in uses) for n, t in tally.items() if t.use}   # 一样多的并列
    out = {}
    for name, t in tally.items():
        parts = []
        if t.final:
            head = f"攻略统计:最终阵容 {t.final} 篇有这个角色,其中 {t.carry} 篇当主 C"
            if t.use and total:
                pct = 100 * t.use / total
                head += f";这些攻略的使用次数占全部的 {pct:.0f}%" if pct >= 1 else ";这些攻略的使用次数占全部的不到 1%"
                head += f",在 {len(rank)} 个角色里排第 {rank[name]}"
            parts.append(head)
            parts.append(f"最终阵容里放前台 {t.front} 篇、后台 {t.back} 篇")
        if t.early:
            parts.append(f"前中期阵容 {t.early} 篇" + ("(只在前中期出现,多是过渡)" if not t.final else ""))
        # 只在这种身份够常见时列搭配:风堇 110 篇里只有 6 篇当主 C,列「当主 C 时配谁」会让模型以为它是主 C
        if t.supports and t.carry >= max(2, t.final / 4):
            parts.append("当主 C 时常配的辅助 " + "、".join(n for n, _ in t.supports.most_common(PARTNERS)))
        if t.carries and t.final - t.carry >= max(2, t.final / 4):
            parts.append("当辅助时常配的主 C " + "、".join(n for n, _ in t.carries.most_common(PARTNERS)))
        out[name] = ";".join(parts)
    return out


class NameIndex:
    def __init__(self, db_path: Path, guide_stats: bool = True):
        """guide_stats:角色说明里带攻略统计(关掉攻略 use_guides 时不带,对比实验不漏攻略信息)。"""
        self.by_name: dict[str, list[Entity]] = {}
        self._names: list[str] = []
        if not db_path.exists():
            return
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            stats = role_stats(con) if guide_stats else {}
            for kind in LINK_KINDS:
                for name, data in con.execute(f"SELECT name, data FROM {kind}"):
                    d = json.loads(data)
                    ent = Entity(kind=kind, name=name, text=_describe(kind, d, stats.get(name, "") if kind == "roles" else ""),
                                 links=tuple(t["name"] for t in d.get("traits") or []) if kind == "roles" else ())
                    if not any(e.kind == kind for e in self.by_name.get(name, [])):
                        self.by_name.setdefault(name, []).append(ent)
        finally:
            con.close()
        # 长名字优先匹配,避免「特战资金」被「资金」之类的短名字抢先
        self._names = sorted(self.by_name, key=len, reverse=True)

    def link(self, texts: list[str]) -> list[Entity]:
        """在一组界面文字里找出出现的条目(完全相同,或文字包含条目名且名字至少 2 个字)。"""
        found: dict[tuple[str, str], Entity] = {}
        for text in texts:
            hits = self.by_name.get(text)
            if not hits:
                for name in self._names:
                    if len(name) >= 2 and name in text:
                        hits = self.by_name[name]
                        break
            for e in hits or []:
                found.setdefault((e.kind, e.name), e)
        return list(found.values())

    def get(self, name: str) -> list[Entity]:
        return self.by_name.get(name, [])

    def mentions(self, text: str) -> list[Entity]:
        """一段自由文字(模型的笔记)里提到的所有条目;link 每段文字只认一个,这里全找出来。"""
        found: dict[tuple[str, str], Entity] = {}
        for name in self._names:
            if len(name) >= 2 and name in text:
                for e in self.by_name[name]:
                    found.setdefault((e.kind, e.name), e)
                text = text.replace(name, " ")    # 长名字先占掉,「特战资金」不再算出「资金」
        return list(found.values())


def format_entities(entities: list[Entity], limit: int = 40) -> str:
    lines = [f"- [{KIND_NAMES[e.kind]}] {e.name}:{e.text}" for e in entities[:limit]]
    return "\n".join(lines)
