"""本局档案:跨回合需要记住的信息,每个回合开头交给模型。分两部分:

- 本局经历:只往后追加、写下就不再改。每个回合结束时程序记一行(做了什么 + 模型自己写的回合小结),
  战斗结果、开局信息、本局学到的教训也记在这里。保留整局所有回合(维护者要求),一局大约 60~90 个回合、
  两三万字。它放在回合第一条消息的最前面,只增不改,下一个回合开头能命中到这里为止的前缀缓存。
- 当前状态:当前参考的攻略、角色身上的装备、没开的道具、本位面的节点、对局方向与计划,随时会变,放在后面。

设计见 docs/TECH_DESIGN.md 第 8.5 节。
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_LINE = 600       # 本局经历每条最长
MAX_DIRECTION = 800


@dataclass
class NodeRecord:
    """当前节点的信息,回合结束时写进本局经历。"""
    node: str
    start: str = ""          # 进入备战时的数字(生命、金币、等级……)
    lineup: str = ""         # 出战时的阵容
    logged: bool = False     # 进场数字已经写进本局经历


@dataclass
class RunLedger:
    difficulty: str = ""
    companion: str = ""
    intro: str = ""                                          # 开局界面上的首领、怪物词条等原文
    plane_nodes: str = ""                                    # 本位面的节点序列(程序在位面开始时收集)
    direction: str = ""                                      # 对局方向与计划(模型写)
    equipped: dict[str, list[str]] = field(default_factory=dict)   # 角色 → 程序给他装上的装备
    items: dict[int, tuple[str, list[str]]] = field(default_factory=dict)   # 备战席几号的道具:(名字, 看过的选项)
    environment: str = ""                                    # 这局选的投资环境(给攻略打分用)
    guide: str = ""                                          # 当前参考的攻略(卡片文字)
    nodes: dict[str, NodeRecord] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)         # 本局经历,只追加

    def node(self, name: str) -> NodeRecord:
        if name not in self.nodes:
            self.nodes[name] = NodeRecord(name)
        return self.nodes[name]

    def add(self, text: str) -> None:
        """往本局经历里追加一条。写下就不再改,保证前缀缓存。"""
        text = text.strip()
        if text:
            self.history.append(text[:MAX_LINE])

    def equip(self, role: str, item: str) -> None:
        self.equipped.setdefault(role, []).append(item)

    def set_direction(self, text: str) -> None:
        self.direction = text.strip()[:MAX_DIRECTION]

    def render_history(self) -> str:
        head = "## 本局经历(程序记录 + 你写的回合小结,从旧到新)\n- 难度 " + (self.difficulty or "?")
        return head + "".join(f"\n- {h}" for h in self.history)

    def history_len(self) -> int:
        return len(self.render_history())

    def render_state(self) -> str:
        parts = []
        if self.guide:
            parts.append("### 当前参考的攻略(只是方向,买谁、怎么站、给谁装备看局面判断)\n" + self.guide)
        if self.equipped:
            parts.append("### 角色身上的装备(程序记录,角色卖掉或换下后可能不准)\n" + "\n".join(
                f"- {r}:{'、'.join(items)}" for r, items in self.equipped.items()))
        if self.items:
            parts.append("### 备战席里还没开的道具(程序看过的选项)\n" + "\n".join(
                f"- 备战席{i}号 {name}:{'、'.join(opts)}" for i, (name, opts) in sorted(self.items.items())))
        if self.plane_nodes:
            parts.append("### 本位面的节点\n" + self.plane_nodes)
        parts.append("### 对局方向与计划(你自己写的,可以在 run_notes 里整体改写)\n" + (self.direction or "(还没写)"))
        return "## 当前状态\n" + "\n\n".join(parts)

    def render(self) -> str:
        """本局经历在前(只增不改,命中缓存),当前状态在后。"""
        return self.render_history() + "\n\n" + self.render_state()
