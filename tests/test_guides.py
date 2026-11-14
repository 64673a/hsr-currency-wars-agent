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


def test_rank_prefers_owned_carry(advisor):
    top = advisor.rank(["刃", "白露"], [], None)
    assert top[0].guide.id == "c"
    assert "主 C 刃 已经有了" in top[0].why()
    assert [m.guide.id for m in advisor.shop_carries([], ["黑天鹅", "白露"])] == ["b"]


def test_initial_direction_then_auto_switch(tmp_path, advisor):
    r = make_runner(tmp_path, advisor)
    text = r.guide_advice(True, {})
    assert r._guide.id == "a"                     # 什么都没有时按热门程度定
    assert "景元攻略" in r.ledger.render()
    assert "当前方向「景元攻略」" in text
    # 主 C 一个都没有时,别的攻略明显更合适就自动换
    r._roles = ["黑天鹅", "卡芙卡"]
    r.guide_advice(False, {})
    assert r._guide.id == "b"


def test_shop_carry_is_only_suggested_once_committed(tmp_path, advisor):
    r = make_runner(tmp_path, advisor)
    r._roles = ["景元", "白露"]
    r.guide_advice(True, {})
    assert r._guide.id == "a"
    r._shop_names = ["刃"]
    record: dict = {}
    text = r.guide_advice(False, {})
    assert r._guide.id == "a"                     # 已经有主 C:只提示
    assert "商店里有 刃,是候选" in text and "「刃攻略」的主 C" in text
    assert r.guide_advice(False, record) == ""    # 候选没变就不重复
    n = next(i for i, g in enumerate(r._candidates, start=1) if g.id == "c")
    r.choose_guide(f"候选{n}", 1)
    assert r._guide.id == "c" and r._guide_pinned
    assert "刃攻略" in r.ledger.render()


def test_environment_recorded_from_choice(tmp_path, advisor):
    r = make_runner(tmp_path, advisor)
    r.episode = Episode(id=1, key=("environment_choice", None), screen="environment_choice", node=None,
                        role="strategic", max_turns=3)
    r.episode.done = ["点击「二手市场」", "点击「人才储备」", "点击「确认」"]
    r.close_episode()
    assert r.ledger.environment == "人才储备"


def test_environment_advice_lists_guides(tmp_path, advisor):
    from cwagent.vision.ocr import TextBox

    r = make_runner(tmp_path, advisor)
    boxes = [TextBox("人才储备", 0.5, 0.3, 0.1, 0.03, 0.9), TextBox("二手市场", 0.2, 0.3, 0.1, 0.03, 0.9)]
    text = r.environment_advice(boxes)
    assert "人才储备:候选 1「刃攻略」主 C 刃" in text and "二手市场" not in text
    r.choose_guide("1", 1)
    assert r._guide.id == "c" and r._guide_pinned
    r.guide_advice(True, {})                       # 第一个备战阶段不再自动换
    assert r._guide.id == "c"
