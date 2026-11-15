"""统一调用入口:complete() 和连通性测试 check_profile()。"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from ..config import ModelEndpoint
from . import anthropic_format, json_mode, openai_format
from .base import LLMError, LLMResponse, Message, ToolSpec
from .external import BRIDGE

_ADAPTERS = {"openai": openai_format, "anthropic": anthropic_format}
MAX_ERROR_BODY = 2000


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError):
            return None


def _json_mode_messages(messages: list[Message], tools: list[ToolSpec]) -> list[Message]:
    """不支持原生工具调用时:工具结果改成 user 消息,助手消息只保留文字;第一条 user 消息后附上输出格式说明。"""
    out: list[Message] = []
    for m in messages:
        if m.role == "tool":
            out.append(Message(role="user", content=f"工具执行结果:\n{m.content}"))
        elif m.role == "assistant":
            out.append(Message(role="assistant", content=m.content))
        else:
            out.append(m)
    first = next(i for i, m in enumerate(out) if m.role == "user")
    out[first] = Message(role="user", content=out[first].content + "\n" + json_mode.instructions(tools))
    return out


def complete(profile: ModelEndpoint, system: str, messages: str | list[Message],
             tools: list[ToolSpec] | None = None, *, http: httpx.Client | None = None, max_retries: int = 4,
             sleep: Callable[[float], None] = time.sleep) -> LLMResponse:
    """调用一次模型。messages 是一段 user 文字或多轮消息列表。

    429、5xx 和网络错误按指数退避重试;其他 4xx 直接抛出 LLMError。
    """
    tools = tools or []
    if isinstance(messages, str):
        messages = [Message(role="user", content=messages)]
    if profile.format == "external":
        return BRIDGE.complete(system, messages, tools, model=profile.model)
    adapter = _ADAPTERS[profile.format]
    native = profile.tool_mode == "native"
    if tools and not native:
        messages = _json_mode_messages(messages, tools)
    url, headers, body = adapter.build_request(profile, system, messages, tools if native else None)

    own = http is None
    client = http or httpx.Client(timeout=httpx.Timeout(profile.timeout_seconds, connect=15.0),
                                  trust_env=True)
    started = time.monotonic()
    try:
        attempt = 0
        while True:
            attempt += 1
            delay: float | None = None
            try:
                resp = client.post(url, headers=headers, json=body)
            except httpx.HTTPError as e:
                error = LLMError(f"请求失败:{type(e).__name__}: {e}", retryable=True)
            else:
                if resp.status_code == 200:
                    try:
                        data: dict[str, Any] = resp.json()
                        result = adapter.parse_response(data)
                    except ValueError as e:
                        raise LLMError(f"无法解析响应:{e}", status=200,
                                       body=resp.text[:MAX_ERROR_BODY]) from None
                    break
                retryable = resp.status_code == 429 or resp.status_code >= 500
                error = LLMError(_error_message(resp), status=resp.status_code,
                                 body=resp.text[:MAX_ERROR_BODY], retryable=retryable)
                delay = _retry_after(resp)
            if not error.retryable or attempt > max_retries:
                raise error
            if delay is None:
                delay = min(60.0, 2.0 ** (attempt - 1)) + random.uniform(0, 0.5)
            sleep(min(delay, 120.0))
    finally:
        if own:
            client.close()

    if tools and not native:
        result.tool_calls = json_mode.parse(result.text, tools)
    if result.stop_reason == "refusal":
        raise LLMError("模型拒绝回答(stop_reason=refusal)", status=200,
                       body=str(data)[:MAX_ERROR_BODY])
    result.latency_s = round(time.monotonic() - started, 3)
    result.attempts = attempt
    result.raw = data
    return result


def _error_message(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:300] or resp.reason_phrase
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err)
    if err:
        return str(err)
    return str(data)[:300]


# ---------------------------------------------------------------- 连通性测试

_TEST_TOOL = ToolSpec(
    name="report_status",
    description="报告连通性测试结果。",
    parameters={
        "type": "object",
        "properties": {"status": {"type": "string", "description": "固定填 ok"},
                       "note": {"type": "string", "description": "一句话说明你是谁"}},
        "required": ["status"],
    },
)


def check_profile(profile: ModelEndpoint, *, http: httpx.Client | None = None) -> dict[str, Any]:
    """用最小请求检查档案是否可用:能否调用成功、是否返回工具调用和思考内容。"""
    try:
        resp = complete(profile, "你在接受接口连通性测试。",
                        "请调用 report_status 工具,status 填 ok,note 用一句话说明你是哪个模型。",
                        [_TEST_TOOL], http=http, max_retries=1)
    except LLMError as e:
        return {"ok": False, "error": str(e), "status": e.status, "body": e.body[:500]}
    call = next((c for c in resp.tool_calls if c.name == _TEST_TOOL.name), None)
    return {
        "ok": True,
        "tool_call": call is not None and call.parse_error is None,
        "tool_arguments": call.arguments if call else None,
        "reasoning": bool(resp.reasoning),
        "reasoning_preview": resp.reasoning[:300],
        "text_preview": resp.text[:300],
        "model": resp.model,
        "stop_reason": resp.stop_reason,
        "latency_s": resp.latency_s,
        "usage": resp.usage.model_dump(),
    }
