"""外部 agent:把模型调用交给通过 MCP 连上来的 agent 工具(DSH、Claude Code、Codex 等)。设计见 docs/TECH_DESIGN.md 11.9。

对局线程照常调用 complete();模型档案是外部 agent 时,complete() 把这一轮的新内容放进信箱,等 agent 用 act 回答。
MCP 那边用 take() 取局面、answer() 交回答(server/mcp.py)。同一时间只跑一局,所以整个进程只有一个信箱 BRIDGE。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .base import ImageInput, LLMError, LLMResponse, Message, ToolCall, ToolSpec

EXTERNAL = "外部 agent"     # 网页上选的模型名,也是对局记录里的档案名
IDLE_SECONDS = 15 * 60      # 局面交出去后这么久没有任何 MCP 调用,就让对局暂停


@dataclass
class Turn:
    """一个等外部 agent 回答的局面。"""

    seq: int
    text: str
    opening: bool   # 回合的第一条消息(带本局档案)
    images: list[ImageInput] = field(default_factory=list)
    snapshot: str = ""


def new_content(messages: list[Message]) -> tuple[str, bool]:
    """外部 agent 自己保留对话,只需要上一次回答之后的新内容:回合开场,或执行结果 + 新界面,或重问。"""
    tail: list[Message] = []
    for m in reversed(messages):
        if m.role == "assistant":
            break
        tail.append(m)
    tail.reverse()
    return "\n\n".join(m.content for m in tail if m.content), len(tail) == len(messages)


class ExternalBridge:
    def __init__(self, idle_seconds: float = IDLE_SECONDS):
        self.idle_seconds = idle_seconds
        self.system = ""            # 最近一次的系统提示词(rules 工具返回它)
        self.last_seen = 0.0        # 最近一次 MCP 调用的时间
        self.agent_session = ""     # 正在下这一局的 agent 会话(Claude Code 插件的 hook 报上来的),新对局清空
        self._cond = threading.Condition()
        self._seq = 0
        self._turn: Turn | None = None
        self._reply: dict[str, Any] | None = None
        self._stop: threading.Event | None = None
        self._active = False
        self._ended: str | None = None
        self._snapshot = ""
        self._images: list[ImageInput] = []

    # ------------------------------------------------------------ 对局一侧

    def begin(self, stop: threading.Event) -> None:
        with self._cond:
            self._stop, self._active, self._ended = stop, True, None
            self.agent_session = ""
            self._turn = self._reply = None
            self._snapshot, self._images = "", []
            self.last_seen = time.time()
            self._cond.notify_all()

    def end(self, result: str) -> None:
        with self._cond:
            self._active, self._ended = False, result
            self._turn = self._reply = None
            self._cond.notify_all()

    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec], *, model: str = "") -> LLMResponse:
        text, opening = new_content(messages)
        started = time.monotonic()
        with self._cond:
            self.system = system
            self._seq += 1
            tail = []
            for message in reversed(messages):
                if message.role == "assistant":
                    break
                tail.append(message)
            images = [image for message in reversed(tail) for image in message.images] or self._images
            snapshot = next((m.snapshot for m in reversed(messages) if m.snapshot), "")
            if snapshot:
                self._snapshot = snapshot
            if images:
                self._images = images
            turn = self._turn = Turn(self._seq, text, opening, images, self._snapshot)
            self._reply = None
            self.last_seen = time.time()   # 从交出局面开始算 agent 多久没动静
            self._cond.notify_all()
            try:
                while self._reply is None:
                    if self._stop is not None and self._stop.is_set():
                        raise LLMError("对局已停止")
                    if time.time() - self.last_seen > self.idle_seconds:
                        minutes = round(self.idle_seconds / 60)
                        reply: dict[str, Any] = {
                            "analysis": f"外部 agent 超过 {minutes} 分钟没有响应",
                            "actions": [{"type": "pause", "reason": f"外部 agent 超过 {minutes} 分钟没有响应"}]}
                        break
                    self._cond.wait(1.0)
                else:
                    reply = self._reply
            finally:
                if self._turn is turn:
                    self._turn = self._reply = None
        call = ToolCall(id=f"ext-{turn.seq}", name=tools[0].name if tools else "act", arguments=reply)
        return LLMResponse(tool_calls=[call], stop_reason="tool_use", model=model or EXTERNAL,
                           latency_s=round(time.monotonic() - started, 3))

    # ------------------------------------------------------------ MCP 一侧

    def take(self, timeout: float) -> Turn | None:
        """等一个待回答的局面。超时或没有进行中的对局时返回 None;同一个局面没回答前可以重复取。"""
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                self.last_seen = time.time()
                if self._turn is not None and self._reply is None:
                    return self._turn
                left = deadline - time.monotonic()
                if not self._active or left <= 0:
                    return None
                self._cond.wait(min(left, 1.0))

    def answer(self, arguments: dict[str, Any], *, turn: int | None = None) -> bool:
        """交回当前局面的回答;没有等待回答的局面时返回 False。"""
        with self._cond:
            self.last_seen = time.time()
            if self._turn is None or self._reply is not None:
                return False
            if turn is not None and turn != self._turn.seq:
                return False
            self._reply = arguments
            self._cond.notify_all()
            return True

    def snapshot(self) -> Turn:
        with self._cond:
            return Turn(self._seq, self._snapshot or "还没有观察到局面", True, list(self._images), self._snapshot)

    def claim(self, session: str) -> None:
        """记下最近调用工具的 agent 会话:对局进行中时只让它接着下(server/mcp.py 的 hook 工具)。"""
        with self._cond:
            if self._active and session:
                self.agent_session = session

    def status(self) -> dict[str, Any]:
        with self._cond:
            return {"active": self._active, "waiting": self._turn is not None and self._reply is None,
                    "turn": self._seq, "ended": self._ended,
                    "idle_s": round(time.time() - self.last_seen) if self.last_seen else None}


BRIDGE = ExternalBridge()
