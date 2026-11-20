"""Antigravity 插件的生命周期 hook:对局未结束时自动让当前会话继续下棋。

由 hooks.json 调用:
  python3 scripts/hook.py post-tool
  python3 scripts/hook.py stop

只使用 Python 标准库,与 cwagent serve 的 /mcp 端点通信,复用服务端的 hook 工具
和会话归属判断(BRIDGE.agent_session),不会干扰未调用过 cwagent 的普通会话。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any
from urllib.error import URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

MAX_CONSECUTIVE_CONTINUES = 8
HTTP_TIMEOUT = 3.0
READONLY_TOOLS = frozenset({"observe", "status"})
_CWAGENT_NAME_RE = re.compile(r"^(?:.*[:_\-/])?cwagent[:_\-/]+(.+)$", re.IGNORECASE)


def _plugin_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _state_dir() -> Path:
    custom = os.environ.get("CWAGENT_HOOK_STATE_DIR")
    return Path(custom) if custom else Path(tempfile.gettempdir()) / "cwagent-antigravity-hooks"


def _state_file(conversation_id: str) -> Path:
    digest = hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()[:16]
    return _state_dir() / f"{digest}.json"


def _read_state(conversation_id: str) -> dict[str, Any] | None:
    path = _state_file(conversation_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"continues": 0}
    except (OSError, json.JSONDecodeError):
        return None


def _write_state(conversation_id: str, data: dict[str, Any]) -> None:
    path = _state_file(conversation_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def _clear_state(conversation_id: str) -> None:
    try:
        _state_file(conversation_id).unlink(missing_ok=True)
    except OSError:
        pass


def _mcp_url() -> str | None:
    url = os.environ.get("CWAGENT_MCP_URL", "").strip()
    if not url:
        config_path = _plugin_root() / "mcp_config.json"
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            server = (cfg.get("mcpServers") or {}).get("cwagent") or {}
            url = str(server.get("url") or server.get("serverUrl") or "").strip()
        except (OSError, json.JSONDecodeError, AttributeError):
            return None
    if not url:
        return None
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    # hook 工具无需 wait 参数,去掉查询串保持请求简洁
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/mcp", "", ""))


def extract_cwagent_tool(tool_call: Any) -> str | None:
    """判断一次工具调用是否属于 cwagent MCP 服务;命中时返回小写工具名,否则返回 None。"""
    if not isinstance(tool_call, dict):
        return None
    name = str(tool_call.get("name") or "").strip()
    args = tool_call.get("args") if isinstance(tool_call.get("args"), dict) else {}

    # Lazy MCP 调用:call_mcp_tool / mcp_tool,参数带 ServerName 和 ToolName
    server = str(
        args.get("ServerName")
        or args.get("serverName")
        or args.get("server_name")
        or ""
    ).strip().lower()
    if server:
        if server == "cwagent" or server.endswith((":cwagent", "_cwagent", "-cwagent")):
            tool = str(
                args.get("ToolName")
                or args.get("toolName")
                or args.get("tool_name")
                or ""
            ).strip().lower()
            return tool
        return None

    # Eager MCP 调用:如 mcp_cwagent_start_run / mcp__cwagent__wait
    m = _CWAGENT_NAME_RE.search(name)
    if m:
        return m.group(1).strip().lower()
    return None


def _parse_mcp_rpc_response(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            obj = json.loads(text)
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    # Streamable HTTP 默认返回 text/event-stream
    last_obj: dict[str, Any] | None = None
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        chunk = line[5:].strip()
        if not chunk:
            continue
        try:
            obj = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            last_obj = obj
            if "result" in obj:
                return obj
    return last_obj


def call_hook_tool(url: str, event: str, session: str, *, timeout: float = HTTP_TIMEOUT) -> dict[str, Any] | None:
    """直接向无状态 Streamable HTTP /mcp 端点调用 hook 工具,返回其解析后的 JSON dict。"""
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "hook", "arguments": {"event": event, "session": session}},
        }
    ).encode("utf-8")
    req = Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except (URLError, OSError, TimeoutError, ValueError):
        return None
    rpc = _parse_mcp_rpc_response(raw)
    if not rpc or "result" not in rpc:
        return None
    result = rpc.get("result") or {}
    if result.get("isError"):
        return None
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            try:
                parsed = json.loads(item.get("text") or "{}")
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
    return {}


def handle_post_tool(payload: dict[str, Any]) -> dict[str, Any]:
    conversation_id = str(payload.get("conversationId") or "").strip()
    if not conversation_id:
        return {}
    tool = extract_cwagent_tool(payload.get("toolCall"))
    if tool is None:
        return {}
    if tool == "stop_run" and not payload.get("error"):
        _clear_state(conversation_id)
        return {}
    if tool in READONLY_TOOLS:
        return {}
    _write_state(conversation_id, {"continues": 0})
    url = _mcp_url()
    if url:
        call_hook_tool(url, "tool", conversation_id)
    return {}


def handle_stop(payload: dict[str, Any]) -> dict[str, Any]:
    conversation_id = str(payload.get("conversationId") or "").strip()
    if not conversation_id:
        return {}
    reason = str(payload.get("terminationReason") or "").strip()
    if reason and reason != "NO_TOOL_CALL":
        return {}
    if payload.get("fullyIdle") is False:
        return {}
    state = _read_state(conversation_id)
    if state is None:
        return {}
    continues = int(state.get("continues") or 0)
    if continues >= MAX_CONSECUTIVE_CONTINUES:
        _clear_state(conversation_id)
        return {}
    url = _mcp_url()
    if not url:
        return {}
    out = call_hook_tool(url, "stop", conversation_id)
    if out is None:
        # serve 未启动或网络暂不可达时放行,不阻塞会话
        return {}
    specific = out.get("hookSpecificOutput") if isinstance(out.get("hookSpecificOutput"), dict) else {}
    keep_playing = str(specific.get("additionalContext") or "").strip()
    if keep_playing:
        _write_state(conversation_id, {"continues": continues + 1})
        return {"decision": "continue", "reason": keep_playing}
    _clear_state(conversation_id)
    return {}


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    mode = args[0] if args else ""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except (OSError, json.JSONDecodeError):
        payload = {}

    if mode == "post-tool":
        result = handle_post_tool(payload)
    elif mode == "stop":
        result = handle_stop(payload)
    else:
        result = {}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
