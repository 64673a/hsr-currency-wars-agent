"""名字索引:从界面上的文字里认出知识库里的条目(实体链接),附上官方描述。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .build import KIND_NAMES

LINK_KINDS = ("portals", "augments", "spotlights", "companions", "equipment", "roles", "traits")


@dataclass(frozen=True)
class Entity:
    kind: str
    name: str
    text: str  # 给模型看的一段说明


def _describe(kind: str, d: dict, carry: tuple[int, int] | None = None) -> str:
    if kind == "roles":
        # 官方的定位标签(输出 / 辅助 / 生存……)要写出来:模型曾把定位是「辅助」的开拓者当主 C 培养(维护者指出)
        skills = ";".join(f"{s['position']}「{s['name']}」〔{'、'.join(s.get('tags') or []) or '?'}〕:"
                          f"{s['stars'][0]['desc'] if s['stars'] else ''}" for s in d["skills"])
        tags = "、".join(d.get("tags") or []) or "未标注"
        text = f"{d['cost']}费 {d['position']},定位 {tags},羁绊 {'、'.join(t['name'] for t in d['traits'])}。{skills}"
        if carry and carry[0]:
            text += f"(攻略:出现在 {carry[0]} 篇的最终阵容,其中 {carry[1]} 篇是主 C)"
        return text
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


def _carry_stats(con) -> dict[str, tuple[int, int]]:
    """每个角色在攻略最终阵容里出现的篇数,和其中作为主 C 的篇数。"""
    seen: dict[str, list[int]] = {}
    try:
        rows = con.execute("SELECT data FROM guides").fetchall()
    except sqlite3.Error:
        return {}
    for (data,) in rows:
        g = json.loads(data)
        carries = set(g.get("carries") or [])
        for side in ("front", "back"):
            for r in (g.get("final_board") or {}).get(side) or []:
                name = r.get("name") if isinstance(r, dict) else r
                if not name:
                    continue
                c = seen.setdefault(name, [0, 0])
                c[0] += 1
                c[1] += int(name in carries or (isinstance(r, dict) and bool(r.get("carry"))))
    return {k: (v[0], v[1]) for k, v in seen.items()}


class NameIndex:
    def __init__(self, db_path: Path):
        self.by_name: dict[str, list[Entity]] = {}
        if not db_path.exists():
            return
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            carries = _carry_stats(con)
            for kind in LINK_KINDS:
                for name, data in con.execute(f"SELECT name, data FROM {kind}"):
                    d = json.loads(data)
                    ent = Entity(kind=kind, name=name, text=_describe(kind, d, carries.get(name) if kind == "roles" else None))
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


def format_entities(entities: list[Entity], limit: int = 40) -> str:
    lines = [f"- [{KIND_NAMES[e.kind]}] {e.name}:{e.text}" for e in entities[:limit]]
    return "\n".join(lines)
