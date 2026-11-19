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


def test_transition_after_ready_does_not_call_the_model(tmp_path):
    """出战后的黑屏、羁绊特效:看不出界面、又没有按钮就等着(2026-11-18 那局 5 次调用模型只得到「等待」)。"""
    import time

    from cwagent.orchestrator.loop import Runner, Trace
    from cwagent.vision.ocr import TextBox
    from tests.test_loop import FakeBrain

    r = Runner(None, FakeBrain({}), Trace(tmp_path / "run"), ocr=object(), log=lambda m: None, settle=False)
    image = Image.new("RGB", (64, 36))
    star = [TextBox("仙舟舰队", 0.5, 0.5, 0.1, 0.03, 0.9), TextBox("星槎出击", 0.5, 0.6, 0.1, 0.03, 0.9)]
    dialog = star + [TextBox("确认", 0.6, 0.7, 0.05, 0.03, 0.9)]

    def transition(boxes):
        acts = r.reflex("other", image, boxes, {}, {b.text for b in boxes})
        return bool(acts) and acts[0].reason == "出战后的过场"

    assert not transition(star)                 # 平时照常交给模型
    r._ready_at = time.monotonic()
    assert transition(star) and not transition(dialog)   # 有按钮的弹窗不算过场


def test_shop_is_opened_after_the_steps_that_need_it_closed(tmp_path):
    """看道具、认卡都要商店收着(里面会收商店):放在开商店之前,模型第一轮拿到的就是开着商店的画面
    (2026-11-18 那局 9 次第一轮只能先点「商店」)。"""
    from cwagent.orchestrator.loop import Runner, Trace
    from tests.test_loop import FakeBrain, FakeOcr, Sim

    sim = Sim({"prepare": [("备战阶段", 0.24, 0.04), ("商店", 0.845, 0.909)]}, "prepare", lambda *_: None)
    r = Runner(sim, FakeBrain({}), Trace(tmp_path / "run"), ocr=FakeOcr(sim), log=lambda m: None, settle=False)
    calls = []

    class Opened(Exception):
        pass

    def set_shop(out, boxes, want):
        calls.append("开商店")
        raise Opened

    r.macros = lambda *a: None
    r._peek_items = lambda n, boxes: calls.append("看道具")
    r._identify_unknown = lambda n, frame, boxes: calls.append("认卡")
    r.prepare.set_shop = set_shop
    r._remember_hud = lambda *a: None
    frame = sim.grab()
    with pytest.raises(Opened):
        r.decide(1, frame, r.ocr(frame.image), "prepare", {"step": 1}, set())
    assert calls == ["看道具", "认卡", "开商店"]
