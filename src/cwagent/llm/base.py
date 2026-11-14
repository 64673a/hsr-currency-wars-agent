"""LLM 接入层的公共类型。设计见 docs/TECH_DESIGN.md 第 11 节。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolSpec(BaseModel):
    """提供给模型的一个工具:名字、说明和参数的 JSON Schema。"""

    name: str
    description: str
    parameters: dict[str, Any]


class ToolCall(BaseModel):
    id: str = ""
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    raw_arguments: str | None = None   # 模型给出的原始参数文本(OpenAI 格式、JSON 模式)
    parse_error: str | None = None      # 参数不是合法 JSON 对象时的错误说明


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0   # 输出中用于思考的部分(接口提供时才有)


class Message(BaseModel):
    """多轮对话里的一条消息。

    - user:content 是文字;
    - assistant:模型的回复。raw 保存接口返回的原始内容(OpenAI 格式的 message 对象、Anthropic 格式的内容块),
      下一轮原样传回:DeepSeek 等思考模型在带工具的多轮对话里要求传回 reasoning_content,Anthropic 要求传回
      thinking 块;
    - tool:某次工具调用的执行结果,tool_call_id 对应 assistant 消息里的 ToolCall.id。
    """

    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str = ""
    raw: Any = None


class LLMResponse(BaseModel):
    text: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    stop_reason: str = ""
    model: str = ""
    latency_s: float = 0.0
    attempts: int = 1
    raw: dict[str, Any] = Field(default_factory=dict)
    message_raw: Any = None   # 回复消息本身的原始内容,见 Message.raw

    def as_message(self) -> Message:
        return Message(role="assistant", content=self.text, tool_calls=self.tool_calls, raw=self.message_raw)


class LLMError(Exception):
    """一次调用最终失败。status 为 HTTP 状态码,网络错误时为 0。"""

    def __init__(self, message: str, *, status: int = 0, body: str = "", retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.body = body
        self.retryable = retryable

    def __str__(self) -> str:
        prefix = f"[HTTP {self.status}] " if self.status else ""
        return prefix + super().__str__()


def deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """把 extra 递归合并进 base 的副本;同名的非字典值以 extra 为准。"""
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out
