"""OpenCode Go 请求和对局会话的生命周期。"""

import json
from uuid import UUID

import httpx
import pytest

from cwagent.config import AppConfig, ModelEndpoint, ModelProfile, ProviderConfig, Roles
from cwagent.llm import check_profile, complete, list_models
from cwagent.llm import client as llm_client
from cwagent.orchestrator.session import endpoints
from tests.test_llm import mock
from tests.test_streaming import chunk, sse, stream_response


def reply(fmt):
    if fmt == "openai":
        return {"choices": [{"message": {"content": "ok"}}]}
    return {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}


@pytest.mark.parametrize("prefix", ["/zen/go", "/inference/go"])
@pytest.mark.parametrize("fmt", ["openai", "anthropic"])
def test_completion_session_survives_retries_and_followup_calls(prefix, fmt):
    def handler(req, n):
        if n == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, json={"error": "busy"})
        return httpx.Response(200, json=reply(fmt))

    http, seen = mock(handler)
    ep = ModelEndpoint(name="go", format=fmt, base_url=f"https://opencode.ai{prefix}/v1", model="m",
                       api_key="test-key", extra_headers={"X-OpenCode-Session": "old-fixed-id", "X-Custom": "keep"})
    for _ in range(2):
        assert complete(ep, "系统", "局面", http=http, sleep=lambda _: None).text == "ok"
    assert len(seen) == 3
    assert {req.headers["x-opencode-session"] for req in seen} == {ep.session_id}
    assert UUID(ep.session_id)
    for req in seen:
        assert len(req.headers.get_list("x-opencode-session")) == 1
        assert req.headers["user-agent"].startswith("cwagent/")
        assert req.headers["x-custom"] == "keep"
        assert req.headers["authorization" if fmt == "openai" else "x-api-key"] == (
            "Bearer test-key" if fmt == "openai" else "test-key")
    assert ep.extra_headers["X-OpenCode-Session"] == "old-fixed-id"


@pytest.mark.parametrize("base", [
    "https://api.example.com/v1", "https://opencode.ai/zen/v1",
    "https://opencode.ai/inference/gopher/v1", "https://opencode.ai.example.com/zen/go/v1",
])
def test_other_providers_do_not_receive_opencode_headers(base):
    http, seen = mock(lambda req, n: httpx.Response(200, json=reply("openai")))
    ep = ModelEndpoint(name="p", format="openai", base_url=base, model="m")
    complete(ep, "系统", "局面", http=http)
    assert "x-opencode-session" not in seen[0].headers
    assert not seen[0].headers["user-agent"].startswith("cwagent/")


@pytest.mark.parametrize("fmt", ["openai", "anthropic"])
@pytest.mark.parametrize("base,enabled", [
    ("https://gateway.example.com/v1", True),
    ("https://opencode.ai/zen/go/v1", False),
])
def test_explicit_option_controls_completions_and_catalog_for_any_url(fmt, base, enabled):
    def handler(req, n):
        return httpx.Response(200, json={"data": [{"id": "m"}]} if req.method == "GET" else reply(fmt))

    http, seen = mock(handler)
    provider = ProviderConfig(name="p", format=fmt, base_url=base, opencode_go=enabled)
    ep = ModelEndpoint.of(provider, ModelProfile(name="m", provider="p", model="m"))
    assert ep.opencode_go is enabled
    complete(ep, "系统", "局面", http=http)
    list_models(provider, http=http)
    assert all(("x-opencode-session" in req.headers) is enabled for req in seen)
    if enabled:
        assert seen[0].headers["x-opencode-session"] == ep.session_id
        assert seen[1].headers["x-opencode-session"] != ep.session_id


def test_all_decision_roles_share_one_session_and_next_run_changes_it():
    cfg = AppConfig(
        providers=[ProviderConfig(name="go", format="openai", base_url="https://opencode.ai/inference/go/openai/v1")],
        models=[ModelProfile(name=name, provider="go", model=name) for name in ("main", "deep", "quick")],
        roles=Roles(tactical="main", strategic="deep", chooser="deep", navigator="quick"),
    )
    first, second = endpoints(cfg, None, "high"), endpoints(cfg, None)
    session = first["default"].session_id
    assert set(first) == {"default", "strategic", "chooser", "navigator"}
    assert {ep.session_id for ep in first.values()} == {session}
    assert len({ep.session_id for ep in second.values()}) == 1
    assert second["default"].session_id != session
    assert endpoints(cfg, "main")["navigator"].session_id != session
    assert "session_id" not in cfg.model_dump_json()
    assert "session_id" not in first["default"].model_dump()

    http, seen = mock(lambda req, n: httpx.Response(200, json=reply("openai")))
    for ep in [*first.values(), first["default"], second["default"]]:
        complete(ep, "系统", "局面", http=http)
    assert {req.headers["x-opencode-session"] for req in seen[:-1]} == {session}
    assert seen[-1].headers["x-opencode-session"] == second["default"].session_id


@pytest.mark.parametrize("fallback", [False, True])
def test_streaming_and_fallback_use_the_run_session(fallback, monkeypatch):
    monkeypatch.setattr(llm_client, "_NO_STREAM", set())

    def handler(req, n):
        if json.loads(req.content).get("stream"):
            if fallback:
                return httpx.Response(400, json={"error": "stream unsupported"})
            return stream_response(sse(chunk({"content": "ok"}), chunk({}, finish="stop"), done=True))
        return httpx.Response(200, json=reply("openai"))

    http, seen = mock(handler)
    ep = ModelEndpoint(name="go", format="openai", base_url="https://opencode.ai/inference/go/openai/v1", model="m")
    for _ in range(2):
        assert complete(ep, "系统", "局面", http=http, on_delta=lambda *_: None).text == "ok"
    assert len(seen) == (3 if fallback else 2)
    assert {req.headers["x-opencode-session"] for req in seen} == {ep.session_id}


@pytest.mark.parametrize("fmt", ["openai", "anthropic"])
def test_model_catalog_has_its_own_session_and_keeps_it_while_paging(fmt):
    def handler(req, n):
        return httpx.Response(200, json={"data": [{"id": f"m-{n}"}], "has_more": n % 2 == 1, "last_id": str(n)})

    http, seen = mock(handler)
    provider = ProviderConfig(name="go", format=fmt, base_url="https://opencode.ai/zen/go/v1",
                              extra_headers={"user-agent": "custom-client/1.0"})
    assert len(list_models(provider, http=http)) == 2
    assert len(list_models(provider, http=http)) == 2
    first, second = [req.headers["x-opencode-session"] for req in seen[:2]], [req.headers["x-opencode-session"] for req in seen[2:]]
    assert first[0] == first[1] and second[0] == second[1] and first[0] != second[0]
    assert all(req.headers["user-agent"] == "custom-client/1.0" for req in seen)
    assert provider.extra_headers == {"user-agent": "custom-client/1.0"}


def test_connection_checks_use_separate_sessions_without_changing_run_endpoint():
    http, seen = mock(lambda req, n: httpx.Response(200, json=reply("openai")))
    ep = ModelEndpoint(name="go", format="openai", base_url="https://opencode.ai/zen/go/v1", model="m")
    check_profile(ep, http=http)
    check_profile(ep, http=http)
    sessions = {req.headers["x-opencode-session"] for req in seen}
    assert len(sessions) == 2 and ep.session_id not in sessions
