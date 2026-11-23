"""一批操作中途被打断(2026-11-23 维护者要求整理):上场凑出羁绊后弹出选择框、装备栏被前面的操作重排。

2026-11-16~21 的对局里,弹窗出来以后后面的装备报「装备栏没有变化」、合成报「没有出现合成确认」、
出战报了成功其实没出战;拿武装箱后装备栏重排,下一件按旧编号装给了别人。
"""

from PIL import Image

from cwagent.brain.screen_agent import Action
from cwagent.capture import Frame
from cwagent.orchestrator.prepare import POPUP_STOP, PrepareExecutor, bag_moved
from cwagent.vision.equipment import Item, cell_center
from cwagent.vision.ocr import TextBox
from tests.test_receipts import COUNTS, Board


def box(text, x, y):
    return TextBox(text, x, y, 0.05, 0.02, 0.9)


PREPARE = [box("备战阶段", 0.149, 0.065), box("1-4", 0.150, 0.100), box("商店", 0.845, 0.909)]
DANCE = [box("梦境交织", 0.544, 0.071), box("选择2个角色进行共舞,为其增加战斗内连携效果", 0.544, 0.126),
         box("确认选择", 0.777, 0.553)]
HACK = [box("骇入效果", 0.500, 0.080), box("详情", 0.300, 0.700), box("确认选择", 0.777, 0.860)]


class Popping(Board):
    """拖完以后目标格出现卡面,同时(或稍后)弹出 popup 的文字。"""

    def __init__(self, popup, *, delay=0):
        super().__init__()
        self.popup, self.delay, self.shown = popup, delay, False
        self.clicks = []

    def drag(self, *a, **k):
        super().drag(*a, **k)
        self.shown = True

    def click(self, x, y, button="left"):
        self.clicks.append((x, y))

    def ocr(self, image, region=None):
        if self.shown:
            if self.delay:
                self.delay -= 1
                return PREPARE
            return self.popup
        return PREPARE


def executor(game, **kw):
    ex = PrepareExecutor(game, game.ocr, raw=lambda a, b: "", sleep=lambda s: None, **kw)
    ex.counts = dict(COUNTS)
    return ex


def test_equip_after_a_deploy_waits_for_the_trait_popup():
    game = Popping(DANCE, delay=2)        # 共舞框有出场动画,拖完前两眼还是备战
    out = executor(game).run([Action(type="move", zone="bench", index=1, to_zone="back", to_index=6),
                              Action(type="equip", item=1, zone="back", index=6)], PREPARE, None)
    assert out.results == ["备战席1号 → 后台6号", POPUP_STOP.format(name="梦境交织共舞")]
    assert out.observe and len(game.drags) == 1           # 装备没有拖到弹窗上


def test_consecutive_deploys_do_not_wait_between_moves():
    """连着上几个人不用每次都等弹窗:弹窗挡住时下一次拖人自己会失败,失败时说明是弹窗。"""
    game = Popping(HACK)              # 第一次拖人后弹出骇入效果;第二次拖到空着的备战席 8 号,画面不再变化
    first = Action(type="move", zone="bench", index=1, to_zone="back", to_index=6)
    ex = executor(game)
    calls = []
    ex._popup = lambda boxes=None, tries=4: calls.append(tries) or (boxes, "hack" if game.shown else None)
    out = ex.run([first, Action(type="move", zone="back", index=1, to_zone="bench", to_index=8)], PREPARE, None)
    assert "没成功(画面上弹出了「骇入效果」,先处理这个界面)" in out.results[-1]
    assert calls == [1]                                   # 只在拖人失败时看了一眼


def test_ready_does_not_count_a_popup_as_entering_battle():
    class Ready:
        def __init__(self):
            self.popup = False

        def click(self, x, y, button="left"):
            if abs(x - 0.946) < 0.01 and abs(y - 0.694) < 0.01:
                self.popup = True                         # 出战时游戏要先选共舞

        def grab(self):
            return Frame(image=Image.new("RGB", (8, 8)), timestamp=0)

        def ocr(self, image, region=None):
            return DANCE if self.popup else PREPARE

    game = Ready()
    ex = PrepareExecutor(game, game.ocr, raw=lambda a, b: "", sleep=lambda s: None)
    out = ex.run([Action(type="ready")], PREPARE, None)
    assert not out.ready and out.observe
    assert out.results == ["出战:没有出战。" + POPUP_STOP.format(name="梦境交织共舞")]


class Bag:
    """装备栏:按名字给出。"""

    def __init__(self, names):
        self.names = names

    def read(self, image):
        return [Item(i, *cell_center(i), None if n is None else f"id-{n}", n, "简易装备")
                for i, n in enumerate(self.names, 1)]


def test_equip_stops_when_the_bag_was_reordered_by_an_earlier_action():
    """2026-11-21 那局 1-9:拿武装箱的轮滑鞋进了装备栏,「装备 3 号」按旧编号把轮滑鞋装给了花火。"""
    game = Popping(PREPARE)
    ex = executor(game, equipment=Bag(["幸运星", "轮滑鞋", "随便骰子"]))
    ex.bag_seen = ["幸运星", "随便骰子"]                  # 模型给编号时只有两件
    out = ex.run([Action(type="equip", item=2, zone="front", index=2)], PREPARE, None)
    assert out.observe and not game.drags
    assert "装备栏和你给编号时不一样了" in out.results[0] and "2号 轮滑鞋" in out.results[0]
    assert ex.bag_seen == ["幸运星", "轮滑鞋", "随便骰子"]   # 下一轮按新的一栏核对


def test_bag_moved_ignores_unrecognized_cells():
    assert not bag_moved(["幸运星", None], ["幸运星", "轮滑鞋"])
    assert bag_moved(["幸运星", "轮滑鞋"], ["轮滑鞋", "幸运星"])
    assert bag_moved(["幸运星"], ["幸运星", "轮滑鞋"])
