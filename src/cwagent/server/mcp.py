"""外部 agent 接入的 MCP 端点(/mcp,Streamable HTTP)。设计见 docs/TECH_DESIGN.md 11.9。

用户自己的 agent 工具(DSH、Claude Code、Codex 等)连上来,通过这几个工具下棋:rules 读规则,start_run / wait 取局面,
act 交回答。局面和回答经过 llm/external.py 的信箱,对局编排和 API 模式完全一样。
"""

from __future__ import annotations

import ipaddress
import json
import copy
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
    ImageContent,
    Tool,
)
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .. import __version__, paths
from ..brain.screen_agent import ACT_TOOL, PROMPTS, ActInput, load_rules
from ..llm.base import ImageInput
from pydantic import ValidationError
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
5. 一直下到返回「对局结束」为止。新回合只追加新增事实和经历;压缩上下文或接手时调用 snapshot 恢复完整档案。
6. 图片就是当前观察。按截图标号操作,act 的 turn 填本次局面的轮次,避免重复提交旧操作。"""

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
    Tool(name="snapshot", description="读取完整档案及最近截图,用于接手或上下文压缩后恢复,不执行游戏操作。", input_schema=_EMPTY),
    Tool(name="observe", description="只截图并标号,用于连通性与读图测试;不开始对局、不发送键鼠。", input_schema=_EMPTY),
    Tool(name="stop_run", description="停止当前对局。", input_schema=_EMPTY),
    Tool(name="hook", description="供 Claude Code 插件的 hook 调用,不要直接调用。",
         input_schema={"type": "object", "properties": {
             "event": {"type": "string", "enum": ["tool", "stop"]}, "session": {"type": "string"}},
             "required": ["event"]}),
]
TOOLS[3].input_schema = copy.deepcopy(ACT_TOOL.parameters)
TOOLS[3].input_schema["properties"]["turn"] = {"type": "integer", "description": "本次局面的轮次"}


class Observation(str):
    def __new__(cls, text: str, images: list[ImageInput] | None = None):
        obj = super().__new__(cls, text)
        obj.images = images or []
        return obj


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
    return Observation(f"【第 {turn.seq} 轮{' · 新回合' if turn.opening else ''}】\n{turn.text}", turn.images)


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
            result = await self._take(wait)
            return Observation("对局已经在进行。\n\n" + result, getattr(result, "images", []))
        difficulty = args.get("difficulty") or None
        try:
            await anyio.to_thread.run_sync(lambda: self.player.start(model=EXTERNAL, difficulty=difficulty))
        except (RuntimeError, ConfigError) as e:
            raise ToolError(f"没能开始对局:{e}") from None
        result = await self._take(wait)
        return Observation("对局已开始。\n\n" + result, getattr(result, "images", []))

    async def wait(self, args: dict[str, Any], wait: float) -> str:
        return await self._take(wait)

    async def act(self, args: dict[str, Any], wait: float) -> str:
        payload = {k: v for k, v in args.items() if k != "turn"}
        try:
            ActInput.model_validate(payload)
        except ValidationError as e:
            raise ToolError(str(e)) from None
        if not BRIDGE.answer(payload, turn=args.get("turn")):
            raise ToolError("现在没有等你回答的局面(可能已经回答过)。调用 wait 看最新局面。")
        return await self._take(wait)

    async def snapshot(self, args: dict[str, Any], wait: float) -> str:
        return _turn_text(BRIDGE.snapshot())

    async def observe(self, args: dict[str, Any], wait: float) -> str:
        from ..vision.annotate import annotate, legend
        from ..vision.ocr import Ocr
        from ..orchestrator.screens import classify

        if self.player.running:
            raise ToolError("对局正在运行,请调用 snapshot 查看最近的局面")
        if self.player.device_factory is None:
            raise ToolError("只能在装有游戏的 Windows 上截图")

        def grab():
            device = self.player.device_factory()
            try:
                frame = device.grab()
                boxes = Ocr()(frame.image)
                screen = classify(boxes)
                from ..vision.shop import shop_state
                picture, marks = annotate(frame.image, boxes, screen=screen, shop_open=shop_state(boxes) is True)
                return Observation(f"只读截图;程序判断界面:{screen}\n" + legend(marks), [picture])
            finally:
                close = getattr(device, "close", None)
                if close:
                    close()
        try:
            return await anyio.to_thread.run_sync(grab)
        except Exception as e:
            raise ToolError(f"只读截图失败:{e}") from None

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
        content = [TextContent(type="text", text=str(text))]
        content += [ImageContent(type="image", data=i.data, mime_type=i.media_type) for i in getattr(text, "images", [])]
        return CallToolResult(content=content)

    server = Server("cwagent", version=__version__, title="货币战争 agent", instructions=INSTRUCTIONS,
                    on_list_tools=list_tools, on_call_tool=call_tool)
    # 无状态:serve 重启后 agent 不用重新握手;Host 检查由 HostGuard 做
    manager = StreamableHTTPSessionManager(
        app=server, stateless=True,
        security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    return HostGuard(StreamableHTTPASGIApp(manager)), manager.run
