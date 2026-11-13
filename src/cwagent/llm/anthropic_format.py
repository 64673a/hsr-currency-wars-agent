"""Anthropic Messages 格式。

注意事项(截至 2026-11 的 Claude API 行为):
- 不发送 temperature / top_p / top_k,新版 API 会拒绝;
- 新一代 Claude 模型不接受强制工具调用(tool_choice 为 any 或指定工具会 400),因此不发送 tool_choice,
  靠提示词要求调用工具;
- 思考内容默认不返回,需要在档案的 extra_body 里写 thinking: {type: adaptive, display: summarized};
  较老的模型用 thinking: {type: enabled, budget_tokens: N}。这些由档案决定,代码不判断;
- stop_reason 为 refusal 时视为失败调用;不启用服务端回退到其他模型的功能,以免破坏模型对比。
"""

from __future__ import annotations

from typing import Any

from ..config import ModelEndpoint
from .base import LLMResponse, ToolCall, ToolSpec, Usage, deep_merge

DEFAULT_MAX_TOKENS = 16000


def endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base + ("/messages" if base.endswith("/v1") else "/v1/messages")


def build_request(profile: ModelEndpoint, system: str, user: str,
                  tools: list[ToolSpec] | None) -> tuple[str, dict[str, str], dict[str, Any]]:
    headers = {"Content-Type": "application/json", "anthropic-version": profile.anthropic_version}
    if profile.api_key:
        headers["x-api-key"] = profile.api_key
    headers.update(profile.extra_headers)
    body: dict[str, Any] = {
        "model": profile.model,
        "max_tokens": profile.max_tokens or DEFAULT_MAX_TOKENS,
        # 系统提示词稳定不变,打上缓存标记
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
    }
    if tools:
        body["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters}
                         for t in tools]
    return endpoint(profile.base_url), headers, deep_merge(body, profile.extra_body)


def parse_response(data: dict[str, Any]) -> LLMResponse:
    if data.get("type") == "error":
        raise ValueError(str(data.get("error")))
    texts, thoughts, calls = [], [], []
    for block in data.get("content") or []:
        kind = block.get("type")
        if kind == "text":
            texts.append(block.get("text", ""))
        elif kind == "thinking":
            if block.get("thinking"):
                thoughts.append(block["thinking"])
        elif kind == "tool_use":
            args = block.get("input")
            calls.append(ToolCall(
                id=block.get("id", ""), name=block.get("name", ""),
                arguments=args if isinstance(args, dict) else {},
                parse_error=None if isinstance(args, dict) else "参数不是 JSON 对象",
            ))
    usage = data.get("usage") or {}
    return LLMResponse(
        text="\n".join(texts).strip(),
        reasoning="\n\n".join(thoughts).strip(),
        tool_calls=calls,
        usage=Usage(input_tokens=usage.get("input_tokens") or 0,
                    output_tokens=usage.get("output_tokens") or 0,
                    cache_read_tokens=usage.get("cache_read_input_tokens") or 0,
                    cache_write_tokens=usage.get("cache_creation_input_tokens") or 0),
        stop_reason=data.get("stop_reason") or "",
        model=data.get("model", ""),
    )
