"""开局页面导航、操作后核对和有界恢复。"""

import numpy as np
import pytest
from PIL import Image

from cwagent.orchestrator import entry
from cwagent.vision.ocr import TextBox


def words(*items):
    return [TextBox(text, x, y, .05, .02, .9) for text, x, y in items]


def test_world_phone_guide_route_and_confirmed_destination(monkeypatch):
    visible = {}
    monkeypatch.setattr(entry, "targets", lambda _: visible)
    image = Image.new("RGB", (1920, 1080))
    nav = entry.EntryNavigator()
    visible["world"] = (.03, .04)
    assert nav.plan("other", image, [], in_run=False)[0].key == "esc"
    visible.clear()
    visible["phone_guide"] = (.8, .5)
    action = nav.plan("other", image, [], in_run=False)[0]
    assert action.type == "click" and (action.x, action.y) == (.8, .5)
    visible.clear()
    visible.update(guide2=(.1, .1), guide5_tab=(.7, .1))
    assert nav.plan("other", image, [], in_run=False)[0].x == .7
    visible.clear()
    visible["guide5"] = (.7, .1)
    # 右边仍显示其他玩法时,不能点它的「前往参与」。
    text = words(("货币战争", .22, .45), ("差分宇宙", .55, .35), ("前往参与", .75, .78))
    assert nav.plan("other", image, text, in_run=False)[0].x == .22
    text = words(("货币战争", .22, .45), ("货币战争·入画奇航", .55, .35), ("前往参与", .75, .78))
    assert nav.plan("other", image, text, in_run=False)[0].x == .75 and nav.joining
    text = words(("货币战争", .22, .32), ("入画奇航", .82, .38), ("前往参与", .78, .81))
    assert nav.plan("other", image, text, in_run=False)[0].x == .78
    visible.clear()
    visible["world"] = (.03, .04)
    assert nav.plan("other", image, [], in_run=False)[0].type == "wait"
    assert nav.plan("other", image, words(("货币战争", .65, .5)), in_run=False)[0].key == "f"


def test_live_game_is_never_sent_back_to_the_menu(monkeypatch):
    def unexpected(_):
        raise AssertionError("对局中不应识别开局导航模板")

    monkeypatch.setattr(entry, "targets", unexpected)
    nav = entry.EntryNavigator()
    image = Image.new("RGB", (1920, 1080))
    assert nav.plan("other", image, [], in_run=True) is None
    assert nav.plan("prepare", image, [], in_run=False) is None
    assert nav.plan("quit_dialog", image, [], in_run=False) is None


@pytest.mark.parametrize("screen", ["home", "mode_select", "difficulty", "run_intro", "prepare", "battle", "supply"])
def test_currency_wars_pages_skip_the_entry_route(monkeypatch, screen):
    monkeypatch.setattr(entry, "targets", lambda _: pytest.fail("已在货币战争页面,不应重走导航"))
    nav = entry.EntryNavigator()
    assert nav.plan(screen, Image.new("RGB", (1920, 1080)), [], in_run=False) is None
    assert nav.actions == 0


def test_homepage_template_skips_world_even_when_background_matches(monkeypatch):
    monkeypatch.setattr(entry, "targets", lambda _: {"homepage": (.8, .8), "world": (.03, .04)})
    nav = entry.EntryNavigator()
    action = nav.plan("other", Image.new("RGB", (1920, 1080)), [], in_run=False)[0]
    assert action.type == "click" and action.x == .8 and nav.page == "homepage"


def test_transient_banner_takes_priority_over_entry_recovery(tmp_path, monkeypatch):
    from cwagent.orchestrator.loop import Runner, Trace

    r = Runner(None, None, Trace(tmp_path / "run"), ocr=object(), settle=False)
    monkeypatch.setattr(r.entry, "plan", lambda *a, **kw: pytest.fail("过场不应触发入口恢复"))
    actions = r.reflex("other", Image.new("RGB", (1920, 1080)),
                       words(("阿哈帮大忙了", .5, .4)), {}, set())
    assert actions[0].type == "wait"


def test_unknown_page_and_failed_click_have_retry_limits(monkeypatch):
    visible = {}
    monkeypatch.setattr(entry, "targets", lambda _: visible)
    nav = entry.EntryNavigator()
    image = Image.new("RGB", (1920, 1080))
    unknown = words(("背包", .1, .1), ("遗器", .2, .2))
    assert [nav.plan("other", image, unknown, in_run=False)[0].key for _ in range(3)] == ["esc"] * 3
    assert nav.plan("other", image, unknown, in_run=False)[0].type == "pause"
    nav = entry.EntryNavigator()
    visible["phone_guide"] = (.8, .5)
    assert [nav.plan("other", image, [], in_run=False)[0].type for _ in range(5)] == ["click"] * 4 + ["pause"]


def test_entry_icons_match_on_the_full_game_canvas():
    rng = np.random.default_rng(8)
    canvas = rng.integers(20, 80, (1080, 1920, 3), dtype=np.uint8)
    template = entry.templates()["guide5_tab"]
    h, w = template.shape[:2]
    canvas[80:80+h, 800:800+w] = template
    image = Image.fromarray(canvas[:, :, ::-1])
    assert entry.targets(image)["guide5_tab"] == ((800 + w / 2) / 1920, (80 + h / 2) / 1080)


def test_real_guide_page_enters_the_selected_currency_wars():
    from pathlib import Path

    from cwagent.vision.ocr import Ocr

    image = Image.open(Path(__file__).parent / "fixtures" / "entry" / "currency-guide.jpg")
    nav = entry.EntryNavigator()
    action = nav.plan("other", image, Ocr()(image), in_run=False)[0]
    assert action.type == "click" and action.x > .7 and action.y > .7 and nav.joining
