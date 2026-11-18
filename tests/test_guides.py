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
    """模型用 lookup 查攻略和全文,在 guide 字段挂上;程序不再替模型挑方向(维护者 2026-11-18)。"""
    from cwagent.brain.screen_agent import Action

    r = make_runner(tmp_path, advisor)
    r._roles = ["白露"]
    out = r.lookup(Action(type="lookup", what="guides", query="刃"))
    assert out.startswith("查攻略:刃:找到 1 篇") and "G3「刃攻略」" in out and "重合:白露" in out
    assert "最终阵容" in r.lookup(Action(type="lookup", what="guide", query="G3"))
    assert "没有这篇攻略" in r.lookup(Action(type="lookup", what="guide", query="G9"))
    assert "没有经验库" in r.lookup(Action(type="lookup", what="memory", query="扑满"))   # 假的 brain 没有经验
    assert r._guide is None and "挂上的攻略" not in r.ledger.render()
    r.choose_guide("G3", 1)
    assert r._guide.id == "c" and "G3「刃攻略」" in r.ledger.render()
    r.choose_guide("无", 2)
    assert r._guide is None and "挂上的攻略" not in r.ledger.render()
    off = make_runner(tmp_path, None)
    assert "攻略没有开启" in off.lookup(Action(type="lookup", what="guides", query="刃"))


def test_lookup_must_be_alone():
    from pydantic import ValidationError

    from cwagent.brain.screen_agent import Action, ActInput

    with pytest.raises(ValidationError, match="单独提交"):
        ActInput(analysis="x", actions=[Action(type="lookup", what="guides", query="刃"), Action(type="ready")])
    with pytest.raises(ValidationError, match="what"):
        Action(type="lookup", query="刃")


def test_environment_recorded_from_choice(tmp_path, advisor):
    r = make_runner(tmp_path, advisor)
    r.episode = Episode(id=1, key=("environment_choice", None), screen="environment_choice", node=None,
                        role="strategic", max_turns=3)
    r.episode.done = ["点击「二手市场」", "点击「人才储备」", "点击「确认」"]
    r.close_episode()
    assert r.ledger.environment == "人才储备"




def test_lookup_turn_does_not_use_a_decision(tmp_path, advisor, monkeypatch):
    """查资料的一轮像 zoom 一样不占回合的决策次数(每回合最多 LOOKUPS 轮),结果交给下一轮。"""
    from cwagent.brain.screen_agent import Action
    from cwagent.orchestrator.loop import LOOKUPS, RunOptions

    from .test_loop import FakeOcr, Sim
    import cwagent.orchestrator.loop as loop

    monkeypatch.setattr(loop, "ACTION_GAP", 0)
    monkeypatch.setattr(loop.time, "sleep", lambda s: None)
    pages = {"env": [("投资环境", 0.5, 0.09), ("追击概念股", 0.77, 0.36), ("确认", 0.56, 0.91)],
             "end": [("对局评价", 0.5, 0.3)]}
    sim = Sim(pages, "env", lambda state, a: {("env", ("click", 0.56, 0.91)): "end"}.get((state, a)))
    look = [Action(type="lookup", what="guides", query="人才储备")]
    brain = FakeBrain({"environment_choice": [look] * (LOOKUPS + 1) + [
        [Action(type="click_text", text="追击概念股"), Action(type="click_text", text="确认")]]})
    r = Runner(sim, brain, Trace(tmp_path / "run"), ocr=FakeOcr(sim), log=lambda m: None, settle=False, guides=advisor)
    r.run(RunOptions(max_steps=10))
    rows = [json.loads(line) for line in (tmp_path / "run" / "steps.jsonl").read_text(encoding="utf-8").splitlines()]
    looked = [row for row in rows if (row.get("actions") or [""])[0].startswith("查攻略")]
    assert len(looked) == LOOKUPS + 1 and "G3「刃攻略」" in looked[0]["actions"][0]
    assert brain.results[0].startswith("查攻略:人才储备:找到 1 篇")   # 结果作为执行结果交给模型
    assert any(row.get("actions") == ["点击「追击概念股」", "点击「确认」"] for row in rows)


def test_report_lists_lookups(tmp_path):
    from cwagent.replay import report

    run = tmp_path / "run"
    run.mkdir()
    rows = [{"step": 1, "screen": "prepare", "node": "2-1",
             "decision": {"actions": [{"type": "lookup", "what": "guides", "query": "克拉拉 刃"}]}},
            {"step": 2, "screen": "prepare", "node": "2-1", "decision": {"guide": "G11", "actions": [{"type": "ready"}]}}]
    (run / "steps.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    text = report.render(run)
    assert "## 查资料(1 次)" in text and "- 2-1 查攻略:克拉拉 刃" in text and "- 2-1 挂攻略:G11" in text
