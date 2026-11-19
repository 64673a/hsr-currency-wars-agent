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


def test_legend_compresses_runs_of_slots():
    """备战界面的对照表把连号的格子压成区间,带名字的标号和文字标号照原样(2026-11-19 报告 P1-8)。"""
    from cwagent.vision.annotate import Mark, legend

    labels = [f"前台{i}" for i in range(1, 5)] + [f"后台{i}" for i in range(1, 7)] + [f"备战席{i}" for i in range(1, 10)] \
        + [f"商店{i}" for i in range(1, 6)] + ["道具1 拆装扳手", "道具2 冶金炉", "刷新", "出战", "收起"]
    marks = [Mark(i, 0.5, 0.5, label) for i, label in enumerate(labels, start=1)]
    assert legend(marks) == ("## 截图标号\n1~4=前台1~4;5~10=后台1~6;11~19=备战席1~9;20~24=商店1~5;"
                             "25=道具1 拆装扳手;26=道具2 冶金炉;27=刷新;28=出战;29=收起")
    assert legend([Mark(1, .5, .5, "确认"), Mark(2, .5, .5, "装备1"), Mark(3, .5, .5, "后台3")]) == \
        "## 截图标号\n1=确认;2=装备1;3=后台3"


def test_inspect_accepts_coordinates(tmp_path, monkeypatch):
    """没有标号的地方(角色、阿哈面板、羁绊图标)按坐标查看(2026-11-19 报告 C1:原来只认标号)。"""
    import pytest
    from pydantic import ValidationError

    from cwagent.brain.screen_agent import Action, action_text
    from cwagent.orchestrator import loop
    from cwagent.orchestrator.loop import Runner, Trace
    from tests.test_loop import FakeBrain, FakeOcr, Sim

    with pytest.raises(ValidationError):
        Action(type="inspect", mode="click")
    a = Action(type="inspect", x=0.389, y=0.372, mode="click")
    assert action_text(a) == "查看 (0.389,0.372)(click)"
    sim = Sim({"prep": [("备战阶段", 0.12, 0.04)]}, "prep", lambda *_: None)
    runner = Runner(sim, FakeBrain({}), Trace(tmp_path / "run"), ocr=FakeOcr(sim), log=lambda _: None, settle=False)
    monkeypatch.setattr(loop.time, "sleep", lambda s: None)
    assert runner._raw(a, []).endswith("说明见新截图和识别结果")
    assert sim.actions == [("click", 0.389, 0.372)]


def test_marks_sit_beside_text_and_skip_descriptions():
    """标号贴在文字左侧、不压字;说明文字不标(2026-11-19 那局投资策略界面「军火[6]易+」)。"""
    from PIL import Image

    from cwagent.vision.annotate import annotate, place
    from cwagent.vision.ocr import TextBox

    boxes = [TextBox("军火贸易+", 0.240, 0.452, 0.06, 0.03, 0.95),
             TextBox("获得1个【简易武装箱】,获得", 0.240, 0.494, 0.17, 0.03, 0.95),
             TextBox("刷新次数1", 0.250, 0.791, 0.05, 0.03, 0.95), TextBox("确认", 0.509, 0.910, 0.03, 0.03, 0.95)]
    _, marks = annotate(Image.new("RGB", (1920, 1080)), boxes, screen="strategy_choice")
    assert [m.label for m in marks] == ["军火贸易+", "刷新次数1", "确认"]
    for m in marks:
        left, top = place(m, 1280, 720, 20, 19)
        assert left + 20 <= (m.x - m.w / 2) * 1280      # 整个标号框在文字左边


def test_prepare_marks_bonds_and_aha_panel_not_slots():
    """备战界面不标格子和商店卡,标左侧羁绊和阿哈面板(按颜色认,随商店开合换位置)。"""
    from PIL import Image, ImageDraw

    from cwagent.vision.annotate import AHA_PANEL, annotate
    from cwagent.vision.ocr import TextBox

    img = Image.new("RGB", (1920, 1080), (40, 30, 90))
    x0, y0, x1, y1 = AHA_PANEL[False]
    ImageDraw.Draw(img).rectangle((x0 * 1920, y0 * 1080, x1 * 1920, y1 * 1080), fill=(170, 30, 50))
    boxes = [TextBox("幻月游戏·唱名", 0.092, 0.230, 0.07, 0.03, 0.95), TextBox("3/5/7/10", 0.083, 0.261, 0.05, 0.03, 0.95),
             TextBox("出战", 0.947, 0.694, 0.03, 0.03, 0.95), TextBox("商店", 0.845, 0.909, 0.03, 0.03, 0.95)]
    _, marks = annotate(img, boxes, screen="prepare", shop_open=False)
    assert [m.label for m in marks] == ["阿哈面板(阿哈装备拖到这里)", "羁绊 幻月游戏·唱名", "出战", "商店"]
    _, marks = annotate(img, boxes, screen="prepare", shop_open=True)   # 商店开着时面板在别处,这里没有
    assert "阿哈面板(阿哈装备拖到这里)" not in [m.label for m in marks]
