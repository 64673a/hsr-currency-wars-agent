import pytest

from cwagent.brain import screen_agent
from cwagent.brain.ledger import RunLedger
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


def test_memories_catalog_pushed_and_looked_up(tmp_path):
    """系统提示词里是经验目录(编号、类型和标题);触发条件对上的经验程序在开场附上原文(维护者 2026-11-19),
    lookup memory 按编号或关键词取原文(标题里对上的排前面)。"""
    from cwagent.memory import MemoryStore

    memories = MemoryStore(local_path=tmp_path / "memory.sqlite").public()
    a = ScreenAgent(endpoint(), FakeIndex(), memories, "规则")
    piggy = next(code for code, m in a.memory_codes.items() if m.id == "m-20261114-piggy-bank")
    assert f"- {piggy} [打法] 次元扑满主题" in a.system and "掉落超多战利品" not in a.system
    book = RunLedger(environment="经济严重过热")
    ep = Episode(id=1, key=("prepare", "1-1"), screen="prepare", node="1-1", role="tactical", max_turns=4)
    text, recalled = a.opening(ep, [], ledger=book, nodes={"reward"})
    assert any(r.startswith(f"{piggy} 次元扑满主题") for r in recalled)          # 选的投资环境触发
    assert "## 相关经验" in text and "掉落超多战利品" in text
    found_by_id = a.lookup_memory(piggy.lower())
    assert found_by_id.startswith(f"{piggy} [打法] 次元扑满主题")
    assert not any(note in found_by_id + text for note in ("来源", "已采纳", "已核实", "有依据未核实"))
    found = a.lookup_memory("扑满")
    assert found.splitlines()[0].endswith("抓次元扑满要带「基础伤害」的羁绊或击破角色,破韧能防止扑满逃跑") or \
        "次元扑满主题" in found.splitlines()[0]
    assert a.lookup_memory("不存在的词") == "没有找到和「不存在的词」相关的经验"


def reply(args=None, *, call_id="c1", raw=None):
    calls = [ToolCall(id=call_id, name="act", arguments=args)] if args is not None else []
    return LLMResponse(text="", tool_calls=calls, message_raw=raw or {"role": "assistant", "reasoning_content": "想"})


def test_episode_multiturn_and_retry(monkeypatch):
    sent = []
    answers = [reply(None),                                                   # 没调用工具:要求重来
               reply({"analysis": "选一", "actions": [{"type": "click_text", "text": "其一"}]}),
               reply({"analysis": "确认", "actions": [{"type": "click_text", "text": "确认"}]}, call_id="c2")]

    def fake_complete(ep, system, messages, tools, **kw):
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


def test_summary_and_cache_cut(monkeypatch):
    """回合小结记在回合上;第一条消息带着本局经历的长度(给 Anthropic 打缓存标记),之后的消息不带。"""
    answers = [reply({"analysis": "选左边", "actions": [{"type": "click_text", "text": "其一"}]}),
               reply({"analysis": "确认", "summary": "选了简单的遭遇,保血", "actions": [{"type": "click_text", "text": "确认"}]},
                     call_id="c2")]
    monkeypatch.setattr(screen_agent, "complete", lambda *a, **k: answers.pop(0))
    a = agent()
    ep = Episode(id=1, key=("encounter_choice", "1-7"), screen="encounter_choice", node="1-7", role="chooser",
                 max_turns=4, cache_at=5)
    a.decide(ep, "本局经历……当前界面")
    assert ep.messages[0].cache_at == 5 and ep.summary == "" and ep.analysis == "选左边"
    d = a.decide(ep, "新界面")
    assert d.summary == ep.summary == "选了简单的遭遇,保血" and ep.messages[2].cache_at == 0


def test_prepare_actions_rejected_elsewhere_and_error_restores(monkeypatch):
    answers = [reply({"analysis": "买", "actions": [{"type": "buy", "slot": 1}]}),
               reply({"analysis": "买", "actions": [{"type": "buy", "slot": 1}]}, call_id="c2")]
    monkeypatch.setattr(screen_agent, "complete", lambda *a, **k: answers.pop(0))
    ep = Episode(id=1, key=("other",), screen="other", node=None, role="navigator", max_turns=4)
    d = agent().decide(ep, "开场")
    assert "只能在备战界面使用" in d.error and ep.pending == []

    def fail(*a, **k):
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
    assert m[2]["content"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "cache_control" not in m[0]["content"][0]


def test_json_mode_messages():
    from cwagent.brain.screen_agent import ACT_TOOL

    out = _json_mode_messages([Message(role="user", content="开场"), Message(role="assistant", content="{...}"),
                               Message(role="tool", tool_call_id="json-0", content="结果")], [ACT_TOOL])
    assert [m.role for m in out] == ["user", "assistant", "user"]
    assert "输出格式" in out[0].content and out[2].content.startswith("工具执行结果")


def test_ledger_history_append_only():
    """本局经历保留整局、只往后追加:之前渲染出的部分是之后的前缀(跨回合命中缓存);当前状态放在后面。"""
    led = RunLedger(difficulty="A8-1")
    led.add("[1-1 备战] 操作:买 | 小结:先攒钱")
    before = led.render_history()
    led.guide, led.direction = "「刃攻略」", "刃 C"
    for i in range(100):
        led.add(f"[1-{i} 战斗] 进度 0% → 100%")
    led.add("x" * 2000)
    history = led.render_history()
    assert history.startswith(before) and "[1-0 战斗]" in history and "[1-99 战斗]" in history
    assert len(led.history[-1]) == 600
    text = led.render()
    # 本局经历在最前面(设计文档第 20.4 节,2026-11-18 起放在事实前面):新增事实不打断经历这段前缀
    assert text.startswith(history) and text.index("刃攻略") > len(history) and led.stable_len() == len(history)
    led.add_fact("位面 1 敌人词条:第一位面强化")
    assert led.render().startswith(history) and not led.add_fact("位面 1 敌人词条:第一位面强化")
    led.set_direction("x" * 2000)
    assert len(led.direction) == 800


def test_anthropic_cache_breakpoint_after_history():
    from cwagent.llm.anthropic_format import _messages

    out = _messages([Message(role="user", content="经历\n\n当前界面", cache_at=2)])
    blocks = out[0]["content"]
    assert blocks[0] == {"type": "text", "text": "经历", "cache_control": {"type": "ephemeral", "ttl": "1h"}}
    assert blocks[1]["text"] == "\n\n当前界面" and blocks[1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


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


def test_shop_state():
    from cwagent.vision.shop import shop_state

    def boxes(*items):
        return [TextBox(t, x, y, 0.05, 0.02, 0.9) for t, x, y in items]

    assert shop_state(boxes(("收起", 0.845, 0.908))) is True
    # 角色详情遮住了右下按钮和「刷新」,概率那一行还在
    assert shop_state(boxes(("45%", 0.518, 0.359), ("33%", 0.570, 0.359), ("20%", 0.621, 0.359), ("出售", 0.92, 0.85))) is True
    assert shop_state(boxes(("商店", 0.845, 0.909))) is False
    assert shop_state(boxes(("出售", 0.92, 0.85))) is None


def test_battle_progress_with_icon_and_banner():
    from cwagent.orchestrator.screens import classify

    assert classify([TextBox("X1-3 0%", 0.385, 0.025, 0.05, 0.02, 0.9)]) == "battle"


def test_tea_break_screens():
    from cwagent.orchestrator.screens import classify, role_of

    def boxes(*texts):
        return [TextBox(t, 0.5, 0.1 * i, 0.1, 0.03, 0.9) for i, t in enumerate(texts)]

    # 2026-11-14 实机记录里的两页
    assert classify(boxes("W1-8", "返回备战界面", "68", "幕后茶歇", "醒香猫语", "享用茶歇", "聊一聊", "摇一摇")) == "tea_break"
    assert classify(boxes("W1-8", "返回备战界面", "享用茶歇", "美餐时间", "回复20小队生命值", "返回")) == "tea_break"
    # 茶歇节点的备战界面仍是备战(右侧按钮是「幕后茶歇」)
    assert classify(boxes("备战阶段", "返回幕后茶歇", "1-8", "幕后茶歇", "商店")) == "prepare"
    assert role_of("tea_break") == "chooser"


def test_shop_state_free_refresh():
    """「免费刷新」也是合法的刷新标签(M7A 的 SHOP_REFRESH_LABELS 就是这两个)。
    原来只认「刷新」,免费刷新时判定不出来,商店开着被当成收起着,出战点打在商店面板上(2026-11-16 实机)。"""
    from cwagent.vision.shop import shop_state

    def boxes(*items):
        return [TextBox(t, x, y, 0.05, 0.02, 0.9) for t, x, y in items]

    assert shop_state(boxes(("收起", 0.845, 0.908), ("免费刷新", 0.842, 0.450))) is True
    assert shop_state(boxes(("收起", 0.845, 0.908), ("刷新", 0.842, 0.451))) is True
    assert shop_state(boxes(("免费刷新", 0.842, 0.450))) is True     # 只认到一半也算开着
    assert shop_state(boxes(("商店", 0.845, 0.909))) is False


def test_history_drops_ready_notice_but_keeps_failures(tmp_path):
    """「这一轮没有出战,出战要单独一轮」是当轮反馈,不进本局经历;没成功的操作照记(2026-11-18)。"""
    from cwagent.brain.screen_agent import Episode
    from cwagent.orchestrator.loop import Runner, Trace
    from cwagent.orchestrator.prepare import READY_DEFERRED

    from .test_loop import FakeBrain

    r = Runner(None, FakeBrain({}), Trace(tmp_path / "run"), ocr=object(), log=lambda m: None, settle=False)
    r.episode = Episode(id=1, key=("prepare", "1-2"), screen="prepare", node="1-2", role="tactical", max_turns=4)
    r.episode.done = ["买商店第 1 格(桂乃芬)", READY_DEFERRED + "出战要单独一轮:先看……再只发 ready",
                      "备战席2号 → 后台3号:上场已满(3/3)", "出战"]
    r.close_episode()
    line = r.ledger.history[-1]
    assert "这一轮没有出战" not in line and "上场已满(3/3)" in line and line.count("出战") == 1


def test_intro_text_skips_performance_overlay():
    from cwagent.orchestrator.screens import intro_text
    from cwagent.vision.ocr import TextBox

    # 2026-11-18 那局 OCR 把叠加层拆成了几段,「82 W」单独一段也要去掉
    words = [TextBox("FPS 118 Low 1% 80", 0.048, 0.009, 0.08, 0.02, 0.9),
             TextBox(") CPU 63℃ 5217 MHz", 0.141, 0.009, 0.1, 0.02, 0.9), TextBox("82 W", 0.210, 0.009, 0.03, 0.02, 0.9),
             TextBox("本场对局首领", 0.3, 0.2, 0.1, 0.03, 0.9), TextBox("下一步", 0.5, 0.9, 0.1, 0.03, 0.9)]
    assert intro_text(words) == "本场对局首领"


def test_situation_alternatives_and_reasons_are_kept(monkeypatch):
    """保留已填写的其他做法和操作理由,也接受省略其他做法和旧格式的操作。"""
    from cwagent.brain.screen_agent import ACT_TOOL, to_jsonable

    schema = ACT_TOOL.parameters
    assert list(schema["properties"])[:3] == ["analysis", "alternatives", "actions"]   # 流式时先出局势
    assert schema["required"] == ["analysis", "actions"]
    assert schema["properties"]["actions"]["items"]["required"] == ["type", "reason"]
    answers = [reply({"analysis": "血量 61,金币 41,上一场首领 66% 没打完",
                      "alternatives": ["转反击:克拉拉只有 1 星,刃要 4 金币", " "],
                      "actions": [{"type": "buy", "slot": 1, "reason": "刃和克拉拉凑 2 反击"}]}),
               reply({"analysis": "出战", "actions": [{"type": "ready"}]}, call_id="c2")]
    monkeypatch.setattr(screen_agent, "complete", lambda *a, **k: answers.pop(0))
    a = agent()
    ep = Episode(id=1, key=("prepare", "2-1"), screen="prepare", node="2-1", role="tactical", max_turns=4)
    d = a.decide(ep, "开场")
    assert d.alternatives == ["转反击:克拉拉只有 1 星,刃要 4 金币"]
    out = to_jsonable(d)
    assert out["alternatives"] == d.alternatives and out["actions"][0]["reason"] == "刃和克拉拉凑 2 反击"
    d2 = a.decide(ep, "执行结果")
    assert d2.error == "" and d2.alternatives == [] and d2.actions[0].reason == ""
