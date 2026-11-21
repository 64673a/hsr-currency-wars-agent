"""Antigravity 插件适配测试:install.py 与 scripts/hook.py。"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import pytest
import uvicorn

from cwagent.config import ConfigStore
from cwagent.llm.external import BRIDGE
from cwagent.memory import MemoryStore
from cwagent.server import create_app

INTEGRATION_DIR = Path(__file__).resolve().parent.parent / "integrations" / "antigravity"
ALLOWED_PLUGIN_FIELDS = {
    "name",
    "displayName",
    "version",
    "description",
    "logo",
    "suggestedPrompts",
    "author",
    "homepage",
    "repository",
    "disabled",
}


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


install_mod = _load_module("cwagent_antigravity_install", INTEGRATION_DIR / "install.py")
hook_mod = _load_module("cwagent_antigravity_hook", INTEGRATION_DIR / "scripts" / "hook.py")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def mcp_server(tmp_path):
    BRIDGE.system = ""
    app = create_app(
        config_store=ConfigStore(tmp_path / "c.yaml"),
        knowledge_path=tmp_path / "none.sqlite",
        templates_dir=tmp_path / "tpl",
        web_dist=tmp_path / "dist",
        runs_dir=tmp_path / "runs",
        memory_store=MemoryStore(tmp_path / "m.yaml", tmp_path / "m.sqlite"),
        device_factory=None,
        agents_file=tmp_path / "agents.json",
    )
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    srv.should_exit = True
    thread.join(5)


def test_plugin_manifest_and_static_files():
    manifest = json.loads((INTEGRATION_DIR / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "cwagent"
    assert set(manifest) <= ALLOWED_PLUGIN_FIELDS
    assert 1 <= len(manifest["suggestedPrompts"]) <= 3

    hooks = json.loads((INTEGRATION_DIR / "hooks.json").read_text(encoding="utf-8"))
    spec = hooks["cwagent-keep-playing"]
    assert isinstance(spec["PostToolUse"], list) and "matcher" in spec["PostToolUse"][0]
    assert isinstance(spec["Stop"], list) and "command" in spec["Stop"][0]

    skill = (INTEGRATION_DIR / "skills" / "cwagent-play" / "SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---\nname: cwagent-play\ndescription:")


def test_normalize_url():
    assert install_mod.normalize_url("http://192.168.0.200:8770") == "http://192.168.0.200:8770/mcp?agent=antigravity"
    assert install_mod.normalize_url("http://192.168.0.200:8770/") == "http://192.168.0.200:8770/mcp?agent=antigravity"
    assert install_mod.normalize_url("http://127.0.0.1:8770/mcp?wait=120") == "http://127.0.0.1:8770/mcp?wait=120&agent=antigravity"
    assert install_mod.normalize_url("http://127.0.0.1:8770/mcp?wait=120", wait=600) == "http://127.0.0.1:8770/mcp?wait=600&agent=antigravity"
    assert install_mod.normalize_url("http://127.0.0.1:8770/mcp?agent=gemini") == "http://127.0.0.1:8770/mcp?agent=gemini"

    with pytest.raises(ValueError):
        install_mod.normalize_url("ftp://127.0.0.1:8770/mcp")
    with pytest.raises(ValueError):
        install_mod.normalize_url("http://127.0.0.1:8770/other")
    with pytest.raises(ValueError):
        install_mod.normalize_url("http://127.0.0.1:8770", wait=0)


def test_install_creates_plugin_and_honors_no_hooks(tmp_path):
    target = tmp_path / "plugins" / "cwagent"
    install_mod.main(
        [
            "--url",
            "http://192.168.0.200:8770",
            "--wait",
            "120",
            "--target",
            str(target),
            "--python",
            sys.executable,
        ]
    )
    assert (target / "plugin.json").is_file()
    assert (target / "skills" / "cwagent-play" / "SKILL.md").is_file()
    assert (target / "scripts" / "hook.py").is_file()

    mcp_cfg = json.loads((target / "mcp_config.json").read_text(encoding="utf-8"))
    assert mcp_cfg == {"mcpServers": {"cwagent": {"url": "http://192.168.0.200:8770/mcp?wait=120&agent=antigravity"}}}

    hooks_cfg = json.loads((target / "hooks.json").read_text(encoding="utf-8"))
    spec = hooks_cfg["cwagent-keep-playing"]
    assert sys.executable in spec["PostToolUse"][0]["hooks"][0]["command"]
    assert sys.executable in spec["Stop"][0]["command"]

    # 再次安装带 --no-hooks 应移除旧的 hooks.json
    install_mod.main(["--url", "http://127.0.0.1:8770", "--target", str(target), "--no-hooks"])
    assert not (target / "hooks.json").exists()


def test_extract_cwagent_tool():
    assert hook_mod.extract_cwagent_tool({"name": "mcp_cwagent_start_run", "args": {}}) == "start_run"
    assert hook_mod.extract_cwagent_tool({"name": "mcp__cwagent__wait", "args": {}}) == "wait"
    assert hook_mod.extract_cwagent_tool({"name": "mcp__plugin_cwagent_cwagent__stop_run", "args": {}}) == "stop_run"
    assert (
        hook_mod.extract_cwagent_tool(
            {"name": "call_mcp_tool", "args": {"ServerName": "cwagent", "ToolName": "act"}}
        )
        == "act"
    )
    assert (
        hook_mod.extract_cwagent_tool(
            {"name": "mcp_tool", "args": {"serverName": "plugin:cwagent", "toolName": "observe"}}
        )
        == "observe"
    )
    assert hook_mod.extract_cwagent_tool({"name": "call_mcp_tool", "args": {"ServerName": "codegraph", "ToolName": "search"}}) is None
    assert hook_mod.extract_cwagent_tool({"name": "run_command", "args": {"CommandLine": "ls"}}) is None


def _run_hook(plugin_dir: Path, mode: str, payload: dict, state_dir: Path) -> dict:
    env = os.environ.copy()
    env["CWAGENT_HOOK_STATE_DIR"] = str(state_dir)
    proc = subprocess.run(
        [sys.executable, str(plugin_dir / "scripts" / "hook.py"), mode],
        cwd=plugin_dir,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    return json.loads(proc.stdout)


def test_hook_end_to_end_with_mcp_server(mcp_server, tmp_path):
    plugin_dir = tmp_path / "plugin"
    state_dir = tmp_path / "state"
    install_mod.install(url=f"{mcp_server}?wait=50", target=plugin_dir, python_exe=sys.executable)

    # 未调用过 cwagent 的普通会话直接放行
    assert _run_hook(plugin_dir, "stop", {"conversationId": "conv-a", "terminationReason": "NO_TOOL_CALL", "fullyIdle": True}, state_dir) == {}

    BRIDGE.begin(threading.Event())
    try:
        # 只读 observe 不认领、不写标记
        assert _run_hook(
            plugin_dir,
            "post-tool",
            {"conversationId": "conv-a", "toolCall": {"name": "call_mcp_tool", "args": {"ServerName": "cwagent", "ToolName": "observe"}}},
            state_dir,
        ) == {}
        assert BRIDGE.agent_session == ""

        # Lazy 调用 start_run 后认领本会话
        assert _run_hook(
            plugin_dir,
            "post-tool",
            {"conversationId": "conv-a", "toolCall": {"name": "call_mcp_tool", "args": {"ServerName": "cwagent", "ToolName": "start_run"}}},
            state_dir,
        ) == {}
        assert BRIDGE.agent_session == "conv-a"

        # 正常停下 -> 要求继续
        out = _run_hook(
            plugin_dir,
            "stop",
            {"conversationId": "conv-a", "terminationReason": "NO_TOOL_CALL", "fullyIdle": True},
            state_dir,
        )
        assert out["decision"] == "continue" and "wait" in out["reason"]

        # 用户取消或仍有后台任务 -> 放行
        assert _run_hook(
            plugin_dir,
            "stop",
            {"conversationId": "conv-a", "terminationReason": "USER_CANCELED", "fullyIdle": True},
            state_dir,
        ) == {}
        assert _run_hook(
            plugin_dir,
            "stop",
            {"conversationId": "conv-a", "terminationReason": "NO_TOOL_CALL", "fullyIdle": False},
            state_dir,
        ) == {}

        # 换会话 conv-b(Eager 工具名)接手后,conv-a 放行,conv-b 继续
        assert _run_hook(
            plugin_dir,
            "post-tool",
            {"conversationId": "conv-b", "toolCall": {"name": "mcp_cwagent_wait", "args": {}}},
            state_dir,
        ) == {}
        assert _run_hook(
            plugin_dir,
            "stop",
            {"conversationId": "conv-a", "terminationReason": "NO_TOOL_CALL", "fullyIdle": True},
            state_dir,
        ) == {}
        out_b = _run_hook(
            plugin_dir,
            "stop",
            {"conversationId": "conv-b", "terminationReason": "NO_TOOL_CALL", "fullyIdle": True},
            state_dir,
        )
        assert out_b["decision"] == "continue"

        # 连续续行达到上限后放行,避免死循环
        for _ in range(hook_mod.MAX_CONSECUTIVE_CONTINUES - 1):
            assert _run_hook(
                plugin_dir,
                "stop",
                {"conversationId": "conv-b", "terminationReason": "NO_TOOL_CALL", "fullyIdle": True},
                state_dir,
            )["decision"] == "continue"
        assert _run_hook(
            plugin_dir,
            "stop",
            {"conversationId": "conv-b", "terminationReason": "NO_TOOL_CALL", "fullyIdle": True},
            state_dir,
        ) == {}

        # 重新调用工具重置计数,调用 stop_run 清除本机标记
        _run_hook(
            plugin_dir,
            "post-tool",
            {"conversationId": "conv-b", "toolCall": {"name": "mcp_cwagent_act", "args": {}}},
            state_dir,
        )
        _run_hook(
            plugin_dir,
            "post-tool",
            {"conversationId": "conv-b", "toolCall": {"name": "mcp_cwagent_stop_run", "args": {}}},
            state_dir,
        )
        assert _run_hook(
            plugin_dir,
            "stop",
            {"conversationId": "conv-b", "terminationReason": "NO_TOOL_CALL", "fullyIdle": True},
            state_dir,
        ) == {}
    finally:
        BRIDGE.end("settlement")

    # 对局结束后放行
    _run_hook(
        plugin_dir,
        "post-tool",
        {"conversationId": "conv-c", "toolCall": {"name": "mcp_cwagent_wait", "args": {}}},
        state_dir,
    )
    assert _run_hook(
        plugin_dir,
        "stop",
        {"conversationId": "conv-c", "terminationReason": "NO_TOOL_CALL", "fullyIdle": True},
        state_dir,
    ) == {}


def test_hook_unreachable_server_is_silent(tmp_path):
    plugin_dir = tmp_path / "plugin"
    state_dir = tmp_path / "state"
    closed_port = _free_port()
    install_mod.install(url=f"http://127.0.0.1:{closed_port}/mcp", target=plugin_dir, python_exe=sys.executable)

    assert _run_hook(
        plugin_dir,
        "post-tool",
        {"conversationId": "conv-offline", "toolCall": {"name": "mcp_cwagent_start_run", "args": {}}},
        state_dir,
    ) == {}
    assert _run_hook(
        plugin_dir,
        "stop",
        {"conversationId": "conv-offline", "terminationReason": "NO_TOOL_CALL", "fullyIdle": True},
        state_dir,
    ) == {}
