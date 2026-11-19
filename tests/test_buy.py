"""buy 的核对:点卡后商店被收起时按金币判断买没买到(2026-11-19 那局 1-6、1-7、2-2 三次误报买到)。"""

from cwagent.brain.screen_agent import Action
from cwagent.vision.ocr import TextBox
from tests.test_roll import Shop, executor


class FoldingShop(Shop):
    """假游戏:前 fold 次点卡时商店被收起、没买到;收起后点右下「商店」重新打开。"""

    def __init__(self, rounds, gold, *, fold=0, pay_then_fold=False, open_=True):
        super().__init__(rounds, gold)
        self.fold, self.pay_then_fold, self.open = fold, pay_then_fold, open_

    def click(self, x, y, button="left"):
        if not self.open:
            if abs(x - 0.845) < 0.03 and abs(y - 0.909) < 0.03:
                self.open = True
            return
        if abs(y - 0.15) < 0.02 and self.fold:
            self.fold -= 1
            self.open = False
            return
        super().click(x, y, button)
        if self.pay_then_fold and abs(y - 0.15) < 0.02:
            self.open = False

    def boxes(self, image=None):
        if self.open:
            return super().boxes()
        return [TextBox("商店", 0.845, 0.909, 0.04, 0.03, 0.9)]


def buy(shop, boxes=None):
    ex = executor(shop)
    out = ex.run([Action(type="buy", slot=2)], boxes if boxes is not None else shop.boxes(), [])
    return out.results


def test_buy_retries_once_when_the_shop_folds_without_paying():
    shop = FoldingShop([[("火花", 1), ("景元", 1), None, None, None]], gold=10, fold=1)
    assert buy(shop) == ["买商店第 2 格(景元)"]
    assert shop.gold == 9 and shop.cards[1] is None


def test_buy_reports_failure_when_the_shop_keeps_folding():
    shop = FoldingShop([[("火花", 1), ("景元", 1), None, None, None]], gold=10, fold=5)
    [text] = buy(shop)
    assert text.startswith("买商店第 2 格(景元):") and "没买到" in text and shop.gold == 10


def test_buy_counts_when_paid_even_if_the_shop_folds():
    shop = FoldingShop([[("火花", 1), ("景元", 1), None, None, None]], gold=10, pay_then_fold=True)
    assert buy(shop) == ["买商店第 2 格(景元)"] and shop.gold == 9


def test_buy_looks_again_when_the_decision_frame_is_stale():
    """模型看到的是商店开着的画面,执行时商店已经收起:先按眼前的画面打开商店再买。"""
    shop = FoldingShop([[("火花", 1), ("景元", 1), None, None, None]], gold=10)
    stale = shop.boxes()
    shop.open = False
    assert buy(shop, stale) == ["买商店第 2 格(景元)"] and shop.gold == 9
