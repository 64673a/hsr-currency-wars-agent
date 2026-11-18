import json
import sqlite3

import pytest

from cwagent.brain.screen_agent import Episode
from cwagent.knowledge.guides import GuideAdvisor
from cwagent.orchestrator.loop import Runner, Trace

from .test_loop import FakeBrain


def guide(gid, title, carries, final, *, uses=0, envs=()):
    return {"id": gid, "title": title, "carries": carries, "use_count": uses, "environments": list(envs),
            "final_board": {"front": [{"name": n} for n in final[:2]], "back": [{"name": n} for n in final[2:]]},
            "key_traits": [{"name": "反击"}], "equip_order": {}, "example_boards": {}}


@pytest.fixture
def advisor(tmp_path):
    db = tmp_path / "k.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE guides (id TEXT, data TEXT)")
    for g in (guide("a", "景元攻略", ["景元"], ["景元", "白露", "符玄", "停云"], uses=900),
              guide("b", "黑天鹅攻略", ["黑天鹅"], ["黑天鹅", "卡芙卡", "阮•梅", "藿藿"], uses=500),
              guide("c", "刃攻略", ["刃"], ["刃", "白露", "符玄", "罗刹"], uses=100, envs=["人才储备"])):
        con.execute("INSERT INTO guides VALUES (?, ?)", (g["id"], json.dumps(g, ensure_ascii=False)))
    con.commit()
    con.close()
    return GuideAdvisor(db)


def make_runner(tmp_path, advisor):
    return Runner(None, FakeBrain({}), Trace(tmp_path / "run"), ocr=object(), log=lambda m: None, settle=False,
                  guides=advisor)


def test_codes_and_search(advisor):
    """攻略按使用次数编号(G1、G2……);检索按对上的词排,主 C 对上比阵容里有分高。"""
    assert [g.code for g in advisor.guides] == ["G1", "G2", "G3"] and advisor.find("g3").id == "c"
    assert [g.id for g in advisor.search("刃")] == ["c"]
    assert [g.id for g in advisor.search("白露 符玄")] == ["a", "c"]          # 一样多时按使用次数
    assert [g.id for g in advisor.search("刃 白露")][0] == "c"
    assert [g.id for g in advisor.search("人才储备")] == ["c"] and advisor.search("") == []
    from cwagent.knowledge.guides import brief
    text = brief(advisor.find("c"), ["白露", "刃", "银狼"])
    assert text.startswith("G3「刃攻略」主 C:刃") and "适合投资环境:人才储备" in text
    assert "和你现在场上、备战席重合:白露、刃" in text


def test_lookup_and_pin(tmp_path, advisor):
    """模型用 lookup 工具查攻略和全文,在 guide 字段挂上;程序不再替模型挑方向(维护者 2026-11-18)。查过的都记下来。"""
    r = make_runner(tmp_path, advisor)
    r._roles = ["白露"]
    out = r.lookup("guides", "刃")
    assert out.startswith("查攻略:刃:找到 1 篇") and "G3「刃攻略」" in out and "重合:白露" in out
    assert "最终阵容" in r.lookup("guide", "G3")
    assert "没有这篇攻略" in r.lookup("guide", "G9")
    assert "没有经验库" in r.lookup("memory", "扑满")          # 假的 brain 没有经验
    assert [x["query"] for x in r._lookups] == ["刃", "G3", "G9", "扑满"]
    assert r._guide is None and "挂上的攻略" not in r.ledger.render()
    r.choose_guide("G3", 1)
    assert r._guide.id == "c" and "G3「刃攻略」" in r.ledger.render()
    r.choose_guide("无", 2)
    assert r._guide is None and "挂上的攻略" not in r.ledger.render()
    off = make_runner(tmp_path, None)
    assert "攻略没有开启" in off.lookup("guides", "刃")


def test_lookup_is_its_own_tool(monkeypatch):
    """查资料是和 act 并列的工具(放在 act 里时模型一次都没用过):查完结果直接交回,模型接着回答,
    这几轮不算无效输出;一轮最多查 LOOKUPS 次,查满了还在查就按一次无效输出算。"""
    from cwagent.brain import screen_agent
    from cwagent.brain.screen_agent import ACT_TOOL, LOOKUPS, ScreenAgent
    from cwagent.llm import LLMResponse, ToolCall

    from .test_episode import FakeIndex, endpoint

    assert "lookup" not in ACT_TOOL.parameters["properties"]["actions"]["items"]["properties"]["type"]["enum"]
    sent, deltas = [], []
    answers = [LLMResponse(tool_calls=[ToolCall(id="l1", name="lookup", arguments={"what": "guides", "query": "刃"})]),
               LLMResponse(tool_calls=[ToolCall(id="a1", name="act", arguments={
                   "analysis": "看过攻略", "alternatives": [], "actions": [{"type": "click_text", "text": "确认", "reason": "x"}]})])]

    def fake(ep, system, messages, tools, **kw):
        sent.append(([t.name for t in tools], [m.role for m in messages]))
        return answers.pop(0)

    monkeypatch.setattr(screen_agent, "complete", fake)
    a = ScreenAgent(endpoint(), FakeIndex(), [], "规则")
    a.lookup_handler = lambda what, query: f"查攻略:{query}:找到 1 篇"
    ep = Episode(id=1, key=("environment_choice", None), screen="environment_choice", node=None, role="strategic",
                 max_turns=3)
    d = a.decide(ep, "开场", on_delta=lambda k, t: deltas.append((k, t)))
    assert d.error == "" and d.actions[0].text == "确认" and d.turn == 1
    assert sent[0][0] == ["act", "lookup"] and sent[1][1] == ["user", "assistant", "tool"]
    assert ep.messages[2].content == "查攻略:刃:找到 1 篇" and ("lookup", "查攻略:刃") in deltas

    # 一直查:查满以后按无效输出算,不会无限查下去
    looping = [LLMResponse(tool_calls=[ToolCall(id=f"l{i}", name="lookup", arguments={"what": "memory", "query": "x"})])
               for i in range(LOOKUPS + 3)]
    monkeypatch.setattr(screen_agent, "complete", lambda *x, **k: looping.pop(0))
    ep2 = Episode(id=2, key=("prepare", "1-1"), screen="prepare", node="1-1", role="tactical", max_turns=3)
    d2 = a.decide(ep2, "开场")
    assert d2.error and not d2.actions and f"已经 {LOOKUPS} 次" in ep2.messages[-1].content


def test_environment_recorded_from_choice(tmp_path, advisor):
    r = make_runner(tmp_path, advisor)
    r.episode = Episode(id=1, key=("environment_choice", None), screen="environment_choice", node=None,
                        role="strategic", max_turns=3)
    r.episode.done = ["点击「二手市场」", "点击「人才储备」", "点击「确认」"]
    r.close_episode()
    assert r.ledger.environment == "人才储备"




def test_report_lists_lookups(tmp_path):
    from cwagent.replay import report

    run = tmp_path / "run"
    run.mkdir()
    rows = [{"step": 1, "screen": "prepare", "node": "2-1",
             "decision": {"lookups": [{"what": "guides", "query": "克拉拉 刃", "result": "…"}], "actions": [{"type": "buy"}]}},
            {"step": 2, "screen": "prepare", "node": "2-1", "decision": {"guide": "G11", "actions": [{"type": "ready"}]}}]
    (run / "steps.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    text = report.render(run)
    assert "## 查资料(1 次)" in text and "- 2-1 查攻略:克拉拉 刃" in text and "- 2-1 挂攻略:G11" in text
