"""M7A v2026.11.18 新加的 S2 界面(文字和 1920x1080 坐标照抄它的测试 tests/test_tasks/test_currency_wars_s2.py)。"""

from cwagent.orchestrator.screens import classify
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
