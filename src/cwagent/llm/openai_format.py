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
