"""LLM 接入层的公共类型。设计见 docs/TECH_DESIGN.md 第 11 节。"""

from __future__ import annotations

from typing import Any, Literal
import base64
from io import BytesIO

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


class ImageInput(BaseModel):
    """发给模型的图片,内容与对局记录保存的 JPEG 一致。"""

    data: str
    media_type: Literal["image/jpeg", "image/png"] = "image/jpeg"
    width: int = 0
    height: int = 0
    label: str = ""

    @classmethod
    def from_pil(cls, image, *, label: str = "", max_size: tuple[int, int] | None = (1280, 720), quality: int = 70):
        """quality 只影响文件大小,不影响 token(按像素算)。外部 agent 的对话里截图一直累积,Claude Code 在请求体
        超过 32MB 前按先后删最旧的图,之后每轮前缀都变、缓存每轮重写(anthropics/claude-code#89418)。
        2026-11-19 那局质量 88 时一张约 180KB,累计到约 127 张就开始删;质量 70 约 107KB,能放约 214 张,
        小字和标号看不出差别。"""
        img = image.convert("RGB").copy()
        if max_size:
            img.thumbnail(max_size)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return cls(data=base64.b64encode(buf.getvalue()).decode("ascii"), width=img.width, height=img.height, label=label)

    def block(self) -> dict[str, Any]:
        return {"type": "image", "source": {"type": "base64", "media_type": self.media_type, "data": self.data}}


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
    images: list[ImageInput] = Field(default_factory=list)
    snapshot: str = ""       # 外部 agent 接管或压缩后恢复用,不发给 API 适配器
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str = ""
    raw: Any = None
    cache_at: int = 0      # user 消息:在这个字符位置断开,前一段打缓存标记(Anthropic;本局经历只增不改,跨回合命中)


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
