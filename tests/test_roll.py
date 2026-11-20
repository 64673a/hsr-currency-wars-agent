"""roll(D 牌):程序自己反复买目标、刷新,到条件才停(维护者 2026-11-18 要求)。"""

from PIL import Image

from cwagent.brain.screen_agent import Action
from cwagent.capture import Frame
from cwagent.orchestrator.prepare import PrepareExecutor
from cwagent.vision.board import SHOP_NAME_Y, SHOP_X, Slot
from cwagent.vision.ocr import TextBox

COSTS = {"那刻夏": {1}, "火花": {1}, "流萤": {2}, "黄泉": {4}, "景元": {1}}


class Shop:
    """假游戏:商店按刷新轮次给出 5 张卡;点卡买(金币够、备战席有空位),点刷新换下一轮(2 金币)。"""

    def __init__(self, rounds, gold, bench_free=9):
        self.rounds, self.i, self.gold, self.bench_free = rounds, 0, gold, bench_free
        self.cards = list(rounds[0])
        self.clicks = 0

    def click(self, x, y, button="left"):
        self.clicks += 1
        if abs(y - 0.15) < 0.02:
            slot = min(range(5), key=lambda k: abs(SHOP_X[k] - x))
            card = self.cards[slot]
            if card and self.gold >= card[1] and self.bench_free:
                self.gold -= card[1]
                self.bench_free -= 1
                self.cards[slot] = None
        elif abs(x - 0.842) < 0.03 and abs(y - 0.451) < 0.03 and self.gold >= 2:
            self.gold -= 2
            self.i = min(self.i + 1, len(self.rounds) - 1)
            self.cards = list(self.rounds[self.i])

    def key(self, k):
        if k == "d":
            self.click(0.842, 0.451)

    def grab(self):
        return Frame(image=Image.new("RGB", (8, 8)), timestamp=0)

    def boxes(self, image=None):
        out = [TextBox("收起", 0.845, 0.909, 0.04, 0.03, 0.9), TextBox("刷新", 0.842, 0.451, 0.04, 0.03, 0.9),
               TextBox("2", 0.852, 0.492, 0.01, 0.03, 0.9)]
        y = (SHOP_NAME_Y[0] + SHOP_NAME_Y[1]) / 2
        for k, card in enumerate(self.cards):
            if card:
                out += [TextBox(card[0], SHOP_X[k] - 0.02, y, 0.05, 0.03, 0.9),
                        TextBox(str(card[1]), SHOP_X[k] + 0.05, y, 0.01, 0.03, 0.9)]
        return out


def executor(shop):
    ex = PrepareExecutor(shop, shop.boxes, raw=lambda a, b: "", sleep=lambda s: None)
    ex.roster = lambda: COSTS
    ex._gold = lambda: shop.gold
    return ex


def run(shop, board=None, **roll):
    ex = executor(shop)
    out = ex.run([Action(type="roll", **roll), Action(type="ready")], shop.boxes(), board or [])
    assert out.observe and not out.ready and len(out.results) == 1     # roll 之后这一轮的其他操作不做
    return out.results[0]


def test_roll_until_target_found():
    rounds = [[("火花", 1), ("景元", 1), None, None, None],
              [("景元", 1), None, ("流萤", 2), None, None],
              [("那刻夏", 1), ("那刻夏", 1), ("火花", 1), None, None]]
    shop = Shop(rounds, gold=30)
    text = run(shop, targets=[{"name": "那刻夏"}])
    assert "刷新 2 次" in text and "买到 那刻夏×1" in text and "目标达成" in text and shop.gold == 30 - 4 - 1


def test_roll_to_two_stars_counts_owned_copies():
    """场上已有 1 张、备战席 1 张,凑 2 星只差 1 张;第三轮那两张都不多买。"""
    board = [Slot("front", 0, 0.4, 0.4, name="那刻夏", score=0.9, stars=1),
             Slot("bench", 0, 0.2, 0.8, name="那刻夏", score=0.9, stars=1)]
    rounds = [[("火花", 1), None, None, None, None], [("那刻夏", 1), ("那刻夏", 1), None, None, None]]
    shop = Shop(rounds, gold=20)
    text = run(shop, board, targets=[{"name": "那刻夏", "stars": 2}])
    assert "买到 那刻夏×1" in text and "目标达成" in text and "那刻夏 现有一星的量 3 张" in text


def test_roll_stops_at_refresh_limit_and_keeps_gold():
    rounds = [[("火花", 1), None, None, None, None]] * 30
    shop = Shop(rounds, gold=40)
    text = run(shop, targets=[{"name": "黄泉"}], max_refresh=5)
    assert "刷新 5 次" in text and "上限" in text and shop.gold == 30
    shop = Shop(rounds, gold=40)
    text = run(shop, targets=[{"name": "黄泉"}], keep_gold=33, max_refresh=20)
    assert "刷新 3 次" in text and "低于保底 33 金" in text and shop.gold == 34


def test_roll_any_and_full_bench():
    rounds = [[None, None, None, None, None], [("流萤", 2), ("景元", 1), None, None, None]]
    shop = Shop(rounds, gold=20)
    text = run(shop, targets=[{"name": "景元"}, {"name": "黄泉"}], until="any")
    assert "买到 景元×1" in text and "目标达成" in text
    shop = Shop(rounds, gold=20, bench_free=0)
    text = run(shop, targets=[{"name": "景元"}])
    assert "买不进" in text and "没买到目标" in text


def test_roll_needs_targets():
    import pytest

    with pytest.raises(ValueError):
        Action(type="roll", targets=[])


def test_roll_target_names_matched_to_known_roles():
    """模型写错字(2026-11-20 写成「刻律德拉」)时按最像的角色找并说明;对不上任何角色的不找。"""
    rounds = [[("那刻夏", 1), None, None, None, None]]
    shop = Shop(rounds, gold=10)
    text = run(shop, targets=[{"name": "那刻下"}, {"name": "张三"}])
    assert "没有叫「那刻下」的角色,按「那刻夏」找" in text and "没有叫「张三」的角色,不找" in text
    assert "买到 那刻夏×1" in text and "目标达成" in text
    shop = Shop(rounds, gold=10)
    text = run(shop, targets=[{"name": "张三"}])
    assert text.endswith("没有可找的目标") and shop.clicks == 0


class FoldedShop(Shop):
    """商店其实已经收起(前面装备、合成时被收的),模型决策时那一帧还是展开的。"""

    def __init__(self, rounds, gold):
        super().__init__(rounds, gold)
        self.open = False

    def click(self, x, y, button="left"):
        if abs(x - 0.845) < 0.02 and abs(y - 0.909) < 0.02:
            self.open = not self.open
            return
        if self.open:
            super().click(x, y, button)

    def boxes(self, image=None):
        return super().boxes() if self.open else [TextBox("商店", 0.845, 0.909, 0.04, 0.03, 0.9)]


def test_roll_reads_the_shop_state_from_a_fresh_frame():
    """2026-11-20 那局装备、合成之后 roll 拿着决策时的画面,以为商店开着直接按 D,一次没刷就停了。"""
    rounds = [[("火花", 1), None, None, None, None], [("那刻夏", 1), None, None, None, None]]
    shop = FoldedShop(rounds, gold=10)
    stale = Shop(rounds, gold=10).boxes()            # 展开态的旧画面
    out = executor(shop).run([Action(type="roll", targets=[{"name": "那刻夏"}])], stale, [])
    assert shop.open and "买到 那刻夏×1" in out.results[0] and "刷新 1 次" in out.results[0]
