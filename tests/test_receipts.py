"""备战操作后看回执(2026-11-20):每隔 POLL 秒看一眼,预期的变化出现就走,不再固定等再等画面静止。"""

from pathlib import Path

from PIL import Image

from cwagent.brain.screen_agent import Action
from cwagent.capture import Frame
from cwagent.orchestrator.prepare import MOVE_TRIES, POLL, PrepareExecutor
from cwagent.vision.board import BENCH_SIZE, slots
from cwagent.vision.ocr import TextBox
from tests.test_roll import Shop, executor

BOARD = Path(__file__).parent / "fixtures" / "board"
COUNTS = {"front": 4, "back": 6}


class Counting(Shop):
    """记下识别了几次。"""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.reads = 0

    def boxes(self, image=None):
        self.reads += 1
        return super().boxes()


def test_buy_moves_on_once_the_card_is_gone():
    shop = Counting([[("火花", 1), ("景元", 1), None, None, None]], gold=10)
    slept = []
    ex = executor(shop)
    ex.sleep = slept.append
    out = ex.run([Action(type="buy", slot=2)], shop.boxes(), [])
    assert out.results == ["买商店第 2 格(景元)"]
    assert sum(slept) <= 2 * POLL and shop.reads <= 4       # 原来:睡 0.5 秒 + 等画面静止 + 整屏识别


class Animated(Counting):
    """刷新后前两眼卡片还在翻(读不出名字),之后才是新的五张。"""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.hidden = 0

    def click(self, x, y, button="left"):
        before = self.i
        super().click(x, y, button)
        if self.i != before:
            self.hidden = 2

    def boxes(self, image=None):
        if self.hidden:
            self.hidden -= 1
            self.reads += 1
            return [b for b in Shop.boxes(self) if b.y > 0.4]   # 只剩按钮和刷新价
        return super().boxes()


def test_refresh_waits_until_the_new_cards_read_the_same_twice():
    shop = Animated([[("火花", 1), None, None, None, None], [None, None, ("黄泉", 4), None, None]], gold=10)
    ex = executor(shop)
    out = ex.run([Action(type="roll", targets=[{"name": "黄泉", "count": 1}], until="all", max_refresh=3)],
                 shop.boxes(), [])
    assert "刷新 1 次" in out.results[0] and "黄泉×1" in out.results[0]


class Board:
    """真尺寸的棋盘画面:拖完以后目标格出现卡面。"""

    def __init__(self):
        self.before = Image.open(BOARD / "deployed-9-9.jpg").convert("RGB").resize((1920, 1080))
        self.after = self.before.copy()
        bench = next(s for s in slots(False, COUNTS) if (s.zone, s.index) == ("bench", 0))
        back = next(s for s in slots(False, COUNTS) if (s.zone, s.index) == ("back", 5))
        w, h = round(BENCH_SIZE[0] * 1920), round(BENCH_SIZE[1] * 1080)
        card = self.before.crop((round(bench.x * 1920 - w / 2), round(bench.y * 1080 - h / 2),
                                 round(bench.x * 1920 + w / 2), round(bench.y * 1080 + h / 2)))
        self.after.paste(card.resize((round(0.066 * 1920), round(0.120 * 1080))),
                         (round(back.x * 1920 - 0.033 * 1920), round(back.y * 1080 - 0.060 * 1080)))
        self.image = self.before
        self.grabs = 0
        self.drags = []

    def drag(self, *a, **k):
        self.drags.append(a)
        self.image = self.after

    def grab(self):
        self.grabs += 1
        return Frame(image=self.image, timestamp=0)


def test_move_is_confirmed_from_pixels_without_reading_text():
    game = Board()
    reads = []
    ex = PrepareExecutor(game, lambda image, region=None: reads.append(1) or [], raw=lambda a, b: "",
                         sleep=lambda s: None)
    ex.counts = dict(COUNTS)
    out = ex.run([Action(type="move", zone="bench", index=1, to_zone="back", to_index=6)], [], None)
    assert out.results == ["备战席1号 → 后台6号"] and len(game.drags) == 1
    assert game.grabs <= 3 and not reads          # 拖前一张、拖后一张就确认了,没有整屏识别


def test_move_falls_back_to_reading_the_screen_when_nothing_lands():
    game = Board()
    game.after = game.before                      # 拖了没反应
    refused = [TextBox("当前情况下不可调整", 0.5, 0.45, 0.2, 0.03, 0.9)]
    ex = PrepareExecutor(game, lambda image, region=None: refused, raw=lambda a, b: "", sleep=lambda s: None)
    ex.counts = dict(COUNTS)
    out = ex.run([Action(type="move", zone="bench", index=1, to_zone="back", to_index=6)], [], None)
    assert "当前情况下不可调整" in out.results[0] and out.observe
    assert game.grabs >= MOVE_TRIES                # 回执一直没来:看满了再按原来的方式核对


def test_swap_into_an_occupied_slot_is_confirmed_when_the_card_changes():
    """目标格本来就有人(换位):只看「有没有卡」看不出来,改看那一格的卡面换没换。"""
    from cwagent.vision.board import slot_changed

    game = Board()
    front = next(s for s in slots(False, COUNTS) if (s.zone, s.index) == ("front", 0))
    back = next(s for s in slots(False, COUNTS) if (s.zone, s.index) == ("back", 0))
    game.after = game.before.copy()
    card = game.before.crop((round((back.x - 0.031) * 1920), round((back.y - 0.058) * 1080),
                             round((back.x + 0.031) * 1920), round((back.y + 0.058) * 1080)))
    game.after.paste(card, (round((front.x - 0.031) * 1920), round((front.y - 0.058) * 1080)))
    assert slot_changed(game.before, game.after, ("front", 0), False, COUNTS)
    assert not slot_changed(game.before, game.before.copy(), ("front", 0), False, COUNTS)
    ex = PrepareExecutor(game, lambda image, region=None: [], raw=lambda a, b: "", sleep=lambda s: None)
    ex.counts = dict(COUNTS)
    out = ex.run([Action(type="move", zone="back", index=1, to_zone="front", to_index=1)], [], None)
    assert out.results == ["后台1号 → 前台1号"] and game.grabs <= 3
