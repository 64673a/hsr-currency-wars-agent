"""在决策点想起相关记忆。不用向量检索,按触发条件命中多少打分。设计见 docs/TECH_DESIGN.md 5.4。"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Memory

ENTITY_WEIGHT, NODE_WEIGHT, SCREEN_WEIGHT = 3.0, 2.0, 1.0
GENERIC_SCORE = 0.5
CONFIDENCE_BONUS = {"high": 0.3, "medium": 0.15, "low": 0.0}


@dataclass
class Cues:
    """从当前局面收集的线索。"""

    entities: set[str] = field(default_factory=set)  # 局面里出现的所有名字
    texts: list[str] = field(default_factory=list)    # 节点名、事件文字等自由文本,按子串匹配
    nodes: set[str] = field(default_factory=set)      # 当前和下一个节点类型
    screen: str | None = None
    layer: str | None = None                          # 战略 / 战术 / 选择 / 事件


@dataclass
class Recalled:
    memory: Memory
    score: float
    reasons: list[str]


def score(memory: Memory, cues: Cues, *, include_draft: bool = False) -> Recalled | None:
    if memory.status in ("rejected", "archived") or (memory.status == "draft" and not include_draft):
        return None
    if memory.scope and cues.layer and cues.layer not in memory.scope:
        return None
    t = memory.triggers
    reasons: list[str] = []
    total = 0.0
    for name in t.entities:
        if name in cues.entities or any(name in text for text in cues.texts):
            total += ENTITY_WEIGHT
            reasons.append(name)
    for node in t.nodes:
        if node in cues.nodes:
            total += NODE_WEIGHT
            reasons.append(f"节点:{node}")
    if cues.screen and cues.screen in t.screens:
        total += SCREEN_WEIGHT
        reasons.append(f"界面:{cues.screen}")
    if t.empty():
        total, reasons = GENERIC_SCORE, ["通用"]
    elif total == 0:
        return None
    total += CONFIDENCE_BONUS[memory.confidence]
    total += max(-1.0, min(1.0, 0.1 * (memory.stats.helped - memory.stats.hurt)))
    if memory.status == "draft":
        total -= 0.5
    return Recalled(memory, round(total, 3), reasons)


def recall(memories: list[Memory], cues: Cues, *, limit: int = 6, max_chars: int = 1500,
           include_draft: bool = False) -> list[Recalled]:
    hits = [r for m in memories if (r := score(m, cues, include_draft=include_draft)) is not None]
    hits.sort(key=lambda r: (-r.score, r.memory.created))
    out, used = [], 0
    for r in hits:
        size = len(r.memory.title) + len(r.memory.content)
        if len(out) >= limit or (out and used + size > max_chars):
            break
        out.append(r)
        used += size
    return out


def format_for_prompt(recalled: list[Recalled]) -> str:
    if not recalled:
        return ""
    lines = ["以下是过去积累的经验,按与当前局面的相关程度排列。它们可能不完整或有错,请结合当前局面判断:"]
    for i, r in enumerate(recalled, 1):
        lines.append(f"{i}. [{r.memory.kind}] {r.memory.title}\n   {r.memory.content.strip()}")
    return "\n".join(lines)
