"""外部 agent:把模型调用交给通过 MCP 连上来的 agent 工具(DSH、Claude Code、Codex 等)。设计见 docs/TECH_DESIGN.md 11.9。

对局线程照常调用 complete();模型档案是外部 agent 时,complete() 把这一轮的新内容放进信箱,等 agent 用 act 回答。
MCP 那边用 take() 取局面、answer() 交回答(server/mcp.py)。同一时间只跑一局,所以整个进程只有一个信箱 BRIDGE。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from .base import LLMError, LLMResponse, Message, ToolCall, ToolSpec

EXTERNAL = "外部 agent"     # 网页上选的模型名,也是对局记录里的档案名
IDLE_SECONDS = 15 * 60      # 局面交出去后这么久没有任何 MCP 调用,就让对局暂停


@dataclass
class Turn:
    """一个等外部 agent 回答的局面。"""

    seq: int
    text: str
    opening: bool   # 回合的第一条消息(带本局档案)


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
        self._cond = threading.Condition()
        self._seq = 0
        self._turn: Turn | None = None
        self._reply: dict[str, Any] | None = None
        self._stop: threading.Event | None = None
        self._active = False
        self._ended: str | None = None

    # ------------------------------------------------------------ 对局一侧

    def begin(self, stop: threading.Event) -> None:
        with self._cond:
            self._stop, self._active, self._ended = stop, True, None
            self._turn = self._reply = None
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
            turn = self._turn = Turn(self._seq, text, opening)
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

    def answer(self, arguments: dict[str, Any]) -> bool:
        """交回当前局面的回答;没有等待回答的局面时返回 False。"""
        with self._cond:
            self.last_seen = time.time()
            if self._turn is None or self._reply is not None:
                return False
            self._reply = arguments
            self._cond.notify_all()
            return True

    def status(self) -> dict[str, Any]:
        with self._cond:
            return {"active": self._active, "waiting": self._turn is not None and self._reply is None,
                    "turn": self._seq, "ended": self._ended,
                    "idle_s": round(time.time() - self.last_seen) if self.last_seen else None}


BRIDGE = ExternalBridge()
