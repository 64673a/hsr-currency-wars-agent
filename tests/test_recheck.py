"""星级的两帧核对和模型的复查(recheck):截图为准,识别对不上时补读(参考 trail-cli)。"""

from pathlib import Path

import pytest
from PIL import Image

from cwagent.brain.screen_agent import Action, ActInput
from cwagent.vision import board as board_mod
from cwagent.vision.board import Portraits, Slot, confirm_stars, format_board, recheck_diff

FIXTURES = Path(__file__).parent / "fixtures" / "board"


def unit(name="姬子", stars=2, zone="front", index=0, **kw):
    return Slot(zone, index, 0.4 + index * 0.07, 0.372, name=name, score=0.9, stars=stars, **kw)


def test_stars_that_differ_between_two_frames_are_reported_as_unsure(monkeypatch):
    image = Image.new("RGB", (1920, 1080))
    monkeypatch.setattr(board_mod, "star_colors", lambda card: (1, 0))   # 早一帧读出 1 颗金星
    board = [unit(stars=2), unit("瓦尔特", stars=1, index=1), Slot("bench", 0, 0.23, 0.845, mark="乱流格")]
    confirm_stars(board, image)
    assert (board[0].stars, board[0].stars_unsure) == (0, True)
    assert (board[1].stars, board[1].stars_unsure) == (1, False)
    text = format_board(board)
    assert "1号 姬子 星级待定" in text and "2号 瓦尔特 1星" in text and "1号(空,乱流格)" in text
    # 主角光环(蓝星)按颜色一起比:颜色变了也算待定
    halo = [unit(stars=1, halo=True)]
    confirm_stars(halo, image)
    assert halo[0].stars_unsure


def test_real_frames_with_glowing_turbulence_keep_star_readings():
    """2026-11-17 实机两帧:备战席的乱流格一直在闪,场上角色的星级两帧一致,不该报待定。"""
    a, b = (Image.open(FIXTURES / f"turbulence-{k}.jpg").convert("RGB") for k in "ab")
    board = Portraits(Path("/nonexistent"), {}).read(b)
    assert sum(1 for s in board if s.mark == "乱流格") == 4
    filled = [s for s in board if not s.empty]
    assert filled and all(s.stars for s in filled)
    confirm_stars(board, a)
    assert not any(s.stars_unsure for s in board)


def test_recheck_diff_lists_changed_slots():
    before = [unit(stars=1), Slot("bench", 2, 0.36, 0.845)]
    after = [unit(stars=2), unit("三月七", stars=1, zone="bench", index=2)]
    assert recheck_diff(before, after) == "前台1号 姬子 1星 → 姬子 2星;备战席3号 空 → 三月七 1星"
    assert recheck_diff(after, after) == "和上一轮一致"
    assert "以这次为准" in recheck_diff(None, after)


def test_recheck_is_a_lone_free_look(tmp_path, monkeypatch):
    from cwagent.orchestrator import loop

    ActInput(analysis="星级对不上", actions=[Action(type="recheck")])
    with pytest.raises(ValueError, match="单独提交"):
        ActInput(analysis="x", actions=[Action(type="recheck"), Action(type="ready")])

    from .test_loop import FakeBrain

    slept = []
    monkeypatch.setattr(loop.time, "sleep", slept.append)
    r = loop.Runner(None, FakeBrain({}), loop.Trace(tmp_path / "run"), ocr=object(), log=lambda _: None, settle=False)
    assert "重新截图识别" in r._raw(Action(type="recheck"), [])
    assert r._recheck and slept == [loop.RECHECK_WAIT]
