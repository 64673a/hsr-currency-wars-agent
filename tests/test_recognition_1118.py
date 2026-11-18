"""2026-11-18 那局(gpt-6.1-sol,A8-20)的识别错误:截图都是那局的原图(对局记录里的标号图或缩小的截图)。"""

from pathlib import Path

import pytest
from PIL import Image

from cwagent.vision.ocr import Ocr

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def ocr():
    return Ocr()


def full(name: str) -> Image.Image:
    return Image.open(FIXTURES / name).convert("RGB").resize((1920, 1080), Image.LANCZOS)


def test_deployed_nine_is_not_read_upside_down(ocr):
    """3-4 起「9/9」被方向分类器当成倒着的字翻成「6/6」;整屏识别把斜杠认成 1,读成「i919」。"""
    from cwagent.vision.hud import read_deployed

    img = full("board/deployed-9-9.jpg")
    assert read_deployed(img, ocr(img), ocr) == ("9/9", False)


def test_parse_deployed_without_slash():
    from cwagent.vision.hud import parse_deployed

    assert parse_deployed("i919") == "9/9" and parse_deployed("515") == "5/5" and parse_deployed("5110") == "5/10"
    assert parse_deployed("i3/5久") == "3/5" and parse_deployed("12/3") == "2/3"


def test_red_tinted_empty_slots_are_empty():
    """「备战席已满」的警告让整个棋盘泛红:后台 4~6 号是空格,原来被认成「认不出」的角色。"""
    from cwagent.vision.board import occupied_slots

    filled = occupied_slots(full("board/bench-full-warning.jpg"), False)
    assert {i for z, i in filled if z == "back"} == {0, 1, 2}
    assert {i for z, i in filled if z == "front"} == {0, 1, 2, 3}


def test_red_digits_without_excess_are_not_over_capacity():
    from cwagent.vision.hud import Hud

    assert "超过上限" not in Hud(deployed="7/7", over=True).text()
    assert "超过上限" in Hud(deployed="8/7", over=True).text()


def test_unknown_shop_name_is_reread(ocr):
    """3-2 的商店第 4 格「翡翠」整屏识别成「霏」;单独裁出名字条重读。认得的名字不重读。"""
    from cwagent.vision.shop import read_shop, reread_names

    img = full("shop/names-3-2.jpg")
    cards = [(1, "三月七", 1), (2, "火花", 1), (3, "白厄", None), (4, "霏", None), (5, "景元", 1)]
    known = {"三月七", "火花", "白厄", "翡翠", "景元"}
    assert [n for _, n, _ in reread_names(img, cards, ocr, known)] == ["三月七", "火花", "白厄", "翡翠", "景元"]
    assert read_shop(ocr(img))[3][1] is not None


def test_drag_can_hold_on_the_target(tmp_path):
    """黄金大饭店喂食:把角色拖到白厄身上按住约 5 秒(维护者 2026-11-18);drag 的 seconds 是按住的秒数。"""
    from cwagent.brain.screen_agent import Action
    from cwagent.device import RecordingDevice
    from cwagent.orchestrator.loop import Runner, Trace
    from tests.test_loop import FakeBrain

    dev = RecordingDevice(source=None)
    r = Runner(dev, FakeBrain({}), Trace(tmp_path / "run"), ocr=object(), log=lambda m: None, settle=False)
    r._raw(Action(type="drag", x=0.29, y=0.85, x2=0.46, y2=0.62, seconds=6), [])
    r._raw(Action(type="drag", x=0.29, y=0.85, x2=0.46, y2=0.62), [])
    assert dev.actions == [("drag", 0.29, 0.85, 0.46, 0.62, 6.0), ("drag", 0.29, 0.85, 0.46, 0.62)]
