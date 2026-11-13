"""通用界面决策:把界面文字、条目描述、记忆和本局笔记交给模型,返回一组操作。

第一版对所有需要决策的界面(三选一、事件、备战)都用它;备战阶段额外附上解析出的结构化信息。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from ..config import ModelEndpoint
from ..knowledge.index import NameIndex, format_entities
from ..llm import LLMError, LLMResponse, ToolSpec, complete
from ..memory import Cues, Memory, format_for_prompt, recall
from ..vision.ocr import TextBox

PROMPTS = Path(__file__).parent / "prompts"
MAX_ACTIONS = 6


class Action(BaseModel):
    type: str = Field(pattern="^(click_text|click|drag|scroll|key|wait|pause)$")
    text: str | None = None
    nth: int = 0
    x: float | None = Field(default=None, ge=0, le=1)
    y: float | None = Field(default=None, ge=0, le=1)
    x2: float | None = Field(default=None, ge=0, le=1)
    y2: float | None = Field(default=None, ge=0, le=1)
    key: str | None = None
    seconds: float | None = Field(default=None, ge=0, le=60)
    notches: int | None = Field(default=None, ge=-30, le=30)  # scroll:正数向上,负数向下
    reason: str = ""


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
                        "type": {"type": "string", "enum": ["click_text", "click", "drag", "key", "wait", "pause"]},
                        "text": {"type": "string"}, "nth": {"type": "integer"},
                        "x": {"type": "number"}, "y": {"type": "number"},
                        "x2": {"type": "number"}, "y2": {"type": "number"},
                        "key": {"type": "string"}, "seconds": {"type": "number"},
                        "reason": {"type": "string"},
                    },
                    "required": ["type"],
                },
            },
            "run_notes": {"type": "string", "description": "整体改写本局笔记(可选)"},
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
class Decision:
    analysis: str
    actions: list[Action]
    run_notes: str | None
    notes: list[Note]
    system: str
    user: str
    response: LLMResponse | None
    recalled: list[str] = field(default_factory=list)
    error: str = ""


class ScreenAgent:
    def __init__(self, endpoint: ModelEndpoint, index: NameIndex, memories: list[Memory], rules: str):
        self.endpoint = endpoint
        self.index = index
        self.memories = memories
        self.system = (PROMPTS / "system.md").read_text(encoding="utf-8").replace("{rules}", rules)

    def build_user(self, boxes: list[TextBox], *, screen: str, run_notes: str, history: list[str],
                   hints: str = "") -> tuple[str, list[str]]:
        texts = [b.text for b in boxes]
        entities = self.index.link(texts)
        cues = Cues(entities={e.name for e in entities}, texts=texts, screen=screen,
                    layer="战术" if screen == "prepare" else "选择")
        hits = recall(self.memories, cues)
        parts = [
            "## 本局笔记\n" + (run_notes or "(空)"),
            f"## 当前界面\n程序判断的界面类型:{screen}" + (f"\n{hints}" if hints else ""),
            "## 界面文字(坐标, 文字)\n" + "\n".join(str(b) for b in boxes),
        ]
        if entities:
            parts.append("## 界面上出现的条目\n" + format_entities(entities))
        if hits:
            parts.append("## 相关经验\n" + format_for_prompt(hits))
        if history:
            parts.append("## 最近的操作(从旧到新)\n" + "\n".join(history[-10:]))
        parts.append("请判断当前局面,调用 act 给出下一步操作。")
        return "\n\n".join(parts), [r.memory.id for r in hits]

    def decide(self, boxes: list[TextBox], *, screen: str, run_notes: str, history: list[str],
               hints: str = "") -> Decision:
        user, recalled = self.build_user(boxes, screen=screen, run_notes=run_notes, history=history, hints=hints)
        prompt = user
        last_error = ""
        for _ in range(2):  # 输出不合法时带着错误重问一次
            try:
                resp = complete(self.endpoint, self.system, prompt, [ACT_TOOL])
            except LLMError as e:
                return Decision("", [], None, [], self.system, user, None, recalled, error=str(e))
            call = next((c for c in resp.tool_calls if c.name == "act"), None)
            if call is None or call.parse_error:
                last_error = call.parse_error if call else "没有调用 act 工具"
            else:
                try:
                    data = ActInput.model_validate(call.arguments)
                    return Decision(data.analysis, data.actions, data.run_notes, data.notes, self.system, user,
                                    resp, recalled)
                except ValidationError as e:
                    last_error = "; ".join(f"{'.'.join(map(str, er['loc']))}: {er['msg']}" for er in e.errors()[:5])
            prompt = user + f"\n\n上一次的输出无效:{last_error}。请重新调用 act。"
        return Decision("", [], None, [], self.system, user, resp, recalled, error=last_error)


def action_text(a: Action) -> str:
    match a.type:
        case "click_text":
            return f"点击「{a.text}」" + (f"(第{a.nth + 1}个)" if a.nth else "")
        case "click":
            return f"点击 ({a.x:.3f},{a.y:.3f})"
        case "drag":
            return f"拖拽 ({a.x:.3f},{a.y:.3f})→({a.x2:.3f},{a.y2:.3f})"
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
            "latency_s": r.latency_s if r else None, "model": r.model if r else ""}
