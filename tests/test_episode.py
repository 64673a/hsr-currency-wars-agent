import pytest

from cwagent.brain import screen_agent
from cwagent.brain.ledger import MAX_NODES, RunLedger
from cwagent.brain.screen_agent import Episode, ScreenAgent
from cwagent.config import ModelEndpoint, ModelProfile, ProviderConfig
from cwagent.llm import LLMError, LLMResponse, Message, ToolCall
from cwagent.llm import anthropic_format, openai_format
from cwagent.llm.client import _json_mode_messages
from cwagent.vision.ocr import TextBox
from cwagent.vision.shop import read_shop


class FakeIndex:
    def link(self, texts):
        return []


def endpoint(fmt="openai"):
    return ModelEndpoint.of(ProviderConfig(name="p", format=fmt, base_url="https://x/v1", api_key="k"),
                            ModelProfile(name="m", provider="p", model="m"))


def agent():
    return ScreenAgent(endpoint(), FakeIndex(), [], "规则")


def reply(args=None, *, call_id="c1", raw=None):
    calls = [ToolCall(id=call_id, name="act", arguments=args)] if args is not None else []
    return LLMResponse(text="", tool_calls=calls, message_raw=raw or {"role": "assistant", "reasoning_content": "想"})


def test_episode_multiturn_and_retry(monkeypatch):
    sent = []
    answers = [reply(None),                                                   # 没调用工具:要求重来
               reply({"analysis": "选一", "actions": [{"type": "click_text", "text": "其一"}]}),
               reply({"analysis": "确认", "actions": [{"type": "click_text", "text": "确认"}]}, call_id="c2")]

    def fake_complete(ep, system, messages, tools):
        sent.append([m.role for m in messages])
        return answers.pop(0)

    monkeypatch.setattr(screen_agent, "complete", fake_complete)
    a = agent()
    ep = Episode(id=1, key=("encounter_choice", "1-7"), screen="encounter_choice", node="1-7", role="chooser",
                 max_turns=4)
    d1 = a.decide(ep, "开场")
    assert d1.error == "" and d1.actions[0].text == "其一" and ep.pending == ["c1"] and d1.turn == 1
    assert sent == [["user"], ["user", "assistant", "user"]]
    d2 = a.decide(ep, "执行结果 + 新界面")
    assert d2.actions[0].text == "确认" and d2.turn == 2
    roles = [m.role for m in ep.messages]
    assert roles == ["user", "assistant", "user", "assistant", "tool", "assistant"]
    assert ep.messages[4].tool_call_id == "c1" and ep.messages[4].content == "执行结果 + 新界面"
    assert ep.messages[3].raw["reasoning_content"] == "想"                     # 原样保留,下一轮传回


def test_prepare_actions_rejected_elsewhere_and_error_restores(monkeypatch):
    answers = [reply({"analysis": "买", "actions": [{"type": "buy", "slot": 1}]}),
               reply({"analysis": "买", "actions": [{"type": "buy", "slot": 1}]}, call_id="c2")]
    monkeypatch.setattr(screen_agent, "complete", lambda *a: answers.pop(0))
    ep = Episode(id=1, key=("other",), screen="other", node=None, role="navigator", max_turns=4)
    d = agent().decide(ep, "开场")
    assert "只能在备战界面使用" in d.error and ep.pending == []

    def fail(*a):
        raise LLMError("超时")

    monkeypatch.setattr(screen_agent, "complete", fail)
    before = list(ep.messages)
    d = agent().decide(ep, "再问")
    assert d.error == "超时" and ep.messages == before and ep.turns == 1          # 这一轮作废,对话不变


def test_openai_messages_pass_reasoning_back():
    msgs = [Message(role="user", content="开场"),
            Message(role="assistant", raw={"role": "assistant", "content": "", "reasoning_content": "想",
                                           "tool_calls": [{"id": "c1", "type": "function",
                                                           "function": {"name": "act", "arguments": "{}"}}]}),
            Message(role="tool", tool_call_id="c1", content="结果")]
    _, _, body = openai_format.build_request(endpoint(), "系统", msgs, None)
    assert [m["role"] for m in body["messages"]] == ["system", "user", "assistant", "tool"]
    assert body["messages"][2]["reasoning_content"] == "想" and body["messages"][3]["tool_call_id"] == "c1"


def test_anthropic_messages_merge_tool_results_and_cache():
    msgs = [Message(role="user", content="开场"),
            Message(role="assistant", raw=[{"type": "thinking", "thinking": "想", "signature": "s"},
                                           {"type": "tool_use", "id": "t1", "name": "act", "input": {}}]),
            Message(role="tool", tool_call_id="t1", content="结果")]
    _, _, body = anthropic_format.build_request(endpoint("anthropic"), "系统", msgs, None)
    m = body["messages"]
    assert [x["role"] for x in m] == ["user", "assistant", "user"]
    assert m[1]["content"][0]["signature"] == "s"                                 # thinking 块原样传回
    assert m[2]["content"][0]["type"] == "tool_result" and m[2]["content"][0]["tool_use_id"] == "t1"
    assert m[2]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in m[0]["content"][0]


def test_json_mode_messages():
    from cwagent.brain.screen_agent import ACT_TOOL

    out = _json_mode_messages([Message(role="user", content="开场"), Message(role="assistant", content="{...}"),
                               Message(role="tool", tool_call_id="json-0", content="结果")], [ACT_TOOL])
    assert [m.role for m in out] == ["user", "assistant", "user"]
    assert "输出格式" in out[0].content and out[2].content.startswith("工具执行结果")


def test_ledger_bounded():
    led = RunLedger(difficulty="A8-1", companion="菇菇兔")
    for i in range(30):
        led.node(f"1-{i}").actions = "买"
        led.event(f"选择 {i}")
    text = led.render()
    assert text.count("\n- 1-") == MAX_NODES and "1-29" in text and "1-0:" not in text
    assert "选择 29" in text and "选择 0\n" not in text
    led.set_direction("x" * 2000)
    assert len(led.direction) == 800


def test_read_shop():
    boxes = [TextBox("银狼", 0.22, 0.28, 0.05, 0.02, 0.9), TextBox("3", 0.30, 0.28, 0.02, 0.02, 0.9),
             TextBox("黄泉", 0.50, 0.28, 0.05, 0.02, 0.9), TextBox("5", 0.58, 0.28, 0.02, 0.02, 0.9)]
    cards = read_shop(boxes)
    assert cards[0] == (1, "银狼", 3) and cards[1] == (2, None, None) and cards[2] == (3, "黄泉", 5)


def test_new_screens():
    from cwagent.orchestrator.screens import classify, role_of

    def boxes(*texts):
        return [TextBox(t, 0.5, 0.1 * i, 0.1, 0.03, 0.9) for i, t in enumerate(texts)]

    assert classify(boxes("挑战成功", "获得金币总览", "继续挑战")) == "node_result"
    assert classify(boxes("挑战结束", "1-4X", "战斗", "点击空白加速")) == "battle_end"
    assert classify(boxes("提示", "可出战角色人数未达上限，是否确认出战？", "确认")) == "deploy_confirm"
    assert classify(boxes("请选择投资策略", "团队力量•金")) == "strategy_choice"
    assert classify(boxes("返回备战界面", "遭遇节点", "遭遇其一", "奖励预览")) == "encounter_choice"
    assert role_of("prepare") == "tactical" and role_of("other") == "navigator"
