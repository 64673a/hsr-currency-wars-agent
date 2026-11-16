"""外部 agent 接入的 MCP 端点(/mcp,Streamable HTTP)。设计见 docs/TECH_DESIGN.md 11.9。

用户自己的 agent 工具(DSH、Claude Code、Codex 等)连上来,通过这几个工具下棋:rules 读规则,start_run / wait 取局面,
act 交回答。局面和回答经过 llm/external.py 的信箱,对局编排和 API 模式完全一样。
"""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import anyio
from mcp.server import Server, ServerRequestContext
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp, StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .. import __version__, paths
from ..brain.screen_agent import ACT_TOOL, PROMPTS, load_rules
from ..config import ConfigError
from ..llm.external import BRIDGE, EXTERNAL, Turn
from .runs import PlayController

DEFAULT_WAIT = 50     # 低于常见客户端 60 秒的默认工具超时;地址上带 ?wait=秒数 可以调大
MAX_WAIT = 3600

INSTRUCTIONS = """本服务让你自主游玩《崩坏:星穹铁道》的「货币战争」。程序负责截图、识别界面和执行操作,只在需要判断时把局面交给你。
1. 先调用 rules 读玩法和操作说明;上下文被压缩后再读一次。
2. 调用 start_run 开始对局;对局已经在网页上开始时调用 wait。
3. 拿到局面后自己判断,调用 act 提交分析和操作。act 执行完会返回结果和下一个局面,接着判断。
4. 返回「还在等待」时(战斗、动画进行中),直接再调用 wait,不要停下来问用户。
5. 一直下到返回「对局结束」为止。每个回合的第一条消息带完整的本局档案,不需要回顾之前的回合。"""

KEEP_PLAYING = "货币战争对局还在进行,不要停下:调用 wait 取下一个局面,一直下到返回「对局结束」。用户要求停下时先调用 stop_run。"

RESULTS = {"settlement": "打完了(到了结算界面)", "pause": "暂停了,需要人处理", "stopped": "已停止",
           "timeout": "超过了对局时长上限", "max_steps": "步数用完了", "interrupted": "有人接管了键鼠", "error": "出错了"}

_EMPTY: dict[str, Any] = {"type": "object", "properties": {}}
TOOLS = [
    Tool(name="rules", description="读取玩法、规则和操作说明。开始前读一次,上下文被压缩后再读。", input_schema=_EMPTY),
    Tool(name="start_run", description="开始一局对局,等到第一个需要你判断的局面后返回它。",
         input_schema={"type": "object", "properties": {
             "difficulty": {"type": "string", "description": "难度,如 A8-1;不填用设置里的"}}}),
    Tool(name="wait", description="等下一个需要你判断的局面并返回它。返回「还在等待」时再调用一次。", input_schema=_EMPTY),
    Tool(name="act", description=ACT_TOOL.description + "执行完后返回执行结果和下一个需要判断的局面。",
         input_schema=ACT_TOOL.parameters),
    Tool(name="status", description="查看对局状态。", input_schema=_EMPTY),
    Tool(name="stop_run", description="停止当前对局。", input_schema=_EMPTY),
    Tool(name="hook", description="供 Claude Code 插件的 hook 调用,不要直接调用。",
         input_schema={"type": "object", "properties": {
             "event": {"type": "string", "enum": ["tool", "stop"]}, "session": {"type": "string"}},
             "required": ["event"]}),
]


class ToolError(Exception):
    pass


def _host_ok(host: str) -> bool:
    name = host[1:host.find("]")] if host.startswith("[") else host.rsplit(":", 1)[0]
    if name == "localhost":
        return True
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


class HostGuard:
    """只接受 Host 为 IP 地址或 localhost 的请求:DNS 重绑定的网页用的是域名,会被挡住。"""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            host = dict(scope["headers"]).get(b"host", b"").decode("latin-1")
            if not _host_ok(host):
                await PlainTextResponse("请用 IP 地址或 localhost 访问", status_code=421)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _wait_seconds(ctx: ServerRequestContext) -> float:
    request = ctx.request
    try:
        value = float(request.query_params.get("wait", DEFAULT_WAIT)) if request is not None else DEFAULT_WAIT
    except (AttributeError, ValueError):
        value = DEFAULT_WAIT
    return min(max(value, 1.0), MAX_WAIT)


def _turn_text(turn: Turn) -> str:
    return f"【第 {turn.seq} 轮{' · 新回合' if turn.opening else ''}】\n{turn.text}"


def _rules_text() -> str:
    if BRIDGE.system:
        return BRIDGE.system
    rules = load_rules(paths.root() / "knowledge" / "rules.md")
    return (PROMPTS / "system.md").read_text(encoding="utf-8").replace("{rules}", rules)


class Tools:
    def __init__(self, player: PlayController):
        self.player = player

    def _idle(self) -> str:
        st = self.player.status()
        agent = st["agent"]
        if agent["active"]:
            return "【还在等待】游戏正在进行(战斗、动画或程序在处理),还没有需要你判断的局面。调用 wait 继续等。"
        if st["running"]:
            return f"当前对局用的是模型档案「{st['profile'] or '?'}」,不归你控制。"
        if agent["ended"]:
            result = agent["ended"]
            lines = [f"【对局结束】{RESULTS.get(result, result)}。"]
            if st["error"]:
                lines.append(f"错误:{st['error']}")
            if st["log"]:
                lines.append("最后的记录:\n" + "\n".join(st["log"][-3:]))
            lines.append("这一局已经结束,停下来向用户汇报;用户要求再打时才调用 start_run。")
            return "\n".join(lines)
        return "现在没有对局。调用 start_run 开始。"

    async def _take(self, wait: float) -> str:
        turn = await anyio.to_thread.run_sync(BRIDGE.take, wait, abandon_on_cancel=True)
        return _turn_text(turn) if turn else self._idle()

    async def rules(self, args: dict[str, Any], wait: float) -> str:
        return _rules_text()

    async def start_run(self, args: dict[str, Any], wait: float) -> str:
        if BRIDGE.status()["active"]:
            return "对局已经在进行。\n\n" + await self._take(wait)
        difficulty = args.get("difficulty") or None
        try:
            await anyio.to_thread.run_sync(lambda: self.player.start(model=EXTERNAL, difficulty=difficulty))
        except (RuntimeError, ConfigError) as e:
            raise ToolError(f"没能开始对局:{e}") from None
        return "对局已开始。\n\n" + await self._take(wait)

    async def wait(self, args: dict[str, Any], wait: float) -> str:
        return await self._take(wait)

    async def act(self, args: dict[str, Any], wait: float) -> str:
        if not BRIDGE.answer(args):
            raise ToolError("现在没有等你回答的局面(可能已经回答过)。调用 wait 看最新局面。")
        return await self._take(wait)

    async def status(self, args: dict[str, Any], wait: float) -> str:
        st = self.player.status()
        keys = ("running", "stopping", "profile", "difficulty", "run_id", "result", "error", "agent")
        return json.dumps({k: st.get(k) for k in keys} | {"log": st["log"][-5:]}, ensure_ascii=False, indent=1)

    async def hook(self, args: dict[str, Any], wait: float) -> str:
        """Claude Code 插件(integrations/claude-code)的 hook,返回 hook 输出(JSON)。
        tool:agent 调用了本服务的工具,记下它的会话;stop:agent 想停下,对局还在进行、且就是它在下时让它接着下。
        连续续行的次数由 Claude Code 自己限制(调用过工具就重新计数),卡住时不会无限循环。"""
        session = str(args.get("session") or "")
        if args.get("event") == "tool":
            BRIDGE.claim(session)
        elif args.get("event") == "stop" and session and BRIDGE.status()["active"] and BRIDGE.agent_session == session:
            return json.dumps({"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": KEEP_PLAYING}},
                              ensure_ascii=False)
        return "{}"

    async def stop_run(self, args: dict[str, Any], wait: float) -> str:
        if not self.player.running:
            return "现在没有对局。"
        self.player.stop()
        return "已请求停止,当前操作做完后停下。"


def create_mcp(player: PlayController) -> tuple[ASGIApp, Callable[[], AbstractAsyncContextManager[None]]]:
    """返回 /mcp 的 ASGI 应用,以及要在宿主应用 lifespan 里进入的上下文。"""
    tools = Tools(player)
    handlers: dict[str, Callable[[dict[str, Any], float], Awaitable[str]]] = {
        t.name: getattr(tools, t.name) for t in TOOLS}

    async def list_tools(ctx: ServerRequestContext, params: PaginatedRequestParams | None) -> ListToolsResult:
        return ListToolsResult(tools=TOOLS)

    async def call_tool(ctx: ServerRequestContext, params: CallToolRequestParams) -> CallToolResult:
        handler = handlers.get(params.name)
        try:
            if handler is None:
                raise ToolError(f"没有工具 {params.name}")
            text = await handler(dict(params.arguments or {}), _wait_seconds(ctx))
        except ToolError as e:
            return CallToolResult(content=[TextContent(type="text", text=str(e))], is_error=True)
        return CallToolResult(content=[TextContent(type="text", text=text)])

    server = Server("cwagent", version=__version__, title="货币战争 agent", instructions=INSTRUCTIONS,
                    on_list_tools=list_tools, on_call_tool=call_tool)
    # 无状态:serve 重启后 agent 不用重新握手;Host 检查由 HostGuard 做
    manager = StreamableHTTPSessionManager(
        app=server, stateless=True,
        security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    return HostGuard(StreamableHTTPASGIApp(manager)), manager.run
