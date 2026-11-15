"""外部 agent:信箱(llm/external.py)和 MCP 端点(server/mcp.py)。"""

import asyncio
import socket
import threading
import time

import pytest
import uvicorn

from cwagent.config import ConfigStore, ModelEndpoint
from cwagent.llm import LLMError, Message, complete
from cwagent.llm.base import ToolCall
from cwagent.llm.external import BRIDGE, EXTERNAL, ExternalBridge, new_content
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
                     memory_store=MemoryStore(tmp_path / "m.yaml", tmp_path / "m.sqlite"), device_factory=None)
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
            assert {"rules", "start_run", "wait", "act", "status", "stop_run"} <= names
            assert "货币战争" in (await client.call_tool("rules")).content[0].text
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


def test_mcp_rejects_domain_host(server):
    import httpx

    r = httpx.post(f"http://127.0.0.1:{server}/mcp", headers={"Host": "evil.example:80"}, json={})
    assert r.status_code == 421
