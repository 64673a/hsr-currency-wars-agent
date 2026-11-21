"""外部 agent:信箱(llm/external.py)和 MCP 端点(server/mcp.py)。"""

import asyncio
import json
import socket
import threading
import time

import pytest
import uvicorn

from cwagent.config import ConfigStore, ModelEndpoint
from cwagent.llm import LLMError, Message, complete
from cwagent.llm.base import ToolCall
from cwagent.llm.external import BRIDGE, EXTERNAL, UNNAMED, AgentBook, ExternalBridge, agent_name, new_content
from cwagent.memory import MemoryStore
from cwagent.server import create_app
from cwagent.brain.screen_agent import ACT_TOOL

ACT = {"analysis": "买核心", "actions": [{"type": "buy", "slot": 2}, {"type": "ready"}]}


def _answer_when_ready(bridge: ExternalBridge, reply: dict, got: list) -> threading.Thread:
    def run():
        turn = bridge.take(5)
        got.append(turn)
        bridge.answer(reply)

    t = threading.Thread(target=run)
    t.start()
    return t


def test_new_content():
    first = [Message(role="user", content="开场")]
    assert new_content(first) == ("开场", True)
    later = first + [Message(role="assistant", tool_calls=[ToolCall(id="a", name="act")]),
                     Message(role="tool", tool_call_id="a", content="执行结果 + 新界面")]
    assert new_content(later) == ("执行结果 + 新界面", False)


def test_round_trip():
    bridge = ExternalBridge()
    bridge.begin(threading.Event())
    got: list = []
    t = _answer_when_ready(bridge, ACT, got)
    resp = bridge.complete("系统", [Message(role="user", content="局面")], [ACT_TOOL])
    t.join()
    assert got[0].text == "局面" and got[0].opening and got[0].seq == 1
    [call] = resp.tool_calls
    assert call.name == "act" and call.arguments == ACT and call.id == "ext-1"
    assert bridge.system == "系统"
    assert not bridge.answer(ACT)   # 已经回答过了
    bridge.end("settlement")
    assert bridge.take(0.1) is None and bridge.status()["ended"] == "settlement"


def test_stop_and_idle():
    bridge = ExternalBridge(idle_seconds=0.5)
    stop = threading.Event()
    bridge.begin(stop)
    resp = bridge.complete("s", [Message(role="user", content="x")], [ACT_TOOL])   # 没有 agent 来取
    assert resp.tool_calls[0].arguments["actions"][0]["type"] == "pause"
    stop.set()
    with pytest.raises(LLMError):
        bridge.complete("s", [Message(role="user", content="x")], [ACT_TOOL])


def test_complete_dispatch():
    BRIDGE.begin(threading.Event())
    got: list = []
    t = _answer_when_ready(BRIDGE, ACT, got)
    try:
        resp = complete(ModelEndpoint(name=EXTERNAL, format="external"), "s", "局面", [ACT_TOOL])
    finally:
        t.join()
        BRIDGE.end("stopped")
    assert resp.tool_calls[0].arguments == ACT


# ---------------------------------------------------------------- MCP 端点

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    BRIDGE.system = ""   # 别的测试留下的
    app = create_app(config_store=ConfigStore(tmp_path / "c.yaml"), knowledge_path=tmp_path / "none.sqlite",
                     templates_dir=tmp_path / "tpl", web_dist=tmp_path / "dist", runs_dir=tmp_path / "runs",
                     memory_store=MemoryStore(tmp_path / "m.yaml", tmp_path / "m.sqlite"), device_factory=None,
                     agents_file=tmp_path / "agents.json")
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield port
    srv.should_exit = True
    thread.join(5)


def test_mcp_tools(server):
    from mcp import Client

    url = f"http://127.0.0.1:{server}/mcp?wait=2"

    async def main():
        async with Client(url) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert {"rules", "start_run", "wait", "act", "lookup", "status", "stop_run"} <= names
            rules = (await client.call_tool("rules")).content[0].text
            assert "货币战争" in rules and "{guides}" not in rules and "{memories}" not in rules
            idle = await client.call_tool("lookup", {"what": "guides", "query": "刃"})   # 没有对局时查不了
            assert idle.is_error and "没有在进行的对局" in idle.content[0].text
            # 没有进行中的对局(前面的测试可能留下了「对局结束」)
            assert "start_run" in (await client.call_tool("wait")).content[0].text
            bad = await client.call_tool("act", ACT)
            assert bad.is_error

            # 对局线程:交出一个局面,等回答
            BRIDGE.begin(threading.Event())
            result: list = []
            worker = threading.Thread(target=lambda: result.append(
                complete(ModelEndpoint(name=EXTERNAL, format="external"), "规则", "备战局面", [ACT_TOOL])))
            worker.start()
            first = (await client.call_tool("wait")).content[0].text
            assert "新回合" in first and "备战局面" in first
            nxt = (await client.call_tool("act", ACT)).content[0].text   # 回答后没有下一个局面,等 2 秒返回
            worker.join(5)
            assert result[0].tool_calls[0].arguments == ACT
            assert "还在等待" in nxt
            BRIDGE.end("settlement")
            assert "对局结束" in (await client.call_tool("wait")).content[0].text

    asyncio.run(main())


def test_mcp_agent_param_and_settings(server):
    """连接地址里的 agent 参数区分各个 agent;网页上给它填的模型写进它下的那一局。"""
    import httpx
    from mcp import Client

    api = f"http://127.0.0.1:{server}/api/external-agents"

    async def main():
        async with Client(f"http://127.0.0.1:{server}/mcp?wait=2&agent=codex") as client:
            await client.list_tools()   # agent 工具启动时就列工具,还没下棋就出现在网页上
            assert [a["name"] for a in httpx.get(api).json()] == ["codex"]
            r = httpx.put(f"{api}/codex", json={"model": " gpt-6.1 ", "effort": "xhigh"})
            assert r.status_code == 200 and r.json()["model"] == "gpt-6.1"
            assert httpx.put(f"{api}/nobody", json={"model": "x"}).status_code == 404

            BRIDGE.begin(threading.Event())
            result: list = []
            worker = threading.Thread(target=lambda: result.append(
                complete(ModelEndpoint(name=EXTERNAL, format="external"), "规则", "局面", [ACT_TOOL])))
            worker.start()
            try:
                await client.call_tool("wait")
                assert BRIDGE.agent == "codex"
                await client.call_tool("act", ACT)
                worker.join(5)
                assert result[0].model == "gpt-6.1"   # 每一步的记录里是填的模型,不是「外部 agent」
            finally:
                BRIDGE.end("stopped")
        async with Client(f"http://127.0.0.1:{server}/mcp?wait=1") as client:   # 自己写的配置没带参数
            await client.call_tool("status")
        assert {a["name"] for a in httpx.get(api).json()} == {"codex", UNNAMED}

    asyncio.run(main())


def test_agent_book(tmp_path):
    assert agent_name("codex") == "codex"
    assert agent_name(None) == agent_name("  ") == UNNAMED
    assert agent_name(" my agent/1 ") == "my-agent-1"
    book = AgentBook(tmp_path / "agents.json")
    with pytest.raises(KeyError):
        book.set("codex", "gpt-6.1", "high")   # 没连上来过
    book.seen("codex")
    book.set("codex", "gpt-6.1", "high")
    again = AgentBook(tmp_path / "agents.json").get("codex")
    assert (again["model"], again["effort"]) == ("gpt-6.1", "high") and again["last_seen"]


def test_bound_agent_settings_go_into_the_run_meta(tmp_path):
    """正在下的 agent:模型和思考强度写进 meta.json,网页上改了也跟着改;新对局重新认 agent。"""
    from cwagent.server.runs import PlayController, RunStore

    book = AgentBook(tmp_path / "agents.json")
    book.seen("claude-code")
    book.set("claude-code", "claude-opus-5-5", "xhigh")
    player = PlayController(ConfigStore(tmp_path / "c.yaml"), RunStore(tmp_path / "runs"), None, agents=book)

    class Trace:
        fields: dict = {}

        def meta(self, **fields):
            self.fields.update(fields)

    class Runner:
        trace = Trace()

    player._runner = Runner()
    player.bind_agent("claude-code")   # 没有对局时不认
    assert Runner.trace.fields == {}
    BRIDGE.begin(threading.Event())
    try:
        player.bind_agent("claude-code")
        assert Runner.trace.fields == {"agent": "claude-code", "model": "claude-opus-5-5", "effort": "xhigh"}
        book.set("claude-code", "claude-opus-5-5", "")
        player.agent_updated("claude-code")
        assert Runner.trace.fields["effort"] is None
    finally:
        BRIDGE.end("stopped")
    BRIDGE.begin(threading.Event())
    try:
        assert BRIDGE.agent == "" and BRIDGE.agent_model == ""
    finally:
        BRIDGE.end("stopped")


def test_mcp_rejects_domain_host(server):
    import httpx

    r = httpx.post(f"http://127.0.0.1:{server}/mcp", headers={"Host": "evil.example:80"}, json={})
    assert r.status_code == 421



def test_mcp_hook(server):
    """Claude Code 插件的 hook:只让正在下这一局的会话接着下。"""
    from mcp import Client

    async def main():
        async with Client(f"http://127.0.0.1:{server}/mcp?wait=1") as client:
            async def hook(event, session):
                return json.loads((await client.call_tool("hook", {"event": event, "session": session})).content[0].text)

            assert await hook("stop", "a") == {}   # 没有对局:放行
            BRIDGE.begin(threading.Event())
            try:
                assert await hook("stop", "a") == {}   # 还没有会话调用过工具
                assert await hook("tool", "a") == {}
                out = await hook("stop", "a")
                assert out["hookSpecificOutput"]["hookEventName"] == "Stop"
                assert "wait" in out["hookSpecificOutput"]["additionalContext"]
                assert await hook("stop", "b") == {}   # 别的会话不受影响
                await hook("tool", "b")                # 换了会话接手
                assert await hook("stop", "a") == {}
            finally:
                BRIDGE.end("stopped")
            assert await hook("stop", "b") == {}
            BRIDGE.begin(threading.Event())            # 新对局不认上一局的会话
            try:
                assert await hook("stop", "b") == {}
            finally:
                BRIDGE.end("stopped")

    asyncio.run(main())


def test_idle_after_external_run_ends_reports_the_end():
    """外部 agent 的对局结束后,线程还在写记录(running 仍为真):要报对局结束,不能说「不归你控制」(2026-11-19 那局结尾)。"""
    from cwagent.server.mcp import Tools

    class Player:
        def __init__(self, **st):
            self.st = {"running": True, "profile": EXTERNAL, "outcome": None, "error": None, "log": [],
                       "agent": {"active": False, "ended": None}} | st

        def status(self):
            return self.st

    ended = Player(outcome="通关", agent={"active": False, "ended": "settlement"})
    assert Tools(ended)._idle().startswith("【对局结束】通关,打完了")
    assert "收尾" in Tools(Player())._idle()                                     # 结束标记还没到
    assert "不归你控制" in Tools(Player(profile="deepseek-flash"))._idle()       # 别的档案在下


def test_long_wait_reports_progress_until_the_turn_arrives(monkeypatch):
    """wait 分段等到下一个局面,段与段之间报进度;有调用在等时 Stop hook 不催模型再调 wait(报告 P1-5)。"""
    from cwagent.server import mcp

    monkeypatch.setattr(mcp, "PROGRESS_EVERY", 0.1)
    reported: list = []

    class Session:
        async def report_progress(self, progress, total=None, message=None):
            reported.append((progress, total))

    class Ctx:
        session = Session()

    class Player:
        def status(self):
            return {"running": True, "profile": EXTERNAL, "agent": BRIDGE.status()}

    tools = mcp.Tools(Player())
    BRIDGE.begin(threading.Event())
    hooks: list = []

    def game():
        time.sleep(0.2)
        hooks.append(asyncio.run(tools.hook({"event": "stop", "session": "s"}, 1)))   # 这时 wait 还在等
        BRIDGE.complete("s", [Message(role="user", content="新局面")], [ACT_TOOL])

    async def main():
        token = mcp._CTX.set(Ctx())
        try:
            return await tools._take(30)
        finally:
            mcp._CTX.reset(token)

    BRIDGE.claim("s")
    t = threading.Thread(target=game)
    t.start()
    try:
        text = asyncio.run(main())
        assert "新局面" in str(text) and reported and reported[0][1] == 30
        assert hooks == ["{}"] and tools.waiting == 0
    finally:
        BRIDGE.answer(ACT)
        t.join()
        BRIDGE.end("stopped")
