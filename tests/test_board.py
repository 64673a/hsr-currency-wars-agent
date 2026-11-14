from cwagent.vision.board import Slot, format_board, shop_is_open, slots
from cwagent.vision.hud import Hud
from cwagent.vision.ocr import TextBox


def test_slots_and_format():
    assert len(slots()) == 4 + 6 + 9 and len(slots(shop_open=True)) == 19
    assert slots()[0].y != slots(shop_open=True)[0].y and slots()[-1].y == slots(shop_open=True)[-1].y
    board = [Slot("front", 0, 0.3885, 0.372, name="万敌", role_id="1", score=0.7, stars=2),
             Slot("front", 1, 0.463, 0.372),
             Slot("bench", 0, 0.2285, 0.845, score=0.4, stars=1)]
    text = format_board(board)
    assert "1号 万敌 2星 (0.389,0.372)" in text and "2号(空)" in text and "1号 认不出 1星" in text


def test_shop_open_and_hud_text():
    assert shop_is_open([TextBox("收起", 0.845, 0.908, 0.02, 0.02, 0.9)])
    assert not shop_is_open([TextBox("商店", 0.845, 0.908, 0.02, 0.02, 0.9)])
    assert Hud(node="1-3", hp=60, gold=None, level=4).text() == "节点 1-3,小队生命 60,金币 认不出,等级 4"
