"""OpenAI Chat Completions 格式(OpenAI 以及各类兼容网关)。"""

from __future__ import annotations

import json
import re
from typing import Any

from ..config import ModelEndpoint
from .base import LLMResponse, Message, ToolCall, ToolSpec, Usage, deep_merge

_THINK = re.compile(r"^\s*<think>(.*?)</think>\s*", re.S)


def _message(m: Message) -> dict[str, Any]:
    if m.role == "user":
        content = ([{"type": "text", "text": m.content}] if m.content else []) + [
            {"type": "image_url", "image_url": {"url": f"data:{i.media_type};base64,{i.data}"}} for i in m.images]
        return {"role": "user", "content": content if m.images else m.content}
    if m.role == "tool":
        return {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content}
    if isinstance(m.raw, dict):  # 原样传回,含 reasoning_content(DeepSeek 思考模式带工具时必须传回)
        return m.raw
    out: dict[str, Any] = {"role": "assistant", "content": m.content}
    if m.tool_calls:
        out["tool_calls"] = [{"id": c.id, "type": "function",
                              "function": {"name": c.name,
                                           "arguments": c.raw_arguments or json.dumps(c.arguments, ensure_ascii=False)}}
                             for c in m.tool_calls]
    return out


def build_request(profile: ModelEndpoint, system: str, messages: list[Message],
                  tools: list[ToolSpec] | None) -> tuple[str, dict[str, str], dict[str, Any]]:
    url = profile.base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if profile.api_key:
        headers["Authorization"] = f"Bearer {profile.api_key}"
    headers.update(profile.extra_headers)
    converted = []
    pending_images = []
    for m in messages:
        if m.role != "tool" and pending_images:
            converted.append(_message(Message(role="user", content="操作后的截图", images=pending_images)))
            pending_images = []
        converted.append(_message(m))
        if m.role == "tool" and m.images:
            pending_images += m.images
    if pending_images:
        converted.append(_message(Message(role="user", content="操作后的截图", images=pending_images)))
    body: dict[str, Any] = {
        "model": profile.model,
        "messages": [{"role": "system", "content": system}, *converted],
    }
    if profile.max_tokens:
        body["max_tokens"] = profile.max_tokens
    if tools:
        body["tools"] = [
            {"type": "function",
             "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in tools
        ]
        body["tool_choice"] = "auto"
    body = deep_merge(body, profile.extra_body)
    effort = profile.reasoning_effort
    if effort:
        body["reasoning_effort"] = effort
        # DeepSeek 的 thinking 开关优先于 reasoning_effort:档案里写了开关就跟着改(OpenAI 不认 thinking,没写不加)
        thinking = body.get("thinking")
        if isinstance(thinking, dict) and effort == "none":
            body["thinking"] = {**thinking, "type": "disabled"}
        elif isinstance(thinking, dict) and thinking.get("type") == "disabled":
            del body["thinking"]
    return url, headers, body


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # 部分网关返回内容块数组
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def parse_response(data: dict[str, Any]) -> LLMResponse:
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("响应中没有 choices")
    choice = choices[0]
    message = choice.get("message") or {}
    text = _text(message.get("content"))

    reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
    if not isinstance(reasoning, str):
        reasoning = json.dumps(reasoning, ensure_ascii=False)
    m = _THINK.match(text)
    if m:  # 部分开源模型把思考写在 <think> 标签里
        reasoning = reasoning or m.group(1).strip()
        text = text[m.end():]

    calls = []
    for i, tc in enumerate(message.get("tool_calls") or []):
        fn = tc.get("function") or {}
        raw = fn.get("arguments")
        args: dict[str, Any] = {}
        error = None
        if isinstance(raw, dict):
            args, raw = raw, json.dumps(raw, ensure_ascii=False)
        else:
            try:
                value = json.loads(raw or "{}")
                if isinstance(value, dict):
                    args = value
                else:
                    error = "参数不是 JSON 对象"
            except ValueError as e:
                error = f"参数不是合法 JSON:{e}"
        calls.append(ToolCall(id=tc.get("id") or f"call-{i}", name=fn.get("name", ""),
                              arguments=args, raw_arguments=raw, parse_error=error))

    usage = data.get("usage") or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    return LLMResponse(
        text=text.strip(),
        reasoning=reasoning.strip(),
        tool_calls=calls,
        usage=Usage(input_tokens=usage.get("prompt_tokens") or 0,
                    output_tokens=usage.get("completion_tokens") or 0,
                    # DeepSeek 用 prompt_cache_hit_tokens 表示缓存命中
                    cache_read_tokens=(prompt_details.get("cached_tokens")
                                       or usage.get("prompt_cache_hit_tokens") or 0),
                    reasoning_tokens=completion_details.get("reasoning_tokens") or 0),
        stop_reason=choice.get("finish_reason") or "",
        model=data.get("model", ""),
        message_raw={k: v for k, v in message.items() if v is not None},
    )


# ---------------------------------------------------------------- 流式

def stream_body(body: dict[str, Any]) -> dict[str, Any]:
    """流式请求:最后一块带用量(没有这个选项时流里不给用量)。"""
    return {**body, "stream": True, "stream_options": {"include_usage": True}}


class Accumulator:
    """把流式的 chat.completion.chunk 拼回和非流式一样的响应,交给 parse_response;feed 返回这一块的增量。"""

    def __init__(self) -> None:
        self.model = ""
        self.content = ""
        self.reasoning = ""
        self.reasoning_key = "reasoning_content"
        self.tools: dict[int, dict[str, Any]] = {}
        self.finish = ""
        self.usage: dict[str, Any] = {}

    def feed(self, chunk: dict[str, Any]) -> list[tuple[str, str]]:
        if chunk.get("error"):   # 有的网关把错误放在流里
            raise ValueError(str(chunk["error"]))
        out: list[tuple[str, str]] = []
        self.model = chunk.get("model") or self.model
        if chunk.get("usage"):
            self.usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            if choice.get("index", 0) != 0:
                continue
            delta = choice.get("delta") or {}
            for key in ("reasoning_content", "reasoning"):   # DeepSeek 用前者,部分网关用后者
                value = delta.get(key)
                if isinstance(value, str) and value:
                    self.reasoning_key = key
                    self.reasoning += value
                    out.append(("reasoning", value))
                    break
            value = delta.get("content")
            if isinstance(value, str) and value:
                self.content += value
                out.append(("text", value))
            for tc in delta.get("tool_calls") or []:
                cur = self.tools.setdefault(tc.get("index", 0),
                                            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                fn = tc.get("function") or {}
                cur["id"] = cur["id"] or tc.get("id") or ""
                cur["function"]["name"] = cur["function"]["name"] or fn.get("name") or ""
                if fn.get("arguments"):
                    cur["function"]["arguments"] += fn["arguments"]
                    out.append(("tool", fn["arguments"]))
            if choice.get("finish_reason"):
                self.finish = choice["finish_reason"]
        return out

    def result(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": self.content or None}
        if self.reasoning:   # 原样带着,下一轮传回(DeepSeek 思考模式带工具时必须传回 reasoning_content)
            message[self.reasoning_key] = self.reasoning
        if self.tools:
            message["tool_calls"] = [self.tools[i] for i in sorted(self.tools)]
        return {"model": self.model, "choices": [{"index": 0, "message": message, "finish_reason": self.finish}],
                "usage": self.usage}
