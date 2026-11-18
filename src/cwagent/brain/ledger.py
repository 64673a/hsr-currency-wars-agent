"""本局档案:跨回合需要记住的信息,每个回合开头交给模型。

- 本局经历:只往后追加、写下就不再改。每个回合结束时程序记一行(做了什么 + 模型自己写的回合小结),
  战斗结果、开局信息、本局学到的教训也记在这里。保留整局所有回合(维护者要求),一局大约 60~90 个回合、
  两三万字。它放在回合第一条消息的最前面,只增不改,有利于前缀稳定(能不能命中缓存还看厂商)。
- 整局已知事实:程序从界面读到的,出现新事实时追加,放在经历后面(追加时不打断经历这段前缀)。
- 条目说明、想起的经验:档案里留全(整局见过、想起过的都在),每个回合开场只发当前相关、当前适用的那部分
  (2026-11-18:原来只加不删,没选的投资环境、商店里没买的角色、不再适用的经验一直带到局末)。
- 当前状态:当前参考的攻略、角色身上的装备、没开的道具、模型的笔记,随时会变,放在最后。

设计见 docs/TECH_DESIGN.md 第 8.5、20.4 节。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_LINE = 600       # 本局经历每条最长
MAX_DIRECTION = 800

# 位面详情里节点说明开头的名字 → 节点类型(记忆的 triggers.nodes 用这些英文名)。
# 只看节点号后面紧跟的名字:「1-8 幕后茶歇 特殊的奖励节点……」后文里也有「奖励节点」,不能按全文匹配
NODE_LABELS = (("首领节点", "boss"), ("遭遇节点", "encounter"), ("战斗节点", "battle"), ("奖励节点", "reward"),
               ("补给", "supply"), ("投资策略", "strategy"), ("幕后茶歇", "tea_break"))


def node_types(lines: list[str]) -> dict[str, set[str]]:
    """位面详情里每个节点的说明行 → {节点号: 类型}。

    实机的说明行如「1-1 奖励节点 难度较低的节点……」「1-3 投资策略 ……选择完毕后将继续备战【战斗节点】。」;
    投资策略选完还要打一场战斗,所以同时算 battle。认不出名字的节点不收。
    """
    out: dict[str, set[str]] = {}
    for line in lines:
        m = re.match(r"\s*([1-3]-\d{1,2})\s+(\S+)(.*)", line)
        if not m:
            continue
        node, label, rest = m.groups()
        types = {t for key, t in NODE_LABELS if label.startswith(key)}
        if "strategy" in types and "战斗节点" in rest:
            types.add("battle")
        if types:
            out[node] = types
    return out


def next_node(node: str) -> str | None:
    m = re.fullmatch(r"([1-3])-(\d{1,2})", node or "")
    return f"{m.group(1)}-{int(m.group(2)) + 1}" if m else None


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
    node_kinds: dict[str, set[str]] = field(default_factory=dict)   # 节点号 → 类型(reward、battle……),给记忆检索
    direction: str = ""                                      # 对局方向与计划(模型写)
    equipped: dict[str, list[str]] = field(default_factory=dict)   # 角色 → 程序给他装上的装备
    items: dict[int, tuple[str, list[str]]] = field(default_factory=dict)   # 备战席几号的道具:(名字, 看过的选项)
    environment: str = ""                                    # 这局选的投资环境(给攻略打分用)
    guide: str = ""                                          # 当前参考的攻略(卡片文字)
    nodes: dict[str, NodeRecord] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)         # 本局经历,只追加
    facts: list[str] = field(default_factory=list)           # 整局已知事实:程序从界面读到的(开局、位面节点与词条、拿到的投资)
    # 条目说明和经验:档案里留全,开场只发当前那部分(设计文档第 20.4 节)
    entries: dict[str, str] = field(default_factory=dict)    # 条目名 → 官方描述(整局见过的都留着)
    relevant: list[str] = field(default_factory=list)        # 当前相关的条目名(回合开场时重算)
    lessons: dict[str, str] = field(default_factory=dict)    # 记忆 id → 经验原文(整局想起过的都留着)
    active: list[str] = field(default_factory=list)          # 当前适用的经验 id(回合开场时重新判断)
    strategies: list[str] = field(default_factory=list)      # 拿到的投资策略
    spotlights: list[str] = field(default_factory=list)      # 选过的主角光环
    held: list[str] = field(default_factory=list)            # 道具栏里的道具(最近一次读到的)
    delivered: dict[str, int] = field(default_factory=dict)  # 外部 agent 已接收的经历、事实条数
    delivered_entries: set[str] = field(default_factory=set)        # 外部 agent 收到过说明的条目
    delivered_lessons: tuple[str, ...] = ()                  # 外部 agent 上次收到的当前适用经验
    lessons_sent: set[str] = field(default_factory=set)      # 外部 agent 收到过原文的经验
    delivered_state: dict[str, str] = field(default_factory=dict)   # 外部 agent 上次收到的当前状态各小节

    def node(self, name: str) -> NodeRecord:
        if name not in self.nodes:
            self.nodes[name] = NodeRecord(name)
        return self.nodes[name]

    def add(self, text: str) -> None:
        """往本局经历里追加一条。写下就不再改,保证前缀缓存。"""
        text = text.strip()
        if text:
            self.history.append(text[:MAX_LINE])

    def add_fact(self, text: str) -> bool:
        """整局已知事实追加一条(和已有的完全相同就不加)。返回是否加了。"""
        text = text.strip()[:MAX_LINE * 2]
        if not text or text in self.facts:
            return False
        self.facts.append(text)
        return True

    def add_entry(self, name: str, text: str) -> bool:
        """条目说明存进档案(已有就不动)。返回是不是第一次见。"""
        if not name or name in self.entries:
            return False
        self.entries[name] = text.strip()
        return True

    def add_lesson(self, memory_id: str, text: str) -> bool:
        if memory_id in self.lessons:
            return False
        self.lessons[memory_id] = text.strip()
        return True

    def kept(self) -> list[str]:
        """不在界面上也一直相关的条目:选的投资环境、拿到的投资策略、主角光环、身上的装备、道具。
        道具的说明在档案里的名字是「道具:名字」(程序从道具图鉴写进去的)。"""
        names = ([self.environment] if self.environment else []) + self.strategies + self.spotlights
        names += [item for items in self.equipped.values() for item in items]
        names += [name for name, _ in self.items.values()] + [f"道具:{n}" for n in self.held]
        return list(dict.fromkeys(n for n in names if n))

    def kinds_near(self, node: str | None) -> set[str]:
        """当前节点和下一个节点的类型(记忆按「当前或下一个节点」触发,设计文档 5.4)。"""
        if not node:
            return set()
        return self.node_kinds.get(node, set()) | self.node_kinds.get(next_node(node) or "", set())

    def equip(self, role: str, item: str) -> None:
        """程序执行 equip 成功后记一笔。一个角色最多穿 3 件(规则卡),多出来的说明前面的记录已经过期,只留最近 3 件。"""
        items = self.equipped.setdefault(role, [])
        items.append(item)
        del items[:-3]

    def unequip(self, role: str | None = None) -> None:
        """角色卖掉、或者用了拆装道具:去掉对应记录(role 为 None 时全部清空,拆的是谁看不出来)。"""
        if role is None:
            self.equipped.clear()
        else:
            self.equipped.pop(role, None)

    def set_direction(self, text: str) -> None:
        self.direction = text.strip()[:MAX_DIRECTION]

    def render_facts(self) -> str:
        head = "## 整局已知事实(程序从游戏界面读到的,出现新事实时追加)\n- 难度 " + (self.difficulty or "?")
        return head + "".join(f"\n- {f}" for f in self.facts)

    def render_history(self) -> str:
        head = "## 本局经历(程序记录 + 你写的回合小结,从旧到新)"
        return head + ("".join(f"\n- {h}" for h in self.history) if self.history else "\n(还没有)")

    def stable_len(self) -> int:
        """本局经历之后的缓存边界:经历只往后加,下个回合开头到这里为止的旧内容不变。"""
        return len(self.render_history())

    history_len = stable_len   # 旧名字

    def state_sections(self) -> list[tuple[str, str]]:
        """当前状态的各小节:(标题, 内容)。"""
        out = []
        if self.guide:
            out.append(("当前参考的攻略(只是方向,买谁、怎么站、给谁装备看局面判断)", self.guide))
        if self.equipped:
            out.append(("程序给角色穿上的装备(只记程序执行的 equip;用拆装道具、卖掉角色后会清掉,其他变化以截图为准)",
                        "\n".join(f"- {r}:{'、'.join(items)}" for r, items in self.equipped.items())))
        if self.items:
            out.append(("备战席里还没开的道具(程序看过的选项)", "\n".join(
                f"- 备战席{i}号 {name}:{'、'.join(opts)}" for i, (name, opts) in sorted(self.items.items()))))
        out.append(("我的笔记(只有你能改:在 run_notes 里整体改写)", self.direction or "(还没写)"))
        return out

    def render_state(self) -> str:
        return "## 当前状态\n" + "\n\n".join(f"### {t}\n{c}" for t, c in self.state_sections())

    def render_entries(self) -> str:
        current = [self.entries[n] for n in self.relevant if n in self.entries]
        return "## 条目说明(和当前局面相关的官方描述)\n" + "\n".join(current) if current else ""

    def render_lessons(self) -> str:
        current = [self.lessons[i] for i in self.active if i in self.lessons]
        return ("## 想起的经验(过去积累的,可能不完整或有错,结合局面判断;按当前局面筛选)\n" + "\n".join(current)
                if current else "")

    def render(self) -> str:
        """从稳定到易变:本局经历(只追加)、整局已知事实(偶尔追加)、当前相关的条目说明和经验、当前状态。"""
        parts = [self.render_history(), self.render_facts(), self.render_entries(), self.render_lessons(),
                 self.render_state()]
        return "\n\n".join(p for p in parts if p)

    def render_delta(self) -> str:
        """外部 agent 保留历史对话,这里只交付上次之后的变化:新增的经历和事实、还没收到过的当前相关条目、
        变了的适用经验(明确说哪些不再适用:它已经收到的旧经验不会因为这边换了列表就消失);当前状态只发变了的小节。"""
        parts = []
        if not self.delivered:
            parts.append("## 本局\n难度 " + (self.difficulty or "?"))
        for key, title in (("history", "新增经历"), ("facts", "新增已知事实")):
            items = getattr(self, key)
            start = self.delivered.get(key, 0)
            if len(items) > start:
                parts.append(f"## {title}\n" + "\n".join(items[start:]))
            self.delivered[key] = len(items)
        new = [n for n in self.relevant if n in self.entries and n not in self.delivered_entries]
        if new:
            parts.append("## 新条目说明\n" + "\n".join(self.entries[n] for n in new))
            self.delivered_entries.update(new)
        active = tuple(i for i in self.active if i in self.lessons)
        if active != self.delivered_lessons:
            if active:
                lines = [self.lessons[i] if i not in self.lessons_sent
                         else self.lessons[i].splitlines()[0] + "(原文之前给过)" for i in active]
                parts.append("## 当前适用的经验(已更新;之前给过、这里没列出的不再适用)\n" + "\n".join(lines))
            else:
                parts.append("## 当前适用的经验\n(之前给过的经验都不再适用)")
            self.lessons_sent.update(active)
            self.delivered_lessons = active
        sections = self.state_sections()
        changed = [(t, c) for t, c in sections if self.delivered_state.get(t) != c]
        gone = [t for t in self.delivered_state if t not in {t for t, _ in sections}]
        self.delivered_state = dict(sections)
        if changed or gone:
            parts.append("## 当前状态(只列有变化的小节,其余和之前相同)\n" + "\n\n".join(
                [f"### {t}\n{c}" for t, c in changed] + [f"### {t}\n(已清空)" for t in gone]))
        else:
            parts.append("## 当前状态\n(和之前相同)")
        return "\n\n".join(parts)


def compact_node(line: str) -> str:
    """位面详情的节点说明行压成一小段:节点号 + 名字 + 画面上的标记(狂化状态、主角光环、奇遇伙伴带来的效果)。
    说明正文(「难度较低的节点,拥有无限的行动值……」)每局都一样,放进规则卡,这里不重复。"""
    m = re.match(r"\s*([1-3]-\d{1,2})\s+(\S+)(.*)", line)
    if not m:
        return line.strip()[:40]
    node, label, rest = m.groups()
    marks = [k for k in ("狂化状态", "主角光环") if k in rest]
    effect = re.search(r"本局效果[:：](.+?)(?:$|\s{2})", rest)
    if "继续备战【战斗节点】" in rest:
        marks.append("选完打战斗")
    text = f"{node} {label}" + (f"({'、'.join(marks)})" if marks else "")
    return text + (f"[{effect.group(1).strip()}]" if effect else "")
