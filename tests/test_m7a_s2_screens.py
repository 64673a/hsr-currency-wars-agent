"""M7A v2026.11.18 新加的 S2 界面(文字和 1920x1080 坐标照抄它的测试 tests/test_tasks/test_currency_wars_s2.py)。"""

from cwagent.orchestrator.screens import classify, invest_refresh, investment_cards, investment_hint, role_of
from cwagent.vision.ocr import TextBox


def boxes(*words):
    return [TextBox(t, x / 1920, y / 1080, 0.05, 0.03, 0.9) for t, x, y in words]


def test_new_s2_screens():
    assert classify(boxes(("机心巧匠", 1046, 79), ("请选择1个强化效果", 1046, 141), ("确认选择", 1492, 585))) == "clara_mod"
    # 备战界面左侧羁绊列表里的「机心巧匠」不算
    assert classify(boxes(("机心巧匠", 130, 331), ("备战阶段", 287, 70), ("1-2", 287, 106))) == "prepare"
    assert classify(boxes(("从师", 960, 66), ("请选择1名角色作为师父，受益于其羁绊效果", 960, 103))) == "mentor"
    # 主角光环界面上效果卡叫「从师」,不是从师页
    assert classify(boxes(("选择一个角色获得主角光环", 960, 90), ("从师", 420, 436))) == "spotlight_choice"
    assert classify(boxes(("选择装备", 1028, 40), ("请选择1个", 1028, 100), ("查看详情", 738, 800))) == "equipment_reward"
    assert classify(boxes(("备战阶段", 287, 70), ("请选择1名角色作为师父，受益于其羁绊效果", 960, 103))) == "mentor"
    assert role_of("clara_mod") == "strategic"


def test_paired_strategy_titles_refresh_and_shared_choice(tmp_path):
    from types import SimpleNamespace

    from cwagent.brain.screen_agent import Episode
    from cwagent.orchestrator.loop import Runner, Trace

    words = boxes(("请选择投资策略", 960, 100), ("选择一个", 515, 248), ("两个都带走", 1184, 248),
                  ("左侧策略", 514, 560), ("右侧策略甲", 1005, 560), ("右侧策略乙", 1409, 560),
                  ("刷新次数0", 510, 841), ("刷新次数1", 1200, 841))
    assert investment_cards(words, "strategy_choice") == 3
    groups = invest_refresh(words, "strategy_choice")["groups"]
    assert groups == [{"columns": (1,), "remaining": 0}, {"columns": (2, 3), "remaining": 1}]
    hint = investment_hint(words, "strategy_choice")
    assert "一起更换这两张" in hint and "各自独立" not in hint

    entities = {n: [SimpleNamespace(name=n, kind="augments", text=f"{n}效果")]
                for n in ["左侧策略", "右侧策略甲", "右侧策略乙"]}
    r = Runner(None, SimpleNamespace(index=SimpleNamespace(by_name=entities)), Trace(tmp_path / "run"),
               ocr=object(), log=lambda _: None, settle=False)
    r.episode = Episode(id=1, key=("strategy_choice", "1-3"), screen="strategy_choice", node="1-3",
                        role="strategic", max_turns=4)
    assert r._remember_choice(1409 / 1920, 420 / 1080, words) == "右侧策略甲、右侧策略乙"
    before = dict(r.episode.chosen)
    assert r._remember_choice(1135 / 1920, 841 / 1080, words) is None
    assert r.episode.chosen == before
    r.close_episode()
    assert "右侧策略甲、右侧策略乙" in r.ledger.facts[-1]


def test_mentor_transition_waits_for_followup_and_reports_missing_page(tmp_path, monkeypatch):
    from cwagent.brain.screen_agent import Episode
    from cwagent.orchestrator import loop

    from .test_loop import FakeBrain

    now = [100.0]
    monkeypatch.setattr(loop.time, "monotonic", lambda: now[0])
    r = loop.Runner(None, FakeBrain({}), loop.Trace(tmp_path / "run"), ocr=object(), log=lambda _: None, settle=False)
    r.episode = Episode(id=1, key=("spotlight_choice", "1-9"), screen="spotlight_choice", node="1-9",
                        role="strategic", max_turns=4, chosen={"spotlights": ("从师", "主角受益于师父的羁绊")})
    assert r._mentor_transition("prepare")[0].type == "wait"
    assert r.episode is None and "主角光环 从师" in r.ledger.facts[-1]
    assert r._mentor_transition("mentor") is None and r._mentor_until == 0
    r._mentor_until = 118
    now[0] = 120
    assert r._mentor_transition("prepare") is None and "仍未识别出师父选择页" in r._notice


def test_fixed_units_block_raw_drag_in_both_directions(tmp_path):
    from cwagent.brain.screen_agent import Action
    from cwagent.orchestrator.loop import Runner, Trace
    from cwagent.vision.board import Slot

    from .test_loop import FakeBrain

    class Device:
        def drag(self, *a, **k):
            raise AssertionError("固定单位不能收到拖拽")

    r = Runner(Device(), FakeBrain({}), Trace(tmp_path / "run"), ocr=object(), log=lambda _: None, settle=False)
    for name in ["佩佩", "杨叔"]:
        r._board = [Slot("back", 2, 0.5, 0.615, name=name, score=1)]
        for a in [Action(type="drag", x=.5, y=.615, x2=.3, y2=.85),
                  Action(type="drag", x=.3, y=.85, x2=.5, y2=.615)]:
            result = r._raw(a, [])
            assert name in result and "没有执行" in result


def test_yang_companion_is_named_and_does_not_match_peppy(tmp_path):
    from pathlib import Path

    import cv2
    from PIL import Image

    from cwagent.vision.board import Portraits, _bgr
    from cwagent.vision.cards import fixed_companion, is_peppy

    image = Image.open(Path(__file__).parent / "fixtures" / "shop" / "prices-1-1.jpg")
    board = Portraits(tmp_path, {}).read(image, shop_open=True, back_hint=7)
    yang = next(s for s in board if s.name == "杨叔")
    assert yang.zone == "back" and yang.index == 5 and "奇遇占位" in yang.mark
    assert not any(s.name == "佩佩" for s in board)
    full = _bgr(image.resize((1920, 1080)))
    assert not is_peppy(full, round(yang.x * 1920), round(yang.y * 1080))
    peppy = cv2.imread(str(Path(__file__).parent / "fixtures" / "board" / "peppy-back.png"))
    assert fixed_companion(peppy[35:165, 57:183])[0] is None


def test_ready_button_variants_and_paired_strategy(tmp_path):
    from cwagent.orchestrator.loop import Runner, Trace

    from .test_loop import FakeBrain

    r = Runner(None, FakeBrain({}), Trace(tmp_path / "run"), ocr=object(), log=lambda m: None, settle=False)
    r.perceive = lambda *a, **k: ("", {}, [])
    r.guide_advice = lambda *a, **k: ""

    class Frame:
        image = None

    stage = (("备战阶段", 471, 46), ("2-1", 469, 78))
    hints, _ = r._hints("prepare", Frame(), boxes(*stage, ("跳过(2/2)", 1816, 751)), {"step": 1}, True)
    assert "跳过(2/2)" in hints and "跳过这场战斗" in hints
    hints, _ = r._hints("prepare", Frame(), boxes(*stage, ("开启宝库", 1816, 752)), {"step": 1}, True)
    assert "开启宝库" in hints
    r.lit = None
    hints, _ = r._hints("strategy_choice", Frame(),
                        boxes(("请选择投资策略", 960, 100), ("选择一个", 515, 248), ("两个都带走", 1184, 248)),
                        {"step": 1}, False)
    assert "两张一起拿" in hints


def test_peppy_recognized_by_avatar():
    """2026-11-16 那局拿到「可爱的佩佩」后的后台最右格(对局记录缩略图放大,M7A 的头像模板 0.95 分)。"""
    from pathlib import Path

    import cv2
    import numpy as np

    from cwagent.vision.cards import is_peppy

    crop = cv2.imread(str(Path(__file__).parent / "fixtures" / "board" / "peppy-back.png"))
    assert is_peppy(crop, 120, 100)
    assert not is_peppy(np.full_like(crop, 70), 120, 100)


def test_supply_four_card_layout_and_pick_count(monkeypatch):
    from PIL import Image

    from cwagent.vision import supply

    monkeypatch.setattr(supply, "_basic_templates", lambda: {})
    words = boxes(("补给阶段", 961, 162), ("那刻夏", 400, 566), ("景元", 755, 566), ("白露", 1109, 566),
                  ("符玄", 1464, 566), ("轮滑鞋", 438, 698), ("已选择0/2", 1683, 924))
    packs = supply.read_supply(Image.new("RGB", (1920, 1080)), words)
    assert [p.role for p in packs] == ["那刻夏", "景元", "白露", "符玄"] and packs[0].equipment == ["轮滑鞋"]
    assert supply.required(words) == 2 and "这次要选 2 组" in supply.describe(packs, 2)
