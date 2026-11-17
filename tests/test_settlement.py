"""旧局结算页漏读下一步时的回归。"""

from PIL import Image

from cwagent.orchestrator.loop import Runner, Trace
from cwagent.orchestrator.screens import classify
from cwagent.vision.ocr import TextBox


def old_result():
    # 20261116-180853 的第 5～257 步:截图有下一步,OCR 只有以下四行。
    return [TextBox("挑战失败", .499, .218, .1, .03, .9),
            TextBox("小队生命值❤28", .5, .274, .1, .03, .9),
            TextBox("对局评价", .5, .347, .1, .03, .9),
            TextBox("UID:103175008", .051, .975, .1, .03, .9)]


def test_missing_next_button_uses_verified_position_and_stops_retrying(tmp_path):
    r = Runner(None, None, Trace(tmp_path / "run"), ocr=object(), settle=False)
    boxes = old_result()
    assert classify(boxes) == "settlement"
    record = {}
    for _ in range(4):
        actions = r.reflex("settlement", None, boxes, record, set())
        assert len(actions) == 1 and actions[0].type == "click"
        assert (actions[0].x, actions[0].y) == (.5, .832)
    assert record["note"] == "处理启动前的旧局结算"
    assert r.reflex("settlement", None, boxes, {}, set())[0].type == "pause"


def test_new_settlement_page_has_its_own_retry_budget(tmp_path):
    r = Runner(None, None, Trace(tmp_path / "run"), ocr=object(), settle=False)
    for _ in range(4):
        r.reflex("settlement", None, old_result(), {}, set())
    r.reflex("previous_run", Image.new("RGB", (192, 108)),
             [TextBox("返回货币战争", .8, .9, .1, .03, .9)], {}, set())
    next_page = [TextBox("对局总结", .5, .2, .1, .03, .9), TextBox("下一步", .5, .9, .1, .03, .9)]
    action = r.reflex("settlement", None, next_page, {}, set())[0]
    assert action.type == "click_text" and action.text == "下一步"
