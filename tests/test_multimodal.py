import asyncio
import base64
import json
import threading
from pathlib import Path

import httpx
import pytest
from PIL import Image

from cwagent.brain.ledger import RunLedger
from cwagent.brain.screen_agent import ActInput, Action, Episode, ScreenAgent
from cwagent.config import ModelEndpoint
from cwagent.knowledge.index import Entity
from cwagent.llm import ImageInput, Message, complete
from cwagent.llm.anthropic_format import _messages
from cwagent.llm.client import _json_mode_messages, check_profile
from cwagent.llm.external import BRIDGE, ExternalBridge
from cwagent.orchestrator.loop import Runner, Trace
from cwagent.server.mcp import Tools
from cwagent.vision.annotate import annotate
from cwagent.vision.ocr import TextBox
from tests.test_external import server
from tests.test_loop import FakeBrain, FakeOcr, Sim


def picture():
    return ImageInput.from_pil(Image.new("RGB", (320, 180), "white"))


def ep(n=1):
    return Episode(id=n, key=("prepare", f"1-{n}"), screen="prepare", node=f"1-{n}", role="tactical", max_turns=5)


def test_entities_reach_first_prompt_and_next_episode():
    class Index:
        def link(self, texts):
            return [Entity("roles", "甲", "前台技能原文")]
    brain = ScreenAgent(ModelEndpoint(name="m", format="openai"), Index(), [], "")
    book = RunLedger()
    first, _ = brain.opening(ep(), [], ledger=book, names=["甲"])
    second, _ = brain.opening(ep(2), [], ledger=book, names=["甲"])
    assert "前台技能原文" in first and "前台技能原文" in second
    assert len(book.entries) == 1


def test_external_opening_is_delta_and_snapshot_is_complete():
    class Index:
        def link(self, texts):
            return []
    brain = ScreenAgent(ModelEndpoint(name="e", format="external"), Index(), [], "")
    book = RunLedger()
    book.add_fact("投资环境甲")
    e1 = ep()
    first, _ = brain.opening(e1, [], ledger=book)
    book.add("节点1完成")
    e2 = ep(2)
    second, _ = brain.opening(e2, [], ledger=book)
    assert "投资环境甲" in first and "投资环境甲" not in second
    assert "节点1完成" in second and "投资环境甲" in e2.snapshot


@pytest.mark.parametrize("fmt,mode", [("openai", "native"), ("anthropic", "native"), ("openai", "json")])
def test_http_adapters_keep_images_and_tool_results(fmt, mode):
    image = picture()
    seen = []
    def handle(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]} if fmt == "anthropic" else
                              {"choices": [{"message": {"content": "ok"}}]})
    messages = [Message(role="user", content="开场", images=[image]),
                Message(role="assistant", raw=({"role": "assistant", "tool_calls": []} if fmt == "openai" else [])),
                Message(role="tool", content="执行结果", tool_call_id="t", images=[image])]
    endpoint = ModelEndpoint(name="m", format=fmt, model="m", base_url="https://test/v1", tool_mode=mode)
    complete(endpoint, "s", messages, http=httpx.Client(transport=httpx.MockTransport(handle)))
    payload = seen[0]
    assert json.dumps(payload).count(image.data) == 2
    if fmt == "openai":
        assert payload["messages"][-2]["role"] == "tool"
        assert payload["messages"][-1]["role"] == "user"
    else:
        assert payload["messages"][-1]["content"][0]["content"][1]["type"] == "image"


def test_json_fallback_keeps_both_images_and_cache():
    from cwagent.brain.screen_agent import ACT_TOOL
    image = picture()
    msgs = _json_mode_messages([Message(role="user", content="开场", images=[image], cache_at=2),
                               Message(role="tool", content="结果", images=[image])], [ACT_TOOL])
    assert all(m.images == [image] for m in msgs) and msgs[0].cache_at == 2


def test_read_image_check_fails_when_model_ignores_image(monkeypatch):
    monkeypatch.setattr("cwagent.llm.client.secrets.choice", lambda _: "3")
    def handle(req):
        body = json.loads(req.content)
        assert "333333" not in body["messages"][0]["content"][0]["text"]
        assert body["messages"][0]["content"][1]["type"] == "image"
        return httpx.Response(200, json={"content": [{"type": "tool_use", "name": "report_status", "id": "t",
                                                     "input": {"status": "ok", "digits": "111111"}}]})
    result = check_profile(ModelEndpoint(name="m", format="anthropic", base_url="https://test"),
                           http=httpx.Client(transport=httpx.MockTransport(handle)))
    assert not result["ok"] and not result["vision"]


def test_marks_use_original_coordinates_and_zoom_is_read_only(tmp_path):
    sim = Sim({"p": [("确认", 0.56, 0.91)]}, "p", lambda *a: None)
    runner = Runner(sim, FakeBrain({}), Trace(tmp_path / "run"), ocr=FakeOcr(sim), settle=False)
    e = ep()
    record = {}
    image = Image.new("RGB", (1920, 1080), "white")
    boxes = [TextBox("确认", 0.56, 0.91, 0.1, 0.05, 0.99)]
    runner._set_vision(e, 1, "other", image, boxes, record)
    assert e.images[0].width == 1280 and "界面文字(坐标" not in e.annotation
    runner._raw(Action(type="click", mark=1), boxes)
    assert sim.actions == [("click", 0.56, 0.91)]
    sim.actions.clear()
    runner._raw(Action(type="zoom", region=(0.25, 0.25, 0.5, 0.5)), boxes)
    runner._set_vision(e, 2, "other", image, boxes, record)
    assert not sim.actions and len(e.images) == 2
    assert (e.images[1].width, e.images[1].height) == (960, 540)
    assert all((runner.trace.root / p).exists() for p in record["vision"])
    runner.trace.close()


def test_mark_is_first_action_and_region_stays_inside_client():
    with pytest.raises(ValueError):
        ActInput.model_validate({"analysis": "", "actions": [{"type": "wait"}, {"type": "click", "mark": 1}]})
    with pytest.raises(ValueError):
        Action(type="zoom", region=(0.9, 0, 0.2, 1))


def test_bridge_rejects_stale_reply_and_retains_snapshot():
    bridge = ExternalBridge()
    bridge.begin(threading.Event())
    results = []
    image = picture()
    t = threading.Thread(target=lambda: results.append(bridge.complete("s", [Message(role="user", content="增量",
                                                images=[image], snapshot="完整档案")], [])))
    t.start()
    turn = bridge.take(2)
    assert turn.images == [image] and bridge.snapshot().text == "完整档案"
    assert not bridge.answer({}, turn=turn.seq - 1)
    assert bridge.answer({"actions": []}, turn=turn.seq)
    t.join(2)
    bridge.end("stopped")
    assert len(results) == 1


def test_mcp_native_image_blocks_and_no_game_start(server):
    from mcp import Client
    async def main():
        async with Client(f"http://127.0.0.1:{server}/mcp?wait=1") as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert {"snapshot", "observe"} <= names
            observed = await client.call_tool("observe")
            assert observed.is_error
            BRIDGE.begin(threading.Event())
            t = threading.Thread(target=lambda: BRIDGE.complete("s", [Message(role="user", content="局面",
                                           images=[picture()], snapshot="完整")], []))
            t.start()
            turn = await client.call_tool("wait")
            assert [b.type for b in turn.content] == ["text", "image"]
            snap = await client.call_tool("snapshot")
            assert "完整" in snap.content[0].text and snap.content[1].mime_type == "image/jpeg"
            BRIDGE.answer({"analysis": "结束", "actions": [{"type": "pause"}]})
            t.join(2)
            BRIDGE.end("stopped")
    asyncio.run(main())


def test_actual_request_roundtrip_keeps_tool_roles_and_image_bytes(tmp_path):
    from cwagent.llm import ToolCall
    from cwagent.replay.runner import load_request
    trace = Trace(tmp_path / 'run')
    image = picture()
    messages = [Message(role='user', content='开场', images=[image]),
                Message(role='assistant', tool_calls=[ToolCall(id='c', name='act', arguments={})]),
                Message(role='tool', tool_call_id='c', content='执行结果', images=[image])]
    name = trace.request(1, '系统规则', messages)
    system, restored = load_request(trace.root, name)
    assert system == '系统规则'
    assert [m.model_dump() for m in messages] == [m.model_dump() for m in restored]
    assert len(list((trace.root / 'vision').glob('*.jpg'))) == 1
    trace.close()


def test_observe_does_not_start_game_or_send_input(monkeypatch):
    from types import SimpleNamespace
    from cwagent.capture import Frame
    closed = []
    class ReadOnly:
        def grab(self):
            return Frame(image=Image.new('RGB', (320, 180), 'white'), timestamp=1)
        def close(self):
            closed.append(True)
    player = SimpleNamespace(running=False, device_factory=ReadOnly)
    monkeypatch.setattr('cwagent.vision.ocr.Ocr', lambda: lambda image: [])
    result = asyncio.run(Tools(player).observe({}, 1))
    assert result.images and '只读截图' in result and closed == [True]


def test_openai_multiple_tool_replies_precede_next_user_image():
    from cwagent.llm.openai_format import build_request
    endpoint = ModelEndpoint(name='m', format='openai', base_url='https://test')
    _, _, body = build_request(endpoint, 's', [Message(role='tool', tool_call_id='a', content='a', images=[picture()]),
                                              Message(role='tool', tool_call_id='b', content='未执行')], [])
    assert [m['role'] for m in body['messages']] == ['system', 'tool', 'tool', 'user']
