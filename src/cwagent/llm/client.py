"""统一调用入口:complete() 和连通性测试 check_profile()。"""

from __future__ import annotations

import json
import random
import secrets
import time
from collections.abc import Callable, Iterator
from email.utils import parsedate_to_datetime
from typing import Any
from uuid import uuid4

import httpx

from ..config import ModelEndpoint
from . import anthropic_format, json_mode, openai_format
from .base import ImageInput, LLMError, LLMResponse, Message, ToolSpec
from .external import BRIDGE
from .opencode import request_headers

_ADAPTERS = {"openai": openai_format, "anthropic": anthropic_format}
MAX_ERROR_BODY = 2000
# 流式请求被拒(网关不认 stream / stream_options)的接口,本进程里之后都用非流式
_NO_STREAM: set[tuple[str, str]] = set()

Delta = Callable[[str, str], None]   # (种类, 文字):reasoning / text / tool(工具参数片段);reset 表示重新开始


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
            out.append(m.model_copy(update={"role": "user", "content": f"工具执行结果:\n{m.content}"}))
        elif m.role == "assistant":
            out.append(Message(role="assistant", content=m.content))
        else:
            out.append(m)
    first = next(i for i, m in enumerate(out) if m.role == "user")
    out[first] = out[first].model_copy(update={"content": out[first].content + "\n" + json_mode.instructions(tools)})
    return out


def _sse(lines: Iterator[str]) -> Iterator[dict[str, Any]]:
    """Server-Sent Events 的 data 行 → JSON。一行一个 JSON 是常态;跨行的按空行分隔拼起来。"""
    buf: list[str] = []
    for line in lines:
        if line.startswith("data:"):
            data = line[5:].strip()
            if data == "[DONE]":
                continue
            if not buf:
                try:
                    yield json.loads(data)
                    continue
                except ValueError:
                    pass
            buf.append(data)
        elif not line.strip() and buf:
            yield json.loads("\n".join(buf))
            buf = []
    if buf:
        yield json.loads("\n".join(buf))


def _stream(client: httpx.Client, url: str, headers: dict[str, str], body: dict[str, Any], adapter,
            on_delta: Delta) -> tuple[httpx.Response, dict[str, Any] | None]:
    """发流式请求,边收边把增量交给 on_delta;返回拼好的完整响应(状态码不是 200 时为 None)。"""
    with client.stream("POST", url, headers=headers, json=body) as resp:
        if resp.status_code != 200:
            resp.read()
            return resp, None
        on_delta("reset", "")
        acc = adapter.Accumulator()
        try:
            for event in _sse(resp.iter_lines()):
                for kind, text in acc.feed(event):
                    on_delta(kind, text)
        except anthropic_format.StreamError as e:
            raise LLMError(f"流式响应出错:{e}", status=200, retryable=True) from None
        except ValueError as e:
            raise LLMError(f"无法解析流式响应:{e}", status=200) from None
        return resp, acc.result()


def complete(profile: ModelEndpoint, system: str, messages: str | list[Message],
             tools: list[ToolSpec] | None = None, *, http: httpx.Client | None = None, max_retries: int = 4,
             sleep: Callable[[float], None] = time.sleep, on_delta: Delta | None = None) -> LLMResponse:
    """调用一次模型。messages 是一段 user 文字或多轮消息列表。

    给了 on_delta 就用流式请求,边收边回调思考、正文和工具参数的片段(网页上实时显示);
    拼出来的响应和非流式完全一样,解析、多轮传回都不变。网关不认流式参数时自动改用非流式。
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
    headers = request_headers(profile.base_url, headers, profile.session_id, enabled=profile.opencode_go)
    key = (url, profile.model)
    stream = on_delta is not None and key not in _NO_STREAM
    fell_back = False

    own = http is None
    client = http or httpx.Client(timeout=httpx.Timeout(profile.timeout_seconds, connect=15.0),
                                  trust_env=True)
    started = time.monotonic()
    try:
        attempt = 0
        while True:
            attempt += 1
            delay: float | None = None
            streamed: dict[str, Any] | None = None
            try:
                if stream:
                    resp, streamed = _stream(client, url, headers, adapter.stream_body(body), adapter, on_delta)
                else:
                    resp = client.post(url, headers=headers, json=body)
            except httpx.HTTPError as e:
                error = LLMError(f"请求失败:{type(e).__name__}: {e}", retryable=True)
            except LLMError as e:
                if not e.retryable:
                    raise
                error = e
            else:
                if stream and resp.status_code in (400, 422):
                    # 可能是网关不认 stream / stream_options:马上用非流式重发,不算一次重试;
                    # 非流式成功了才记下这个接口以后不用流式(400 也可能是别的原因)
                    stream, fell_back, attempt = False, True, attempt - 1
                    continue
                if resp.status_code == 200:
                    try:
                        data: dict[str, Any] = streamed if stream else resp.json()
                        result = adapter.parse_response(data)
                    except ValueError as e:
                        raise LLMError(f"无法解析响应:{e}", status=200,
                                       body=json.dumps(data, ensure_ascii=False)[:MAX_ERROR_BODY] if stream
                                       else resp.text[:MAX_ERROR_BODY]) from None
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

    if fell_back:
        _NO_STREAM.add(key)
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
                       "digits": {"type": "string", "description": "图片中的六位数字"},
                       "note": {"type": "string", "description": "一句话说明你是谁"}},
        "required": ["status", "digits"],
    },
)


def check_profile(profile: ModelEndpoint, *, http: httpx.Client | None = None) -> dict[str, Any]:
    """检查文字、工具调用和实际读图能力。"""
    from PIL import Image, ImageDraw, ImageFont

    profile = profile.model_copy(update={"session_id": str(uuid4())})
    digits = "".join(secrets.choice("0123456789") for _ in range(6))
    image = Image.new("RGB", (420, 100), "white")
    draw = ImageDraw.Draw(image)
    draw.text((25, 16), digits, fill="black", font=ImageFont.load_default(size=64))
    message = Message(role="user", content="读取图片中的六位数字,调用 report_status,status 填 ok,digits 原样填数字。",
                      images=[ImageInput.from_pil(image)])
    try:
        resp = complete(profile, "你在接受接口连通性测试。",
                        [message],
                        [_TEST_TOOL], http=http, max_retries=1)
    except LLMError as e:
        return {"ok": False, "error": str(e), "status": e.status, "body": e.body[:500]}
    call = next((c for c in resp.tool_calls if c.name == _TEST_TOOL.name), None)
    vision = bool(call and str(call.arguments.get("digits", "")).strip() == digits)
    return {
        "ok": bool(vision and call and call.parse_error is None and call.arguments.get("status") == "ok"),
        "vision": vision,
        "error": "" if vision else "模型没有正确读出测试图片中的数字",
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
