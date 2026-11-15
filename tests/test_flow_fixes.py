"""2026-11-15 实机对局暴露的流程问题:羁绊商店入口被当成弹窗、投资卡没翻完就识别、拖动被拒绝没察觉、敌人词条。"""

from PIL import Image

from cwagent.brain.screen_agent import Action
from cwagent.capture import Frame
from cwagent.knowledge.affixes import AffixBook
from cwagent.orchestrator.loop import overlay_texts
from cwagent.orchestrator.macros import read_affixes
from cwagent.orchestrator.prepare import PrepareExecutor
from cwagent.orchestrator.screens import investment_cards, trait_shop
from cwagent.vision.board import Slot
from cwagent.vision.ocr import TextBox


def _b(text, x, y):
    return TextBox(text, x, y, 0.05, 0.02, 0.9)


def test_trait_shop_entry_is_not_a_popup():
    boxes = [_b("打开商店", 0.328, 0.363), _b("×0", 0.36, 0.38), _b("有商品可购买", 0.30, 0.40),
             _b("当前情况下不可调整", 0.50, 0.50)]
    assert overlay_texts(boxes) == ["当前情况下不可调整"]
    assert "羁绊专属商店" in trait_shop(boxes) and "有商品可购买" in trait_shop(boxes)


def test_investment_cards():
    # 2026-11-15 实机:第三张投资环境还在翻牌,只认出半句效果说明
    early = [_b("过剩经费", 0.228, 0.363), _b("小小巨人邀请", 0.500, 0.358), _b("个投资农略是棱彩品质。", 0.780, 0.389)]
    assert investment_cards(early, "environment_choice") == 2
    done = early[:2] + [_b("连击邀请", 0.771, 0.363)]
    assert investment_cards(done, "environment_choice") == 3
    strategy = [_b("公司军火更新•金", 0.240, 0.453), _b("终结加演", 0.500, 0.453), _b("全武行", 0.760, 0.454)]
    assert investment_cards(strategy, "strategy_choice") == 3


class AffixDevice:
    """位面详情:点词条弹出说明,点左边空白收起。"""

    def __init__(self):
        self.popup = None
        self.clicks = []

    def grab(self):
        return Frame(image=Image.new("RGB", (8, 8)), timestamp=0)

    def click(self, x, y, *, button="left"):
        self.clicks.append((round(x, 3), round(y, 3)))
        self.popup = {0.461: "前台熄火"}.get(round(x, 3), self.popup) if y > 0.8 else None
        if round(x, 3) == 0.734:
            self.popup = "前台熄火"


def test_read_affixes_records_new_ones(tmp_path):
    book = AffixBook(public=tmp_path / "public.yaml", local=tmp_path / "local.yaml")
    book.record("开局不利", "开局时小队生命降低。")
    dev = AffixDevice()
    row = [_b("位面详情", 0.08, 0.07), _b("敌人难度67+3", 0.324, 0.919), _b("开局不利", 0.557, 0.92),
           _b("前台熄火", 0.734, 0.921)]

    def ocr(image, region=None):
        if dev.popup:
            return row + [_b("前台角色熄火,", 0.70, 0.80), _b("只能造成1点伤害。", 0.70, 0.83)]
        return row

    got = read_affixes(dev, ocr, row, book, log=lambda m: None)
    assert got == [("开局不利", "开局时小队生命降低。"), ("前台熄火", "前台角色熄火,只能造成1点伤害。")]
    assert dev.clicks[0] == (0.734, 0.921)          # 已知的「开局不利」不再点开
    assert book.get("前台熄火") == "前台角色熄火,只能造成1点伤害。"


class DragDevice:
    def __init__(self, refused):
        self.refused, self.drags = refused, []

    def grab(self):
        return Frame(image=Image.new("RGB", (8, 8)), timestamp=0)

    def drag(self, *a, **k):
        self.drags.append(a)


def test_refused_move_stops_the_turn():
    dev = DragDevice(refused=True)
    ocr = lambda image: [_b("当前情况下不可调整", 0.5, 0.5)]
    ex = PrepareExecutor(dev, ocr, raw=lambda a, b: "", sleep=lambda s: None)
    board = [Slot("bench", 0, 0.2285, 0.845, name="刻律德菈", score=0.9)]
    out = ex.run([Action(type="move", zone="bench", index=1, to_zone="back", to_index=2),
                  Action(type="sell", zone="bench", index=2), Action(type="ready")], [], board, final=True)
    assert out.results == ["备战席1号 → 后台2号:没成功(界面提示「当前情况下不可调整」),后面的操作先不做"]
    assert out.observe and not out.ready and len(dev.drags) == 1
