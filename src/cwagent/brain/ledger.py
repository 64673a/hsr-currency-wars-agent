"""本局档案:跨回合需要记住的信息,由程序维护(对局方向除外),每个回合开头交给模型。

只在节点切换、回合结束时变化,一个回合内不变,所以放在回合对话的最前面也能命中前缀缓存。
条目数有上限,整局下来长度也有界,不需要让模型做总结。设计见 docs/TECH_DESIGN.md 第 8.5 节。
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_NODES = 15      # 只列最近这么多个节点
MAX_EVENTS = 20     # 只列最近这么多条选择记录
MAX_DIRECTION = 800


@dataclass
class NodeRecord:
    node: str
    start: str = ""          # 进入备战时的数字(生命、金币、等级……)
    lineup: str = ""         # 出战时的阵容
    actions: str = ""        # 这个备战阶段做了什么
    battle: str = ""         # 战斗曲线摘要

    def line(self) -> str:
        parts = [p for p in (self.start, f"阵容:{self.lineup}" if self.lineup else "",
                             f"操作:{self.actions}" if self.actions else "",
                             f"战斗:{self.battle}" if self.battle else "") if p]
        return f"- {self.node}:" + ";".join(parts)


@dataclass
class RunLedger:
    difficulty: str = ""
    companion: str = ""
    intro: str = ""                                          # 开局界面上的首领、怪物词条等原文
    plane_nodes: str = ""                                    # 本位面的节点序列(程序在位面开始时收集)
    direction: str = ""                                      # 对局方向与计划(模型写)
    events: list[str] = field(default_factory=list)          # 各次选择(投资环境、投资策略、遭遇……)
    equipped: dict[str, list[str]] = field(default_factory=dict)   # 角色 → 程序给他装上的装备
    nodes: dict[str, NodeRecord] = field(default_factory=dict)

    def node(self, name: str) -> NodeRecord:
        if name not in self.nodes:
            self.nodes[name] = NodeRecord(name)
        return self.nodes[name]

    def event(self, text: str) -> None:
        self.events = (self.events + [text])[-MAX_EVENTS:]

    def equip(self, role: str, item: str) -> None:
        self.equipped.setdefault(role, []).append(item)

    def set_direction(self, text: str) -> None:
        self.direction = text.strip()[:MAX_DIRECTION]

    def render(self) -> str:
        head = [f"难度 {self.difficulty}" if self.difficulty else "", f"奇遇伙伴:{self.companion}" if self.companion else ""]
        parts = ["## 本局档案(程序记录)\n" + ("- " + ";".join(h for h in head if h) if any(head) else "- (刚开局)")]
        if self.intro:
            parts.append("### 开局信息\n" + self.intro)
        if self.events:
            parts.append("### 做过的选择(从旧到新)\n" + "\n".join(f"- {e}" for e in self.events))
        if self.equipped:
            parts.append("### 角色身上的装备(程序记录,角色卖掉或换下后可能不准)\n" + "\n".join(
                f"- {r}:{'、'.join(items)}" for r, items in self.equipped.items()))
        if self.plane_nodes:
            parts.append("### 本位面的节点\n" + self.plane_nodes)
        if self.nodes:
            recent = list(self.nodes.values())[-MAX_NODES:]
            parts.append("### 打过的节点(从旧到新)\n" + "\n".join(r.line() for r in recent))
        parts.append("## 对局方向与计划(你自己写的,可以在 run_notes 里整体改写)\n" + (self.direction or "(还没写)"))
        return "\n\n".join(parts)
