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


def _describe(kind: str, d: dict) -> str:
    if kind == "roles":
        skills = ";".join(f"{s['position']}「{s['name']}」:{s['stars'][0]['desc'] if s['stars'] else ''}" for s in d["skills"])
        return f"{d['cost']}费 {d['position']},羁绊 {'、'.join(t['name'] for t in d['traits'])}。{skills}"
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


class NameIndex:
    def __init__(self, db_path: Path):
        self.by_name: dict[str, list[Entity]] = {}
        if not db_path.exists():
            return
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            for kind in LINK_KINDS:
                for name, data in con.execute(f"SELECT name, data FROM {kind}"):
                    d = json.loads(data)
                    ent = Entity(kind=kind, name=name, text=_describe(kind, d))
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
