"""界面决策:一个「回合」(一个备战阶段或一个需要选择的界面)内是多轮对话,回合之间不带对话,只带本局档案。

设计见 docs/TECH_DESIGN.md 第 8.5 节。每轮模型调用 act 给出一组操作;程序执行后,把执行结果和新的界面
作为这次工具调用的结果交回,模型接着判断。回合结束后对话丢弃,本局档案(ledger.py)里只留一行摘要。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, model_validator

from ..config import ModelEndpoint
from ..knowledge.index import NameIndex, format_entities
from ..llm import ImageInput, LLMError, LLMResponse, Message, ToolSpec, complete
from ..memory import Cues, Memory, format_for_prompt, recall
from ..vision.ocr import TextBox

PROMPTS = Path(__file__).parent / "prompts"
MAX_ACTIONS = 12
ZONES = ("front", "back", "bench")
ZONE_NAMES = {"front": "前台", "back": "后台", "bench": "备战席"}
PREPARE_ONLY = {"buy", "sell", "move", "refresh", "roll", "buy_exp", "ready", "compose", "equip", "open", "take"}
ROLL_REFRESHES = 10     # roll 不填 max_refresh 时最多刷新几次
ROLL_MAX = 30


class RollTarget(BaseModel):
    """roll 要找的一张牌:stars 是要凑到几星(场上 + 备战席一星的量够 1/3/9 张),count 是这次买几张(给了就按它)。"""
    name: str = Field(min_length=1)
    stars: int = Field(default=1, ge=1, le=3)
    count: int | None = Field(default=None, ge=1, le=9)


class Action(BaseModel):
    type: str = Field(
        pattern="^(click_text|click|drag|scroll|key|wait|pause|buy|sell|move|refresh|roll|buy_exp|ready|compose|equip|open"
                "|take|zoom|inspect|recheck)$")
    mark: int | None = Field(default=None, ge=1, le=100)
    region: tuple[float, float, float, float] | None = None
    mode: str = Field(default="hover", pattern="^(hover|click)$")
    text: str | None = None
    nth: int = 0
    x: float | None = Field(default=None, ge=0, le=1)
    y: float | None = Field(default=None, ge=0, le=1)
    x2: float | None = Field(default=None, ge=0, le=1)
    y2: float | None = Field(default=None, ge=0, le=1)
    key: str | None = None
    seconds: float | None = Field(default=None, ge=0, le=60)
    notches: int | None = Field(default=None, ge=-30, le=30)  # scroll:正数向上,负数向下
    slot: int | None = Field(default=None, ge=1, le=5)        # buy:商店第几格,从左到右 1~5
    zone: str | None = Field(default=None, pattern="^(front|back|bench)$")
    index: int | None = Field(default=None, ge=1, le=9)       # 格子编号,从左到右从 1 开始
    to_zone: str | None = Field(default=None, pattern="^(front|back|bench)$")
    to_index: int | None = Field(default=None, ge=1, le=9)
    times: int | None = Field(default=None, ge=1, le=20)      # buy_exp:买几次
    item: int | None = Field(default=None, ge=1, le=24)       # compose / equip:装备栏第几件
    onto: int | None = Field(default=None, ge=1, le=24)       # compose:拖到第几件上
    targets: list[RollTarget] | None = None                   # roll:要找的牌
    until: str | None = Field(default=None, pattern="^(all|any)$")   # roll:all 全部达成才停(默认),any 任意一个
    keep_gold: int | None = Field(default=None, ge=0, le=999)        # roll:保底金币,买卡和刷新都不低于它
    max_refresh: int | None = Field(default=None, ge=1, le=ROLL_MAX)  # roll:最多刷新几次
    reason: str = ""

    @model_validator(mode="after")
    def _required(self) -> "Action":
        need = {"click_text": ["text"], "click": [] if self.mark else ["x", "y"], "drag": ["x", "y", "x2", "y2"], "key": ["key"],
                "zoom": ["region"], "inspect": ["mark"],
                "buy": ["slot"], "sell": ["zone", "index"], "move": ["zone", "index", "to_zone", "to_index"],
                "compose": ["item", "onto"], "equip": ["item", "zone", "index"], "open": ["zone", "index"],
                "take": ["zone", "index", "text"], "roll": ["targets"]}
        missing = [f for f in need.get(self.type, []) if getattr(self, f) in (None, "") or getattr(self, f) == []]
        if missing:
            raise ValueError(f"{self.type} 操作缺少 {', '.join(missing)}")
        if self.region is not None:
            x, y, w, h = self.region
            if min(x, y) < 0 or min(w, h) <= 0 or x + w > 1 or y + h > 1:
                raise ValueError("region 必须是客户区内的 x,y,宽,高,范围 0~1")
        return self


class Note(BaseModel):
    kind: str = "教训"
    title: str
    content: str
    entities: list[str] = []


class ActInput(BaseModel):
    analysis: str
    alternatives: list[str] = Field(default_factory=list, max_length=10)
    actions: list[Action] = Field(min_length=1, max_length=MAX_ACTIONS)
    run_notes: str | None = None
    summary: str | None = None
    guide: str | None = None
    notes: list[Note] = []
    perception_issues: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _observation_actions(self):
        if len(self.actions) > 1 and any(a.type in ("zoom", "inspect", "recheck") for a in self.actions):
            raise ValueError("zoom、inspect、recheck 要单独提交,取得新观察后再决定操作")
        if any(a.mark is not None for a in self.actions[1:]):
            raise ValueError("标号只对应当前截图,只能用于一组操作的第一项")
        return self


_SLOT = {"type": "string", "enum": list(ZONES)}
ACT_TOOL = ToolSpec(
    name="act",
    description="提交局势判断、考虑过但没有采用的做法,以及要按顺序执行的操作。",
    parameters={
        "type": "object",
        "properties": {
            # 2026-11-18 维护者要求:分开写局势、没采用的做法和每个操作的理由,网页分块显示,方便对比不同模型的想法。
            # 只描述每块写什么,不给打法上的引导
            "analysis": {"type": "string", "description": "局势:你对当前局面的判断和依据"},
            "alternatives": {"type": "array", "items": {"type": "string"},
                             "description": "考虑过、但这一轮没有采用的做法,每条写做法和没采用的原因;没有就给空数组"},
            "actions": {
                "type": "array", "minItems": 1, "maxItems": MAX_ACTIONS,
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["buy", "sell", "move", "refresh", "roll", "buy_exp", "ready",
                                                            "compose", "equip", "open", "take", "click_text", "click", "drag",
                                                            "scroll", "key", "wait", "pause", "zoom", "inspect",
                                                            "recheck"]},
                        "mark": {"type": "integer", "minimum": 1, "maximum": 100},
                        "region": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
                        "mode": {"type": "string", "enum": ["hover", "click"]},
                        "slot": {"type": "integer"}, "zone": _SLOT, "index": {"type": "integer"},
                        "to_zone": _SLOT, "to_index": {"type": "integer"}, "times": {"type": "integer"},
                        "item": {"type": "integer"}, "onto": {"type": "integer"},
                        "text": {"type": "string"}, "nth": {"type": "integer"},
                        "x": {"type": "number"}, "y": {"type": "number"},
                        "x2": {"type": "number"}, "y2": {"type": "number"},
                        "key": {"type": "string"}, "seconds": {"type": "number"}, "notches": {"type": "integer"},
                        "targets": {"type": "array", "items": {"type": "object", "properties": {
                            "name": {"type": "string"}, "stars": {"type": "integer", "minimum": 1, "maximum": 3},
                            "count": {"type": "integer", "minimum": 1, "maximum": 9}}, "required": ["name"]}},
                        "until": {"type": "string", "enum": ["all", "any"]},
                        "keep_gold": {"type": "integer", "minimum": 0},
                        "max_refresh": {"type": "integer", "minimum": 1, "maximum": ROLL_MAX},
                        "reason": {"type": "string", "description": "这个操作的理由"},
                    },
                    "required": ["type", "reason"],
                },
            },
            "run_notes": {"type": "string", "description": "整体改写你的笔记(可选)"},
            "perception_issues": {"type": "array", "items": {"type": "string"},
                                  "description": "程序识别与截图不一致或不确定的地方,只记录问题,不直接改写事实"},
            "summary": {"type": "string", "description": "回合小结:在结束这个回合的那次 act 里填(备战阶段带 ready 的那次、"
                        "选择界面做出最终选择的那次),写这一回合做了什么、为什么、接下来打算什么,不超过 150 字;"
                        "会记进本局经历,之后的回合都能看到"},
            "guide": {"type": "string", "description": "改用另一篇攻略作为方向时,填「攻略参考」里的候选编号或标题(可选)"},
            "notes": {
                "type": "array",
                "items": {"type": "object", "properties": {
                    "kind": {"type": "string", "enum": ["机制", "打法", "教训"]}, "title": {"type": "string"},
                    "content": {"type": "string"}, "entities": {"type": "array", "items": {"type": "string"}}},
                    "required": ["title", "content"]},
            },
        },
        "required": ["analysis", "alternatives", "actions"],
    },
)


def load_rules(path: Path) -> str:
    if not path.exists():
        return "(暂无)"
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if "【待核实】" not in ln]
    return "\n".join(lines).strip()


@dataclass
class Episode:
    """一个回合的对话。key 用来判断下一步是不是还在同一个回合里。"""

    id: int
    key: tuple
    screen: str
    node: str | None
    role: str                       # 用哪个决策层的模型:tactical / strategic / chooser / navigator
    max_turns: int
    messages: list[Message] = field(default_factory=list)
    turns: int = 0
    pending: list[str] = field(default_factory=list)   # 等待执行结果的工具调用 id
    done: list[str] = field(default_factory=list)      # 本回合执行过的操作(给回合摘要)
    seen: set[str] = field(default_factory=set)        # 这段对话里已经给过描述的条目(外部 agent 整局共用一份)
    book: Any = None                                   # 本局档案(RunLedger):新出现的条目也追加进去
    waiting: bool = False                              # 上一轮模型只让等待
    cache_at: int = 0                                  # 第一条消息里本局经历的长度(在这里断开打缓存标记)
    summary: str = ""                                  # 模型写的回合小结(最近一次)
    analysis: str = ""                                 # 模型最近一次的判断(没写小结时代替)
    images: list[ImageInput] = field(default_factory=list)
    snapshot: str = ""
    annotation: str = ""
    request_messages: list[Message] = field(default_factory=list)
    chosen: dict[str, tuple[str, str]] = field(default_factory=dict)
    spotlight_role: str = ""
    free_looks: int = 0                                # 只做了 zoom / inspect 的轮数(不占决策次数,有上限)
    grace: int = 0                                     # 最后一轮操作被打断(合成后重排、操作没成功)后多给的一轮

    @property
    def last_turn(self) -> bool:
        return self.turns + 1 >= self.max_turns


@dataclass
class Decision:
    analysis: str
    actions: list[Action]
    run_notes: str | None
    notes: list[Note]
    prompt: str                    # 这一轮发给模型的新内容(开场或执行结果 + 新界面)
    response: LLMResponse | None
    recalled: list[str] = field(default_factory=list)
    error: str = ""
    episode: int = 0
    turn: int = 0
    role: str = ""
    guide: str | None = None       # 模型改选的攻略(候选编号或标题)
    summary: str | None = None     # 回合小结
    perception_issues: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)   # 考虑过但没采用的做法


class ScreenAgent:
    def __init__(self, endpoints: ModelEndpoint | dict[str, ModelEndpoint], index: NameIndex,
                 memories: list[Memory], rules: str):
        self.endpoints = endpoints if isinstance(endpoints, dict) else {"default": endpoints}
        self.index = index
        self.memories = memories
        self.system = (PROMPTS / "system.md").read_text(encoding="utf-8").replace("{rules}", rules)

    def endpoint(self, role: str) -> ModelEndpoint:
        return self.endpoints.get(role) or self.endpoints["default"]

    # ---------------------------------------------------------------- 发给模型的内容

    def _entities(self, boxes: list[TextBox], names: list[str] | None, episode: Episode) -> tuple[str, set[str]]:
        """界面上这段对话里还没给过说明的条目;有本局档案时同时存进档案。"""
        found = [e for e in self.index.link([b.text for b in boxes] + (names or [])) if e.name not in episode.seen]
        if episode.book is not None:
            for e in found:
                episode.book.add_entry(e.name, format_entities([e]))
        episode.seen.update(e.name for e in found)
        return (format_entities(found) if found else ""), {e.name for e in found}

    def _relevant(self, book, boxes: list[TextBox], names: list[str] | None) -> list[str]:
        """当前相关的条目(设计文档第 20.4 节,2026-11-18):界面上的(场上、备战席、商店、选项)、场上和备战席角色的羁绊、
        选的投资和光环、身上的装备和道具、笔记里提到的。不在里面的说明留在档案里,不再自动发。"""
        get = getattr(self.index, "get", lambda name: [])
        out: list[str] = []

        def take(e) -> None:
            book.add_entry(e.name, format_entities([e]))
            if e.name not in out:
                out.append(e.name)

        board = {e.name for e in self.index.link(names or [])}
        for e in self.index.link([b.text for b in boxes] + (names or [])):
            # 羁绊跟着角色走:界面上的羁绊名多是商店卡面上的标签,只带场上和备战席角色的(下面展开)和笔记里提到的
            if e.kind == "traits":
                continue
            take(e)
            if e.name in board:                   # 商店里的角色不展开羁绊,买了再说
                for trait in getattr(e, "links", ()):
                    for t in get(trait):
                        take(t)
        for name in book.kept():
            if name in book.entries and name not in out:
                out.append(name)
            for e in get(name):
                take(e)
        for e in getattr(self.index, "mentions", lambda text: [])(book.direction):
            take(e)
        return out

    def opening(self, episode: Episode, boxes: list[TextBox], *, ledger, hints: str = "",
                names: list[str] | None = None, nodes: set[str] | None = None) -> tuple[str, list[str]]:
        """回合的第一条消息:本局档案 + 当前界面。nodes 是当前和下一个节点的类型(记忆按它触发)。

        ledger 是本局档案(RunLedger)时(设计文档第 20.4 节):新出现的条目说明、第一次相关的经验追加进档案里只追加的块,
        档案渲染出来就带着它们,之后的回合不再当作新信息重发。ledger 是字符串时照旧把条目和经验放在当前界面后面。"""
        texts = [b.text for b in boxes]
        book = ledger if hasattr(ledger, "render") else None
        external = self.endpoint(episode.role).format == "external"
        present = {e.name for e in self.index.link(texts + (names or []))}
        if book is not None:
            episode.book = book
            book.relevant = self._relevant(book, boxes, names)
            # 这段对话里给过说明的:API 每个回合是新对话,就是这次开场带上的当前相关条目;
            # 外部 agent 保留整局对话,收到过的都算(和档案共用一份,回合中途新给的也记进去)
            episode.seen = book.delivered_entries if external else set(book.relevant)
            described = ""
        else:
            described, _ = self._entities(boxes, names, episode)
        cues = Cues(entities=present, texts=texts, nodes=set(nodes or ()), screen=episode.screen,
                    layer="战术" if episode.screen == "prepare" else "选择")
        if book is not None:
            cues.entities.update(n for n in (book.environment, book.companion, *book.strategies, *book.spotlights) if n)
        checkpoint = book is None or "战略检查点(" in hints or episode.screen == "environment_choice"
        if book is not None:
            # 经验每个回合开场都重新判断适用性(选完环境、进新节点、阵容或修正变了):检查点从全部记忆里找,
            # 其他时候只在这局想起过的里筛;不再适用的(例如没选的「经济严重过热」带出来的抓猪经验)退出当前输入
            pool = self.memories if checkpoint else [m for m in self.memories if m.id in book.lessons]
            hits = recall(pool, cues)
            for r in hits:
                m = r.memory
                book.add_lesson(m.id, f"- [{m.label()}] {m.title}\n  {m.content.strip()}")
            book.active = [r.memory.id for r in hits]
            episode.cache_at = book.stable_len()
            full = book.render()
            head = [book.render_delta() if external else full]
            episode.snapshot = full
        else:
            hits = recall(self.memories, cues) if checkpoint else []
            head = [ledger]
        parts = head + [
            f"## 当前界面\n程序判断的界面类型:{episode.screen}" + (f"\n{hints}" if hints else ""),
            episode.annotation or "## 界面文字(坐标, 文字)\n" + "\n".join(str(b) for b in boxes),
        ]
        if book is None:
            if described:
                parts.append("## 界面上出现的条目\n" + described)
            if hits:
                parts.append("## 相关经验\n" + format_for_prompt(hits))
        parts.append(self._ask(episode))
        text = "\n\n".join(p for p in parts if p)
        if book is not None:
            episode.snapshot += "\n\n" + "\n\n".join(parts[len(head):])
        return text, [r.memory.id for r in hits]

    def observation(self, episode: Episode, results: list[str], boxes: list[TextBox], *, screen: str,
                    hints: str = "", names: list[str] | None = None) -> str:
        """回合内后续每轮:上一组操作的执行结果 + 现在的界面。"""
        described, _ = self._entities(boxes, names, episode)
        parts = ["## 执行结果\n" + ("\n".join(f"- {r}" for r in results) or "(没有执行任何操作)"),
                 f"## 现在的界面\n程序判断的界面类型:{screen}" + (f"\n{hints}" if hints else ""),
                 episode.annotation or "## 界面文字(坐标, 文字)\n" + "\n".join(str(b) for b in boxes)]
        if described:
            parts.append("## 新出现的条目\n" + described)
        parts.append(self._ask(episode))
        text = "\n\n".join(parts)
        if episode.book is not None:
            episode.snapshot = episode.book.render() + "\n\n" + text
        return text

    @staticmethod
    def _ask(episode: Episode) -> str:
        if episode.screen == "prepare" and episode.last_turn:
            return ("这是本备战阶段最后一次决策:请把要做的事一次做完,以 ready 结束,并写回合小结(summary)。"
                    "这一轮的操作有没成功的,程序不会出战,之后会直接出战。")
        return "请判断当前局面,调用 act 给出下一步操作。"

    # ---------------------------------------------------------------- 调用模型

    def decide(self, episode: Episode, text: str, recalled: list[str] | None = None,
               on_delta: Callable[[str, str], None] | None = None) -> Decision:
        """把 text 交给模型(回合内有待回复的工具调用时作为它的结果),返回模型的决定。

        on_delta 收模型边想边写的片段(流式,网页上实时显示);输出不合法重问时会再从 reset 开始。
        接口调用失败时这一轮作废,回合的对话恢复原样,下次重新问。
        """
        snapshot = (list(episode.messages), list(episode.pending), episode.turns)
        if episode.pending:
            episode.messages += [Message(role="tool", tool_call_id=cid, content=text if i == 0 else "没有执行(每轮只执行第一个 act)",
                                         images=episode.images if i == 0 else [], snapshot=episode.snapshot if i == 0 else "")
                                 for i, cid in enumerate(episode.pending)]
            episode.pending = []
        else:
            episode.messages.append(Message(role="user", content=text,
                                            images=episode.images, snapshot=episode.snapshot,
                                            cache_at=0 if episode.messages else episode.cache_at))
        episode.turns += 1
        endpoint = self.endpoint(episode.role)
        base = dict(prompt=text, recalled=recalled or [], episode=episode.id, turn=episode.turns, role=episode.role)
        resp: LLMResponse | None = None
        last_error = ""
        for _ in range(2):  # 输出不合法时带着错误重问一次
            try:
                episode.request_messages = list(episode.messages)
                resp = complete(endpoint, self.system, episode.messages, [ACT_TOOL], on_delta=on_delta)
            except LLMError as e:
                episode.messages, episode.pending, episode.turns = snapshot
                return Decision("", [], None, [], response=None, error=str(e), **base)
            episode.messages.append(resp.as_message())
            calls = [c for c in resp.tool_calls if c.name == "act"]
            ids = [c.id for c in resp.tool_calls]
            call = calls[0] if calls else None
            if call is None or call.parse_error:
                last_error = call.parse_error if call else "没有调用 act 工具"
            else:
                try:
                    data = ActInput.model_validate(call.arguments)
                    wrong = [a.type for a in data.actions if a.type in PREPARE_ONLY] if episode.screen != "prepare" else []
                    if wrong:
                        raise ValueError(f"{'、'.join(wrong)} 只能在备战界面使用")
                    episode.pending = [call.id] + [i for i in ids if i != call.id]
                    episode.waiting = all(a.type == "wait" for a in data.actions)
                    episode.analysis = data.analysis
                    if data.summary:
                        episode.summary = data.summary.strip()[:200]
                    return Decision(data.analysis, data.actions, data.run_notes, data.notes, response=resp,
                                    guide=data.guide, summary=data.summary, perception_issues=data.perception_issues,
                                    alternatives=[a.strip() for a in data.alternatives if a.strip()], **base)
                except ValidationError as e:
                    last_error = "; ".join(f"{'.'.join(map(str, er['loc']))}: {er['msg']}" for er in e.errors()[:5])
                except ValueError as e:
                    last_error = str(e)
            retry = f"输出无效:{last_error}。请重新调用 act。"
            if ids:
                episode.messages += [Message(role="tool", tool_call_id=i, content=retry) for i in ids]
            else:
                episode.messages.append(Message(role="user", content=retry))
        return Decision("", [], None, [], response=resp, error=last_error, **base)


def _f(v: float | None) -> str:
    return "?" if v is None else f"{v:.3f}"


def slot_text(zone: str | None, index: int | None) -> str:
    return f"{ZONE_NAMES.get(zone or '', zone)}{index}号"


def action_text(a: Action) -> str:
    match a.type:
        case "buy":
            return f"买商店第 {a.slot} 格"
        case "sell":
            return f"卖掉{slot_text(a.zone, a.index)}"
        case "move":
            return f"{slot_text(a.zone, a.index)} → {slot_text(a.to_zone, a.to_index)}"
        case "refresh":
            return "刷新商店"
        case "roll":
            want = "、".join(f"{t.name}" + (f"×{t.count}" if t.count else f" {t.stars}星" if t.stars > 1 else "")
                            for t in a.targets or [])
            return (f"D 牌找 {want}(" + ("任意一个就停" if a.until == "any" else "全部找到才停")
                    + f",保底 {a.keep_gold or 0} 金,最多刷新 {min(a.max_refresh or ROLL_REFRESHES, ROLL_MAX)} 次)")
        case "buy_exp":
            return f"买经验 ×{a.times or 1}"
        case "ready":
            return "出战"
        case "compose":
            return f"合成装备 {a.item}号 + {a.onto}号"
        case "equip":
            return f"装备 {a.item}号 → {slot_text(a.zone, a.index)}"
        case "open":
            return f"看{slot_text(a.zone, a.index)}道具里的选项"
        case "take":
            return f"从{slot_text(a.zone, a.index)}的道具里拿「{a.text}」"
        case "click_text":
            return f"点击「{a.text}」" + (f"(第{a.nth + 1}个)" if a.nth else "")
        case "click":
            return f"点击标号 {a.mark}" if a.mark else f"点击 ({_f(a.x)},{_f(a.y)})"
        case "zoom":
            return f"放大区域 {a.region}"
        case "inspect":
            return f"查看标号 {a.mark}({a.mode})"
        case "recheck":
            return "复查(重新截图识别)"
        case "drag":
            return f"拖拽 ({_f(a.x)},{_f(a.y)})→({_f(a.x2)},{_f(a.y2)})"
        case "scroll":
            return f"滚轮 {a.notches or -3:+d} 格 @({a.x or 0.5:.3f},{a.y or 0.5:.3f})"
        case "key":
            return f"按键 {a.key}"
        case "wait":
            return f"等待 {a.seconds or 3}s"
        case _:
            return "暂停"


def to_jsonable(d: Decision) -> dict[str, Any]:
    r = d.response
    return {"analysis": d.analysis, "alternatives": d.alternatives,
            "actions": [a.model_dump(exclude_none=True) for a in d.actions],
            "run_notes": d.run_notes, "notes": [n.model_dump() for n in d.notes], "recalled": d.recalled,
            "error": d.error, "reasoning": r.reasoning if r else "", "usage": r.usage.model_dump() if r else None,
            "latency_s": r.latency_s if r else None, "model": r.model if r else "",
            "episode": d.episode, "turn": d.turn, "role": d.role, "prompt": d.prompt, "guide": d.guide,
            "summary": d.summary, "perception_issues": d.perception_issues}
