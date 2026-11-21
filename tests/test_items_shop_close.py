"""拖动角色/装备/道具前自动关闭商店，以及道具栏在商店收起时缓存、展开时合并。"""

from __future__ import annotations

from PIL import Image

from cwagent.brain.screen_agent import Action, Episode
from cwagent.capture import Frame
from cwagent.orchestrator import loop
from cwagent.orchestrator import prepare as prep
from cwagent.orchestrator.prepare import SHOP_BUTTON, PrepareExecutor, _remap_drag
from cwagent.vision.annotate import AHA_PANEL, annotate
from cwagent.vision.board import Slot, slots
from cwagent.vision.equipment import Item as Equip
from cwagent.vision.items import Item as Consumable
from cwagent.vision.items import SHOP_VISIBLE_SLOTS, describe, merge_items
from cwagent.vision.ocr import TextBox

from .test_loop import FakeBrain, FakeOcr, Sim

OPEN_BOXES = [TextBox("收起", 0.845, 0.909, 0.05, 0.03, 0.95)]
CLOSED_BOXES = [TextBox("商店", 0.845, 0.909, 0.05, 0.03, 0.95)]


class ToggleShopDevice:
    """模拟商店展开/收起：点收起按钮后商店关闭，并记录所有点击和拖拽。"""

    def __init__(self, *, shop_open: bool = True):
        self.shop_open = shop_open
        self.clicks: list[tuple[float, float]] = []
        self.drags: list[tuple[float, float, float, float]] = []
        self.image = Image.new("RGB", (64, 64))

    def click(self, x: float, y: float, button: str = "left") -> None:
        self.clicks.append((x, y))
        if abs(x - SHOP_BUTTON[0]) < 0.03 and abs(y - SHOP_BUTTON[1]) < 0.03:
            self.shop_open = not self.shop_open

    def drag(self, x1: float, y1: float, x2: float, y2: float, **kwargs) -> None:
        self.drags.append((x1, y1, x2, y2))

    def grab(self) -> Frame:
        return Frame(image=self.image, timestamp=0)

    def ocr(self, image=None) -> list[TextBox]:
        return list(OPEN_BOXES if self.shop_open else CLOSED_BOXES)


def test_merge_items_and_describe_and_annotate():
    wrench = Consumable(1, 350101, "拆装扳手", 4, 0.961, 0.152, 0.86)
    furnace = Consumable(2, 350103, "冶金炉", 3, 0.921, 0.152, 0.91)
    token = Consumable(3, 350107, "好运令牌", 1, 0.881, 0.152, 0.88)
    cached = [Consumable(1, 350101, "拆装扳手", 3, 0.961, 0.152, 0.86), furnace, token]

    # 商店展开且前 2 格 id 一致：更新前 2 格数量并接上缓存的第 3 格
    merged = merge_items([wrench, furnace], cached, shop_open=True)
    assert [(i.slot, i.name, i.count) for i in merged] == [
        (1, "拆装扳手", 4), (2, "冶金炉", 3), (3, "好运令牌", 1),
    ]
    text = describe(merged, shop_open=True)
    assert "好运令牌×1(0.881,0.152)" in text and "被商店遮挡" in text
    assert "被商店遮挡" not in describe(merged, shop_open=False)

    # 商店展开时标号不压在商店卡面上的第 3 格；商店收起时全部标号
    im = Image.new("RGB", (960, 540))
    _, marks_open = annotate(im, OPEN_BOXES, screen="prepare", shop_open=True, items=merged)
    _, marks_closed = annotate(im, CLOSED_BOXES, screen="prepare", shop_open=False, items=merged)
    assert [m.label for m in marks_open if m.label.startswith("道具")] == ["道具1 拆装扳手", "道具2 冶金炉"]
    assert [m.label for m in marks_closed if m.label.startswith("道具")] == [
        "道具1 拆装扳手", "道具2 冶金炉", "道具3 好运令牌",
    ]

    # 商店展开但前 2 格不足 2 个：不拼缓存
    assert merge_items([wrench], cached, shop_open=True) == [wrench]
    # 商店展开但前 2 格发生位移（id 变了）：不拼旧的第 3 格
    assert merge_items([furnace, token], cached, shop_open=True) == [furnace, token]
    # 商店收起：直接以当前可见为准
    assert merge_items([wrench, furnace], cached, shop_open=False) == [wrench, furnace]


def test_move_closes_shop_and_uses_closed_coords():
    dev = ToggleShopDevice(shop_open=True)
    closed_calls = []
    ex = PrepareExecutor(dev, dev.ocr, raw=lambda a, b: "", sleep=lambda s: None,
                         on_shop_closed=lambda im, b: closed_calls.append(len(b)))
    board = [Slot("bench", 0, 0.2285, 0.845, name="流萤", score=0.9)]
    out = ex.run([Action(type="move", zone="bench", index=1, to_zone="front", to_index=1)], OPEN_BOXES, board)
    assert not dev.shop_open and not ex.shop
    assert dev.clicks and closed_calls
    front1_closed = next(s for s in slots(False) if (s.zone, s.index) == ("front", 0))
    assert len(dev.drags) == 1
    assert abs(dev.drags[0][2] - front1_closed.x) < 0.002 and abs(dev.drags[0][3] - front1_closed.y) < 0.002
    assert out.results == ["备战席1号 → 前台1号"]


def test_compose_and_equip_close_shop_first(monkeypatch):
    class Bag:
        def __init__(self):
            self.items = [Equip(1, 0.961, 0.23, "a", "轮滑鞋"), Equip(2, 0.961, 0.30, "b", "折叠小刀")]
            self.confirm = False

        def read(self, image):
            return list(self.items)

    bag = Bag()
    dev = ToggleShopDevice(shop_open=True)
    orig_drag = dev.drag

    def drag(x1, y1, x2, y2, **kwargs):
        orig_drag(x1, y1, x2, y2, **kwargs)
        assert not dev.shop_open   # 拖装备合成和穿戴时商店都必须已关闭
        if len(dev.drags) == 1:
            bag.confirm = True
        else:
            bag.items = []

    orig_click = dev.click

    def click(x, y, button="left"):
        orig_click(x, y, button)
        if bag.confirm and abs(x - 0.948) < 0.02:
            bag.confirm = False
            bag.items = [Equip(1, 0.961, 0.23, "c", "火力风暴潮")]

    dev.drag = drag
    dev.click = click
    monkeypatch.setattr(prep, "find_compose_confirm", lambda image: (0.948, 0.301) if bag.confirm else None)
    ex = PrepareExecutor(dev, dev.ocr, raw=lambda a, b: "", sleep=lambda s: None, equipment=bag)
    board = [Slot("front", 0, 0.410, 0.451, name="那刻夏", score=0.9)]

    out = ex.run([Action(type="compose", item=2, onto=1)], OPEN_BOXES, board)
    assert not dev.shop_open and "合成了 火力风暴潮" in out.results[0]

    # 再测商店展开状态下直接穿装备：先关商店，并按收起态前台 1 号坐标拖
    dev.shop_open = True
    ex.shop = True
    out = ex.run([Action(type="equip", item=1, zone="front", index=1)], OPEN_BOXES, board)
    assert not dev.shop_open and out.equipped == [("那刻夏", "火力风暴潮")]
    front1_closed = next(s for s in slots(False) if (s.zone, s.index) == ("front", 0))
    assert abs(dev.drags[-1][2] - front1_closed.x) < 0.002 and abs(dev.drags[-1][3] - front1_closed.y) < 0.002


def test_drag_closes_shop_and_remaps_board_and_aha_coords():
    dev = ToggleShopDevice(shop_open=True)
    seen_raw: list[Action] = []

    def raw(a: Action, boxes: list[TextBox]) -> str:
        assert not dev.shop_open
        seen_raw.append(a)
        return "ok"

    ex = PrepareExecutor(dev, dev.ocr, raw=raw, sleep=lambda s: None)
    # 商店展开时模型发「拖第 3 格好运令牌 → 展开态前台 1 号(0.410, 0.451)」
    ex.run([Action(type="drag", x=0.881, y=0.152, x2=0.410, y2=0.451)], OPEN_BOXES, None)
    front1_closed = next(s for s in slots(False) if (s.zone, s.index) == ("front", 0))
    assert len(seen_raw) == 1
    assert seen_raw[0].x == 0.881 and seen_raw[0].y == 0.152
    assert abs(seen_raw[0].x2 - front1_closed.x) < 0.002 and abs(seen_raw[0].y2 - front1_closed.y) < 0.002

    # 展开态阿哈面板中心 → 收起态阿哈面板中心
    ox0, oy0, ox1, oy1 = AHA_PANEL[True]
    cx0, cy0, cx1, cy1 = AHA_PANEL[False]
    remapped = _remap_drag(Action(type="drag", x=0.961, y=0.230, x2=(ox0 + ox1) / 2, y2=(oy0 + oy1) / 2), None)
    assert abs(remapped.x2 - (cx0 + cx1) / 2) < 0.002 and abs(remapped.y2 - (cy0 + cy1) / 2) < 0.002

    # 同一批动作 [move, drag]：move 已经关了商店，但模型决策时商店开着，后续 drag 仍按决策态换算坐标
    dev2 = ToggleShopDevice(shop_open=True)
    seen2: list[Action] = []
    ex2 = PrepareExecutor(dev2, dev2.ocr, raw=lambda a, b: seen2.append(a) or "ok", sleep=lambda s: None)
    board = [Slot("bench", 0, 0.2285, 0.845, name="流萤", score=0.9)]
    ex2.run([Action(type="move", zone="bench", index=1, to_zone="front", to_index=2),
             Action(type="drag", x=0.761, y=0.152, x2=0.410, y2=0.451)], OPEN_BOXES, board)
    assert len(seen2) == 1 and seen2[0].x == 0.761
    assert abs(seen2[0].x2 - front1_closed.x) < 0.002 and abs(seen2[0].y2 - front1_closed.y) < 0.002
    assert loop.ITEM_BAR[0] <= 0.761 and loop.ITEM_BAR[1] <= 0.152 <= loop.ITEM_BAR[2]


def test_runner_caches_hidden_token_on_banner_step_and_uses_it_when_shop_open(tmp_path, monkeypatch):
    """复现 2-5：第 1 步播备战横幅且商店收起时缓存第 3 格好运令牌；第 2 步商店自动展开仍能认出并拖动使用。"""
    banner_closed = [
        ("备战阶段", 0.245, 0.042), ("2-5", 0.244, 0.071), ("备战阶段", 0.500, 0.469),
        ("出战", 0.947, 0.694), ("商店", 0.845, 0.909),
    ]
    shop_open = [
        ("备战阶段", 0.149, 0.065), ("2-5", 0.149, 0.100),
        ("出战", 0.947, 0.694), ("收起", 0.845, 0.909),
    ]
    shop_closed = [
        ("备战阶段", 0.149, 0.065), ("2-5", 0.149, 0.100),
        ("出战", 0.947, 0.694), ("商店", 0.845, 0.909),
    ]
    wrench = Consumable(1, 350101, "拆装扳手", 4, 0.961, 0.152, 0.86)
    furnace = Consumable(2, 350103, "冶金炉", 3, 0.921, 0.152, 0.91)
    token = Consumable(3, 350107, "好运令牌", 1, 0.881, 0.152, 0.88)

    def rules(state, action):
        if state == "banner":
            return "open"
        if state == "open" and action[0] == "click" and abs(action[1] - 0.845) < 0.03:
            return "closed"
        if state == "closed" and action[0] == "drag":
            return "used"
        return None

    sim = Sim({"banner": banner_closed, "open": shop_open, "closed": shop_closed, "used": shop_closed}, "banner", rules)
    monkeypatch.setattr(loop.time, "sleep", lambda _: None)
    monkeypatch.setattr(loop, "read_items", lambda img, ocr=None, max_slots=6: (
        [wrench, furnace] if sim.state == "used" else [wrench, furnace, token][:max_slots]
    ))

    # 模型在商店展开时发「把第 3 格好运令牌拖到展开态前台 1 号(0.410, 0.451)」
    drag_token = Action(type="drag", x=0.881, y=0.152, x2=0.410, y2=0.451)
    brain = FakeBrain({"prepare": [[drag_token]]})
    r = loop.Runner(sim, brain, loop.Trace(tmp_path / "run"), ocr=FakeOcr(sim), log=lambda _: None, settle=False)

    class StubPortraits:
        counts = {"front": 4, "back": 6}

        def learn_from_shop(self, image, boxes):
            return []

        def read(self, image, *, shop_open=False, back_hint=None):
            return [Slot("front", 0, 0.410 if shop_open else 0.389, 0.451 if shop_open else 0.372,
                         name="那刻夏", score=0.9)]

    r.portraits = StubPortraits()

    # Step 1：横幅等待，顺手缓存包含好运令牌的完整道具栏
    assert r.step(1) == "prepare"
    assert [it.name for it in r._items_cache] == ["拆装扳手", "冶金炉", "好运令牌"]

    # Step 2：商店已自动展开，perceive 合并缓存后认出好运令牌；拖动时先关商店并把终点换算为收起态前台 1 号
    sim.state = "open"
    r.episode = Episode(id=1, key=("prepare", "2-5"), screen="prepare", node="2-5", role="tactical", max_turns=2)
    assert r.step(2) == "prepare"
    assert [it.name for it in r._items] == ["拆装扳手", "冶金炉", "好运令牌"]
    front1_closed = next(s for s in slots(False) if (s.zone, s.index) == ("front", 0))
    drags = [a for a in sim.actions if a[0] == "drag"]
    assert len(drags) == 1
    assert drags[0][1:3] == (0.881, 0.152)
    assert abs(drags[0][3] - front1_closed.x) < 0.002 and abs(drags[0][4] - front1_closed.y) < 0.002
    assert "好运令牌" in r._results[0] and "前台1号 那刻夏" in r._results[0]
    # 用完后无弹窗，on_shop_closed 立即刷新缓存，好运令牌已消失
    assert [it.name for it in r._items_cache] == ["拆装扳手", "冶金炉"]
    assert SHOP_VISIBLE_SLOTS == 2


def test_set_shop_close_waits_until_animation_settles_before_caching():
    """点收起后右下按钮字先变「商店」，但顶部面板还在滑出动画中：要等画面静止才回调 on_shop_closed。"""
    sliding = Image.new("RGB", (64, 64), (10, 10, 10))
    sliding2 = Image.new("RGB", (64, 64), (90, 90, 90))
    settled = Image.new("RGB", (64, 64), (200, 200, 200))

    class AnimatedCloseDevice(ToggleShopDevice):
        def __init__(self):
            super().__init__(shop_open=True)
            self.frames = [sliding, sliding2, settled, settled]
            self.idx = 0

        def grab(self) -> Frame:
            im = self.frames[min(self.idx, len(self.frames) - 1)]
            self.idx += 1
            return Frame(image=im, timestamp=0)

    dev = AnimatedCloseDevice()
    captured: list[tuple[int, int, int]] = []
    ex = PrepareExecutor(dev, dev.ocr, raw=lambda a, b: "", sleep=lambda s: None,
                         on_shop_closed=lambda im, b: captured.append(im.getpixel((0, 0))))
    ex.set_shop(prep.PrepareResult(), OPEN_BOXES, False)
    assert not ex.shop and not dev.shop_open
    # 不会在动画中途的 sliding / sliding2 上回调，只在静止帧 settled 上回调一次
    assert captured == [(200, 200, 200)]


def test_cache_items_no_shrink_preserves_slot3_on_transient_grab(tmp_path, monkeypatch):
    """主动开商店前的单帧抓图如果恰好被开始展开的商店挡住第 3 格，不裁掉已有缓存的第 3 格。"""
    wrench = Consumable(1, 350101, "拆装扳手", 3, 0.961, 0.152, 0.86)
    furnace = Consumable(2, 350103, "冶金炉", 2, 0.921, 0.152, 0.91)
    proj = Consumable(3, 350105, "员工投影仪", 2, 0.881, 0.152, 0.94)
    sim = Sim({"closed": [("商店", 0.845, 0.909)]}, "closed", lambda *_: None)
    r = loop.Runner(sim, FakeBrain({}), loop.Trace(tmp_path / "run"), ocr=FakeOcr(sim), log=lambda _: None, settle=False)
    r._items_cache = [wrench, furnace, proj]
    monkeypatch.setattr(loop, "read_items", lambda img, ocr=None, max_slots=6: [wrench, furnace])
    r._cache_items(sim.grab().image, shrink=False)
    assert [it.name for it in r._items_cache] == ["拆装扳手", "冶金炉", "员工投影仪"]
    # 静止帧全量刷新（shrink=True）则正常更新为 2 格
    r._cache_items(sim.grab().image, shrink=True)
    assert [it.name for it in r._items_cache] == ["拆装扳手", "冶金炉"]
