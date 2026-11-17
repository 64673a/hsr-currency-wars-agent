from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

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


def test_battle_summary():
    from cwagent.vision.battle import read_progress, summarize

    assert read_progress([TextBox("1-4 40%", 0.4, 0.03, 0.05, 0.02, 0.9)]) == 40
    assert read_progress([TextBox("40%", 0.9, 0.5, 0.05, 0.02, 0.9)]) is None
    text = summarize([(164, 0), (164, 10), (150, 20), (110, 30), (None, 45), (40, 60), (0, 70)])
    assert text == "进度 0% → 最后读到 70%;行动值 164 → 最后读到 40"
    # 单次误读(实机「104 → 6 → 90」)不影响首尾,也不报「骤降」
    text = summarize([(104, None), (6, None), (90, None), (6, None), (69, None), (63, None), (35, None)])
    assert text == "行动值 90 → 最后读到 63" and "阵亡" not in text


def test_avatar_roles_skins_and_paths():
    from cwagent.vision.board import avatar_roles

    names = {"150102": "火花", "151302": "砂金•戏浪", "130402": "砂金", "800902": "开拓者", "122402": "三月七",
             "131002": "流萤"}
    m7a = {"Sparxie": "火花", "SparxieCandyCoatedWaves": "火花·甜梦电波", "AventurineWaveflair": "砂金·戏浪",
           "Aventurine": "砂金", "StelleElation": "星（欢愉）", "CaelumDestruction": "穹（毁灭）",
           "March7thImaginary": "三月七（虚数）", "FireflySpringMissive": "流萤「春日手信」", "Seele": "希儿"}
    assert avatar_roles(m7a, names) == {
        "Sparxie": "150102", "SparxieCandyCoatedWaves": "150102", "AventurineWaveflair": "151302",
        "Aventurine": "130402", "StelleElation": "800902", "CaelumDestruction": "800902",
        "March7thImaginary": "122402", "FireflySpringMissive": "131002"}


def test_identify_by_m7a_avatar(tmp_path):
    import json

    import cv2
    import numpy as np
    from PIL import Image

    from cwagent.vision.board import AVATAR_CENTER, AVATAR_SCALE, AVATAR_SIDE, Portraits

    rng = np.random.default_rng(0)

    def texture(h, w):
        return cv2.GaussianBlur(rng.integers(0, 255, (h, w, 3), dtype=np.uint8), (5, 5), 0)

    folder = tmp_path / "avatars"
    folder.mkdir()
    skin, other = texture(100, 100), texture(100, 100)
    Image.fromarray(skin).save(folder / "SparxieCandyCoatedWaves.png")
    Image.fromarray(other).save(folder / "Pearl.png")
    (folder / "names.json").write_text(json.dumps({"SparxieCandyCoatedWaves": "火花·甜梦电波", "Pearl": "真珠"}),
                                       encoding="utf-8")
    p = Portraits(tmp_path, {"150102": "火花", "150302": "真珠"})
    assert sorted(r for r, _ in p.avatars) == ["150102", "150302"]

    # 卡面:皮肤头像的中央按卡宽缩放后贴在实测位置
    card = np.full((129, 113, 3), 40, np.uint8)
    face = cv2.cvtColor(skin[18:18 + AVATAR_SIDE, 18:18 + AVATAR_SIDE], cv2.COLOR_RGB2BGR)
    k = 113 * AVATAR_SCALE
    face = cv2.resize(face, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    y, x = int(AVATAR_CENTER[1] * 129 - face.shape[0] / 2), int(AVATAR_CENTER[0] * 113 - face.shape[1] / 2)
    card[y:y + face.shape[0], x:x + face.shape[1]] = face
    rid, score = p.identify(card)
    assert rid == "150102" and score > 0.95


@pytest.mark.parametrize("name, shop_open, back", [
    ("back7.jpg", False, 7), ("back7-shop.jpg", True, 7), ("back6.jpg", False, 6), ("back6-shop.jpg", True, 6)])
def test_back_count(name, shop_open, back):
    """实机截图(960x540,只留棋盘一带):后台有 6 格的局,也有 7 格的局。界面数字读不出时按画面核对奇偶。"""
    from cwagent.vision.board import back_count, back_hint

    crop = Image.open(Path(__file__).parent / "fixtures" / "board" / name).convert("RGB")
    frame = Image.new("RGB", (960, 540))
    frame.paste(crop, (int(0.2 * 960), int(0.3 * 540)))
    img = cv2.cvtColor(np.asarray(frame.resize((1920, 1080))), cv2.COLOR_RGB2BGR)
    assert back_count(img, shop_open, None, 6) == back          # 上次以为 6 格
    assert back_count(img, shop_open, None, 7) == back          # 上次以为 7 格
    assert back_count(img, shop_open, back, 6) == back
    assert back_hint(4, "5/5") == 7 and back_hint(5, "5/5") == 6 and back_hint(None, "5/5") is None
    assert len([s for s in slots(shop_open, {"back": back}) if s.zone == "back"]) == back


def test_lifted_card_star_count():
    """2026-11-17 实机:选中抬起的 1 星那刻夏,下面露出的装备图标上沿是金色细条,原来被数成 3 星(这张是缩略图放大的)。"""
    from pathlib import Path

    import cv2

    from cwagent.vision.cards import star_colors

    card = cv2.imread(str(Path(__file__).parent / "fixtures" / "board" / "lifted-card-1star.png"))
    assert star_colors(card) == (1, 0)
