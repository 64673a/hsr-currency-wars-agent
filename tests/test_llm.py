import json

import httpx
import pytest

from cwagent.config import ModelEndpoint
from cwagent.llm import LLMError, ToolSpec, check_profile, complete, json_mode

TOOL = ToolSpec(name="submit_actions", description="提交动作",
                parameters={"type": "object", "properties": {"actions": {"type": "array"}},
                            "required": ["actions"]})


def profile(fmt: str, **kw) -> ModelEndpoint:
    base = {"openai": "https://api.example.com/v1", "anthropic": "https://api.example.com"}[fmt]
    return ModelEndpoint(name="p", format=fmt, base_url=base, api_key="sk-test-123456", model="m", **kw)


def mock(handler):
    seen = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request, len(seen))

    return httpx.Client(transport=httpx.MockTransport(wrapped)), seen


# ---------------------------------------------------------------- OpenAI 格式

def test_openai_request_and_parse():
    reply = {
        "model": "m-2026",
        "choices": [{"finish_reason": "tool_calls", "message": {
            "content": "先买那刻夏。", "reasoning_content": "金币 23,商店有那刻夏……",
            "tool_calls": [{"id": "c1", "type": "function", "function": {
                "name": "submit_actions", "arguments": json.dumps({"actions": [{"op": "buy"}]})}}]}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "prompt_tokens_details": {"cached_tokens": 64},
                  "completion_tokens_details": {"reasoning_tokens": 12}},
    }
    http, seen = mock(lambda req, n: httpx.Response(200, json=reply))
    p = profile("openai", max_tokens=1000, extra_body={"reasoning_effort": "high"},
                extra_headers={"X-Org": "o"})
    resp = complete(p, "系统", "用户", [TOOL], http=http)

    req = seen[0]
    assert str(req.url) == "https://api.example.com/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer sk-test-123456"
    assert req.headers["x-org"] == "o"
    body = json.loads(req.content)
    assert body["messages"] == [{"role": "system", "content": "系统"}, {"role": "user", "content": "用户"}]
    assert body["tools"][0]["function"]["name"] == "submit_actions"
    assert body["tool_choice"] == "auto"
    assert body["max_tokens"] == 1000
    assert body["reasoning_effort"] == "high"

    assert resp.text == "先买那刻夏。"
    assert resp.reasoning.startswith("金币 23")
    assert resp.tool_calls[0].arguments == {"actions": [{"op": "buy"}]}
    assert resp.usage.input_tokens == 100 and resp.usage.cache_read_tokens == 64
    assert resp.usage.reasoning_tokens == 12
    assert resp.stop_reason == "tool_calls" and resp.model == "m-2026"


def test_openai_think_tag_and_bad_arguments():
    reply = {"usage": {"prompt_tokens": 9, "prompt_cache_hit_tokens": 8},
             "choices": [{"finish_reason": "tool_calls", "message": {
        "content": "<think>想一想</think>结论",
        "tool_calls": [{"id": "c1", "function": {"name": "submit_actions", "arguments": "{bad"}}]}}]}
    http, _ = mock(lambda req, n: httpx.Response(200, json=reply))
    resp = complete(profile("openai"), "s", "u", [TOOL], http=http)
    assert resp.reasoning == "想一想" and resp.text == "结论"
    assert resp.usage.cache_read_tokens == 8  # DeepSeek 的缓存字段
    assert resp.tool_calls[0].parse_error and resp.tool_calls[0].raw_arguments == "{bad"


def test_openai_without_key_and_tools():
    http, seen = mock(lambda req, n: httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]}))
    p = ModelEndpoint(name="local", format="openai", base_url="http://localhost:11434/v1", model="q")
    resp = complete(p, "s", "u", http=http)
    assert "authorization" not in seen[0].headers
    body = json.loads(seen[0].content)
    assert "tools" not in body and "max_tokens" not in body
    assert resp.text == "hi"


# ---------------------------------------------------------------- Anthropic 格式

def test_anthropic_request_and_parse():
    reply = {
        "model": "claude-x", "stop_reason": "tool_use",
        "content": [
            {"type": "thinking", "thinking": "思考摘要", "signature": "sig"},
            {"type": "text", "text": "我来买。"},
            {"type": "tool_use", "id": "tu1", "name": "submit_actions", "input": {"actions": []}},
        ],
        "usage": {"input_tokens": 50, "output_tokens": 10, "cache_read_input_tokens": 40,
                  "cache_creation_input_tokens": 5},
    }
    http, seen = mock(lambda req, n: httpx.Response(200, json=reply))
    p = profile("anthropic", extra_body={"thinking": {"type": "adaptive", "display": "summarized"},
                                         "output_config": {"effort": "medium"}})
    resp = complete(p, "系统", "用户", [TOOL], http=http)

    req = seen[0]
    assert str(req.url) == "https://api.example.com/v1/messages"
    assert req.headers["x-api-key"] == "sk-test-123456"
    assert req.headers["anthropic-version"] == "2026-10-21"
    body = json.loads(req.content)
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["max_tokens"] == 16000
    assert body["tools"][0]["input_schema"]["required"] == ["actions"]
    assert "tool_choice" not in body and "temperature" not in body
    assert body["thinking"]["display"] == "summarized"

    assert resp.reasoning == "思考摘要" and resp.text == "我来买。"
    assert resp.tool_calls[0].id == "tu1" and resp.tool_calls[0].arguments == {"actions": []}
    assert resp.usage.cache_read_tokens == 40 and resp.usage.cache_write_tokens == 5


def test_anthropic_base_url_with_v1():
    http, seen = mock(lambda req, n: httpx.Response(200, json={"content": [], "stop_reason": "end_turn"}))
    p = ModelEndpoint(name="gw", format="anthropic", base_url="https://gw.example.com/anthropic/v1",
                     model="m", anthropic_version="2023-06-01")
    complete(p, "s", "u", http=http)
    assert str(seen[0].url) == "https://gw.example.com/anthropic/v1/messages"
    assert seen[0].headers["anthropic-version"] == "2023-06-01"


def test_anthropic_refusal_is_error():
    http, _ = mock(lambda req, n: httpx.Response(200, json={"content": [], "stop_reason": "refusal"}))
    with pytest.raises(LLMError, match="refusal"):
        complete(profile("anthropic"), "s", "u", http=http)


# ---------------------------------------------------------------- 重试与错误

def test_retry_on_429_then_success():
    def handler(req, n):
        if n == 1:
            return httpx.Response(429, headers={"retry-after": "3"}, json={"error": {"message": "slow"}})
        if n == 2:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    http, seen = mock(handler)
    sleeps = []
    resp = complete(profile("openai"), "s", "u", http=http, sleep=sleeps.append)
    assert resp.text == "ok" and resp.attempts == 3 and len(seen) == 3
    assert sleeps[0] == 3.0 and 2.0 <= sleeps[1] <= 2.5


def test_client_error_not_retried():
    http, seen = mock(lambda req, n: httpx.Response(400, json={"error": {"message": "bad model"}}))
    with pytest.raises(LLMError) as info:
        complete(profile("anthropic"), "s", "u", http=http, sleep=lambda s: None)
    assert info.value.status == 400 and "bad model" in str(info.value) and len(seen) == 1


def test_retries_exhausted():
    http, seen = mock(lambda req, n: httpx.Response(500, text="boom"))
    with pytest.raises(LLMError) as info:
        complete(profile("openai"), "s", "u", http=http, max_retries=2, sleep=lambda s: None)
    assert info.value.retryable and len(seen) == 3


def test_network_error_retried():
    def handler(req, n):
        if n == 1:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    http, seen = mock(handler)
    assert complete(profile("openai"), "s", "u", http=http, sleep=lambda s: None).text == "ok"


# ---------------------------------------------------------------- JSON 模式

def test_json_mode_request_and_parse():
    content = '分析……\n```json\n{"tool": "submit_actions", "arguments": {"actions": [{"op": "ready"}]}}\n```'
    http, seen = mock(lambda req, n: httpx.Response(200, json={"choices": [{"message": {"content": content}}]}))
    resp = complete(profile("openai", tool_mode="json"), "s", "用户", [TOOL], http=http)
    body = json.loads(seen[0].content)
    assert "tools" not in body
    assert "submit_actions" in body["messages"][1]["content"]
    assert resp.tool_calls[0].name == "submit_actions"
    assert resp.tool_calls[0].arguments == {"actions": [{"op": "ready"}]}


def test_json_mode_parse_variants():
    assert json_mode.parse('先说 {"x": "}"} 再给 {"tool": "submit_actions", "arguments": {"a": 1}}',
                           [TOOL])[0].arguments == {"a": 1}
    assert json_mode.parse('{"actions": []}', [TOOL])[0].arguments == {"actions": []}  # 单工具时接受裸参数
    assert json_mode.parse("没有 JSON", [TOOL]) == []


# ---------------------------------------------------------------- 连通性测试

def test_check_profile_ok_and_failure(monkeypatch):
    monkeypatch.setattr("cwagent.llm.client.secrets.choice", lambda _: "7")
    reply = {"content": [{"type": "tool_use", "id": "t", "name": "report_status", "input": {"status": "ok", "digits": "777777"}}],
             "stop_reason": "tool_use", "model": "m"}
    http, _ = mock(lambda req, n: httpx.Response(200, json=reply))
    result = check_profile(profile("anthropic"), http=http)
    assert result["ok"] and result["tool_call"] and not result["reasoning"]

    http, _ = mock(lambda req, n: httpx.Response(401, json={"error": {"message": "invalid x-api-key"}}))
    result = check_profile(profile("anthropic"), http=http)
    assert not result["ok"] and result["status"] == 401 and "invalid" in result["error"]


# ---------------------------------------------------------------- 模型列表

def test_list_models_openai():
    from cwagent.config import ProviderConfig
    from cwagent.llm import list_models

    http, seen = mock(lambda req, n: httpx.Response(200, json={"object": "list", "data": [
        {"id": "m-b", "owned_by": "x"}, {"id": "m-a", "owned_by": "x"}, {"id": "m-a"}]}))
    p = ProviderConfig(name="x", format="openai", base_url="https://api.example.com/v1", api_key="k")
    models = list_models(p, http=http)
    assert [m.id for m in models] == ["m-a", "m-b"]
    assert str(seen[0].url) == "https://api.example.com/v1/models"
    assert seen[0].headers["authorization"] == "Bearer k"


def test_list_models_anthropic_paged():
    from cwagent.config import ProviderConfig
    from cwagent.llm import list_models

    def handler(req, n):
        if n == 1:
            assert req.url.params["limit"] == "1000" and "after_id" not in req.url.params
            return httpx.Response(200, json={"data": [{"id": "c-1", "display_name": "C1", "max_input_tokens": 1000}],
                                             "has_more": True, "last_id": "c-1"})
        assert req.url.params["after_id"] == "c-1"
        return httpx.Response(200, json={"data": [{"id": "c-2"}], "has_more": False, "last_id": "c-2"})

    http, seen = mock(handler)
    p = ProviderConfig(name="a", format="anthropic", base_url="https://api.example.com", api_key="k")
    models = list_models(p, http=http)
    assert [m.id for m in models] == ["c-1", "c-2"] and models[0].context_window == 1000
    assert str(seen[0].url).startswith("https://api.example.com/v1/models")
    assert seen[0].headers["x-api-key"] == "k"


def test_list_models_error():
    from cwagent.config import ProviderConfig
    from cwagent.llm import list_models

    http, _ = mock(lambda req, n: httpx.Response(404, text="not found"))
    p = ProviderConfig(name="a", format="openai", base_url="https://api.example.com", api_key="k")
    with pytest.raises(LLMError) as info:
        list_models(p, http=http)
    assert info.value.status == 404
