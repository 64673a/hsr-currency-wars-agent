"""旧局评价、阵容总结和伤害统计的完整离开流程。"""

import json
from pathlib import Path
import re

from PIL import Image
import pytest

from cwagent.capture import Frame
from cwagent.orchestrator.loop import Runner, Trace
from cwagent.orchestrator.screens import classify, companion_banner
from cwagent.vision.ocr import Ocr, TextBox

FIXTURES = Path(__file__).parent / "fixtures" / "settlement"


def pages():
    out = []
    for page in json.loads((FIXTURES / "startup-pages.json").read_text(encoding="utf-8")):
        boxes = []
        for line in page["texts"]:
            m = re.fullmatch(r"\(([\d.]+),([\d.]+)\) (.*)", line)
            boxes.append(TextBox(m[3], float(m[1]), float(m[2]), .08, .03, .9))
        out.append((page["expected_screen"], boxes))
    return out


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


def test_old_result_pages_reach_home_without_a_model(tmp_path):
    observations = pages()

    class Game:
        current = 0

        def grab(self):
            return Frame(Image.new("RGB", (192, 108), (self.current, 0, 0)), timestamp=0)

        def click(self, x, y):
            # 三张结算页的推进按钮均在底部中间,点奇遇横幅位置不会推进。
            assert abs(x - .5) < .01 and abs(y - .832) < .01
            self.current += 1

    game = Game()
    runner = Runner(game, None, Trace(tmp_path / "run"), ocr=lambda im: observations[im.getpixel((0, 0))[0]][1],
                    settle=False, log=lambda _: None)
    for n, expected in enumerate(("settlement", "previous_run", "previous_run"), 1):
        assert runner.step(n) == expected
    assert game.current == 3
    assert classify(observations[game.current][1]) == "home"
    assert not runner.in_run
    runner.trace.close()


def test_real_summary_image_is_not_a_companion_banner():
    boxes = Ocr()(Image.open(FIXTURES / "summary-2-5.jpg"))
    assert companion_banner(boxes) is None
    assert classify(boxes) == "previous_run"


def test_result_columns_do_not_hide_a_reward_overlay():
    boxes = pages()[1][1]
    assert classify(boxes + [TextBox("点击空白处关闭", .5, .9, .1, .03, .9)]) == "tap_close"


@pytest.mark.parametrize("index,missing", [(1, {"对局未完成", "下一页"}), (2, {"返回货币战争"})])
def test_result_page_missing_button_uses_position_and_has_retry_limit(tmp_path, index, missing):
    boxes = [b for b in pages()[index][1] if b.text not in missing]
    assert classify(boxes) == "previous_run" and companion_banner(boxes) is None
    r = Runner(None, None, Trace(tmp_path / "run"), ocr=object(), settle=False)
    for _ in range(4):
        action = r.reflex("previous_run", None, boxes, {}, set())[0]
        assert action.type == "click" and (action.x, action.y) == (.5, .832)
    assert r.reflex("previous_run", None, boxes, {}, set())[0].type == "pause"


def test_summary_and_statistics_have_separate_retry_budgets(tmp_path):
    r = Runner(None, None, Trace(tmp_path / "run"), ocr=object(), settle=False)
    for _, boxes in pages()[1:3]:
        for _ in range(4):
            assert r.reflex("previous_run", None, boxes, {}, set())[0].type == "click_text"
    assert r.reflex("previous_run", None, boxes, {}, set())[0].type == "pause"


def _page(*texts):
    return [TextBox(t, .5, .1 + .05 * i, .08, .03, .9) for i, t in enumerate(texts)]


# 实机记录里的结算页(OCR 原文,坐标从略)
FINAL_WIN = _page("10", "挑战成功", "3-7", "首领", "火热连胜", "×15", "挑战进度", "100", "前往结算")   # 20261119-102131 第 238 步
FINAL_LOSS = _page("33", "挑战结束", "3-7", "首领", "挑战进度", "-24①", "前往结算")                 # 20261118-094054 第 192 步
HP_OUT = _page("12", "挑战结束", "2-1 战斗", "挑战进度", "-22①", "前往结算")                         # 20261114-092016 第 42 步
EVAL_WIN = _page("挑战成功", "小队生命值❤100", "对局评价", "SSS", "下一步")                           # 20261119-102131 第 240 步


def test_result_pages_follow_m7a():
    """M7A:带「继续挑战」或「前往结算」的是节点结算,程序点按钮;对局在带「对局评价」的整局结算页才结束。
    原来最终首领打赢的那页被认成 other 交给模型,打输的那页直接当成整局结束、没等到整局结算页。"""
    from cwagent.orchestrator.screens import match_outcome

    for boxes in (FINAL_WIN, FINAL_LOSS, HP_OUT):
        assert classify(boxes) == "node_result"
    assert classify(EVAL_WIN) == "settlement" and match_outcome(EVAL_WIN) == "通关"
    assert classify(old_result()) == "settlement" and match_outcome(old_result()) == "失败"


def test_final_result_page_leads_to_settlement_and_outcome(tmp_path):
    r = Runner(None, None, Trace(tmp_path / "run"), ocr=object(), settle=False)
    [action] = r.reflex("node_result", None, FINAL_WIN, {}, set())
    assert (action.type, action.text) == ("click_text", "前往结算")
    r.in_run = True
    record = {}
    r.reflex("settlement", None, EVAL_WIN, record, set())
    assert r.outcome == "通关" and record["note"] == "对局结束:通关"
