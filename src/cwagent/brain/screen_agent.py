"""界面决策:一个「回合」(一个备战阶段或一个需要选择的界面)内是多轮对话,回合之间不带对话,只带本局档案。

设计见 docs/TECH_DESIGN.md 第 8.5 节。每轮模型调用 act 给出一组操作;程序执行后,把执行结果和新的界面
作为这次工具调用的结果交回,模型接着判断。回合结束后对话丢弃,本局档案(ledger.py)里只留一行摘要。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, model_validator

from ..config import ModelEndpoint
from ..knowledge.index import NameIndex, format_entities
from ..llm import LLMError, LLMResponse, Message, ToolSpec, complete
from ..memory import Cues, Memory, format_for_prompt, recall
from ..vision.ocr import TextBox

PROMPTS = Path(__file__).parent / "prompts"
MAX_ACTIONS = 12
ZONES = ("front", "back", "bench")
ZONE_NAMES = {"front": "前台", "back": "后台", "bench": "备战席"}
PREPARE_ONLY = {"buy", "sell", "move", "refresh", "buy_exp", "ready", "compose", "equip"}


class Action(BaseModel):
    type: str = Field(
        pattern="^(click_text|click|drag|scroll|key|wait|pause|buy|sell|move|refresh|buy_exp|ready|compose|equip)$")
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
    reason: str = ""

    @model_validator(mode="after")
    def _required(self) -> "Action":
        need = {"click_text": ["text"], "click": ["x", "y"], "drag": ["x", "y", "x2", "y2"], "key": ["key"],
                "buy": ["slot"], "sell": ["zone", "index"], "move": ["zone", "index", "to_zone", "to_index"],
                "compose": ["item", "onto"], "equip": ["item", "zone", "index"]}
        missing = [f for f in need.get(self.type, []) if getattr(self, f) in (None, "")]
        if missing:
            raise ValueError(f"{self.type} 操作缺少 {', '.join(missing)}")
        return self


class Note(BaseModel):
    kind: str = "教训"
    title: str
    content: str
    entities: list[str] = []


class ActInput(BaseModel):
    analysis: str
    actions: list[Action] = Field(min_length=1, max_length=MAX_ACTIONS)
    run_notes: str | None = None
    notes: list[Note] = []


_SLOT = {"type": "string", "enum": list(ZONES)}
ACT_TOOL = ToolSpec(
    name="act",
    description="提交对当前界面的判断和要按顺序执行的操作。",
    parameters={
        "type": "object",
        "properties": {
            "analysis": {"type": "string", "description": "对当前界面和局势的判断,以及这样操作的理由"},
            "actions": {
                "type": "array", "minItems": 1, "maxItems": MAX_ACTIONS,
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["buy", "sell", "move", "refresh", "buy_exp", "ready",
                                                            "compose", "equip", "click_text", "click", "drag",
                                                            "scroll", "key", "wait", "pause"]},
                        "slot": {"type": "integer"}, "zone": _SLOT, "index": {"type": "integer"},
                        "to_zone": _SLOT, "to_index": {"type": "integer"}, "times": {"type": "integer"},
                        "item": {"type": "integer"}, "onto": {"type": "integer"},
                        "text": {"type": "string"}, "nth": {"type": "integer"},
                        "x": {"type": "number"}, "y": {"type": "number"},
                        "x2": {"type": "number"}, "y2": {"type": "number"},
                        "key": {"type": "string"}, "seconds": {"type": "number"}, "notches": {"type": "integer"},
                        "reason": {"type": "string"},
                    },
                    "required": ["type"],
                },
            },
            "run_notes": {"type": "string", "description": "整体改写对局方向与计划(可选)"},
            "notes": {
                "type": "array",
                "items": {"type": "object", "properties": {
                    "kind": {"type": "string", "enum": ["机制", "打法", "教训"]}, "title": {"type": "string"},
                    "content": {"type": "string"}, "entities": {"type": "array", "items": {"type": "string"}}},
                    "required": ["title", "content"]},
            },
        },
        "required": ["analysis", "actions"],
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
    seen: set[str] = field(default_factory=set)        # 已经给过描述的游戏条目
    waiting: bool = False                              # 上一轮模型只让等待

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
        found = [e for e in self.index.link([b.text for b in boxes] + (names or [])) if e.name not in episode.seen]
        episode.seen.update(e.name for e in found)
        return (format_entities(found) if found else ""), {e.name for e in found}

    def opening(self, episode: Episode, boxes: list[TextBox], *, ledger: str, hints: str = "",
                names: list[str] | None = None) -> tuple[str, list[str]]:
        """回合的第一条消息:本局档案 + 当前界面 + 条目描述 + 相关经验。"""
        texts = [b.text for b in boxes]
        described, entities = self._entities(boxes, names, episode)
        cues = Cues(entities=entities, texts=texts, screen=episode.screen,
                    layer="战术" if episode.screen == "prepare" else "选择")
        hits = recall(self.memories, cues)
        parts = [
            ledger,
            f"## 当前界面\n程序判断的界面类型:{episode.screen}" + (f"\n{hints}" if hints else ""),
            "## 界面文字(坐标, 文字)\n" + "\n".join(str(b) for b in boxes),
        ]
        if described:
            parts.append("## 界面上出现的条目\n" + described)
        if hits:
            parts.append("## 相关经验\n" + format_for_prompt(hits))
        parts.append(self._ask(episode))
        return "\n\n".join(p for p in parts if p), [r.memory.id for r in hits]

    def observation(self, episode: Episode, results: list[str], boxes: list[TextBox], *, screen: str,
                    hints: str = "", names: list[str] | None = None) -> str:
        """回合内后续每轮:上一组操作的执行结果 + 现在的界面。"""
        described, _ = self._entities(boxes, names, episode)
        parts = ["## 执行结果\n" + ("\n".join(f"- {r}" for r in results) or "(没有执行任何操作)"),
                 f"## 现在的界面\n程序判断的界面类型:{screen}" + (f"\n{hints}" if hints else ""),
                 "## 界面文字(坐标, 文字)\n" + "\n".join(str(b) for b in boxes)]
        if described:
            parts.append("## 新出现的条目\n" + described)
        parts.append(self._ask(episode))
        return "\n\n".join(parts)

    @staticmethod
    def _ask(episode: Episode) -> str:
        if episode.screen == "prepare" and episode.last_turn:
            return "这是本备战阶段最后一次决策:请把要做的事一次做完,以 ready 结束。"
        return "请判断当前局面,调用 act 给出下一步操作。"

    # ---------------------------------------------------------------- 调用模型

    def decide(self, episode: Episode, text: str, recalled: list[str] | None = None) -> Decision:
        """把 text 交给模型(回合内有待回复的工具调用时作为它的结果),返回模型的决定。

        接口调用失败时这一轮作废,回合的对话恢复原样,下次重新问。
        """
        snapshot = (list(episode.messages), list(episode.pending), episode.turns)
        if episode.pending:
            episode.messages += [Message(role="tool", tool_call_id=cid, content=text if i == 0 else "没有执行(每轮只执行第一个 act)")
                                 for i, cid in enumerate(episode.pending)]
            episode.pending = []
        else:
            episode.messages.append(Message(role="user", content=text))
        episode.turns += 1
        endpoint = self.endpoint(episode.role)
        base = dict(prompt=text, recalled=recalled or [], episode=episode.id, turn=episode.turns, role=episode.role)
        resp: LLMResponse | None = None
        last_error = ""
        for _ in range(2):  # 输出不合法时带着错误重问一次
            try:
                resp = complete(endpoint, self.system, episode.messages, [ACT_TOOL])
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
                    return Decision(data.analysis, data.actions, data.run_notes, data.notes, response=resp, **base)
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
        case "buy_exp":
            return f"买经验 ×{a.times or 1}"
        case "ready":
            return "出战"
        case "compose":
            return f"合成装备 {a.item}号 + {a.onto}号"
        case "equip":
            return f"装备 {a.item}号 → {slot_text(a.zone, a.index)}"
        case "click_text":
            return f"点击「{a.text}」" + (f"(第{a.nth + 1}个)" if a.nth else "")
        case "click":
            return f"点击 ({_f(a.x)},{_f(a.y)})"
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
    return {"analysis": d.analysis, "actions": [a.model_dump(exclude_none=True) for a in d.actions],
            "run_notes": d.run_notes, "notes": [n.model_dump() for n in d.notes], "recalled": d.recalled,
            "error": d.error, "reasoning": r.reasoning if r else "", "usage": r.usage.model_dump() if r else None,
            "latency_s": r.latency_s if r else None, "model": r.model if r else "",
            "episode": d.episode, "turn": d.turn, "role": d.role, "prompt": d.prompt}
