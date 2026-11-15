"""参考 M7A S2 版补的感知:新界面、商店、上场人数、卡面、详情面板、开拓者名字。"""

from pathlib import Path

import numpy as np
from PIL import Image

from cwagent.orchestrator.screens import classify, role_of
from cwagent.vision import cards
from cwagent.vision.board import Portraits, detail_name
from cwagent.vision.hud import parse_deployed
from cwagent.vision.ocr import Aliased, TextBox
from cwagent.vision.shop import format_shop, read_shop, two_star


def _boxes(*items):
    return [TextBox(t, x, y, 0.05, 0.02, 0.9) for t, x, y in items]


# 2026-11-14 实机:开拓商店盖在备战界面上(背景里还有「备战阶段」,左侧羁绊列表里有「开拓银轨」)
RAIL = [("备战阶段", 0.168, 0.065), ("【开拓商店】", 0.545, 0.069), ("2-4", 0.168, 0.100), ("0", 0.332, 0.103),
        ("开拓银轨", 0.080, 0.386), ("星穹列车，跃迁！", 0.527, 0.352), ("详情", 0.550, 0.409),
        ("出战", 0.947, 0.694), ("购买经验", 0.155, 0.799), ("商店", 0.845, 0.909)]
DANCE = [("备战阶段", 0.17, 0.07), ("梦境交织", 0.55, 0.07), ("选择2个角色进行共舞，为其增加战斗内连携效果", 0.55, 0.12),
         ("黑天鹅", 0.48, 0.33), ("长夜月", 0.61, 0.33), ("详情", 0.49, 0.38), ("确认选择", 0.78, 0.53),
         ("出战", 0.95, 0.69), ("购买经验", 0.15, 0.80), ("商店", 0.84, 0.91)]


def test_overlay_screens():
    assert classify(_boxes(*RAIL)) == "rail_shop" and role_of("rail_shop") == "chooser"
    assert classify(_boxes(*DANCE)) == "dance"
    assert classify(_boxes(("主角光环与奇遇详情", 0.35, 0.06), ("重选主角", 0.43, 0.66))) == "spotlight_info"
    # 左侧羁绊列表里的「奇兽花园」「开拓银轨」不算面板标题
    board = [("备战阶段", 0.17, 0.07), ("奇兽花园", 0.08, 0.46), ("开拓银轨", 0.08, 0.54), ("出战", 0.95, 0.69)]
    assert classify(_boxes(*board)) == "prepare"
    assert classify(_boxes(("提示", 0.5, 0.38), ("失去网络连接，战斗结果同步失败，是否尝试重新连接？", 0.5, 0.48),
                           ("确认", 0.61, 0.62))) == "reconnect"
    assert classify(_boxes(("前台区域无角色，是否继续？", 0.5, 0.45), ("确认", 0.53, 0.66))) == "empty_front"
    assert classify(_boxes(("获得物品", 0.5, 0.2), ("点击空白处关闭", 0.5, 0.9))) == "tap_close"
    assert classify(_boxes(("本局不再提示", 0.5, 0.55), ("确认", 0.53, 0.66))) == "deploy_confirm"


def test_shop_merged_price_and_two_star():
    boxes = [TextBox("那刻夏1", 0.255, 0.28, 0.06, 0.02, 0.9),
             TextBox("银狼LV.9993", 0.396, 0.28, 0.06, 0.02, 0.9),     # 名字末尾本身带数字
             TextBox("银狼LV.999", 0.535, 0.28, 0.06, 0.02, 0.9), TextBox("9", 0.58, 0.28, 0.02, 0.02, 0.9)]
    cards_ = read_shop(boxes)
    assert cards_[0] == (1, "那刻夏", 1) and cards_[1] == (2, "银狼LV.999", 3) and cards_[2] == (3, "银狼LV.999", 9)
    costs = {"银狼LV.999": {3}, "星神★阿哈": {4, 5, 7}}
    assert two_star("银狼LV.999", 9, costs) and not two_star("银狼LV.999", 3, costs)
    assert not two_star("星神★阿哈", 5, costs)            # 5 费版本本身的价格,不是二星
    assert "3号 银狼LV.999 2星(9 金币)" in format_shop(cards_, costs)


def test_parse_deployed():
    assert parse_deployed("i3/5久") == "3/5"
    assert parse_deployed("12/3") == "2/3"              # 左边的小人图标被认成 1
    assert parse_deployed("11/11") == "11/11"
    assert parse_deployed("7/6") is None and parse_deployed("7/6", over=True) == "7/6"
    assert parse_deployed("9/94") is None


def test_card_cost_uses_bottom_strip():
    card = np.zeros((130, 113, 3), np.uint8)
    card[:] = (90, 60, 50)
    card[100:110, 10:100] = (148, 131, 128)             # 立绘里的灰白色衣服(像 1 费的灰)
    card[118:126] = (121, 130, 44)                      # 最下面的绿色色条:2 费(BGR)
    assert cards.card_cost(card) == 2
    card[118:126] = (79, 170, 209)
    assert cards.card_cost(card) == 5


def test_star_colors():
    card = np.zeros((130, 113, 3), np.uint8)
    for x in (40, 60):
        card[100:106, x:x + 6] = (255, 240, 200)        # 蓝星(BGR):选了主角光环
    assert cards.star_colors(card) == (0, 2)
    card[:] = 0
    card[100:106, 50:56] = (120, 230, 255)              # 金星
    assert cards.star_colors(card) == (1, 0)


def test_detail_name_and_learning(tmp_path: Path):
    boxes = _boxes(("试用", 0.765, 0.189), ("那刻夏", 0.798, 0.213), ("前台", 0.807, 0.250), ("输出", 0.953, 0.250),
                   ("出售", 0.922, 0.847))
    assert detail_name(boxes) == "那刻夏"
    assert detail_name(_boxes(("备战阶段", 0.17, 0.07))) is None
    p = Portraits(tmp_path, {"1": "那刻夏", "2": "银狼LV.999"})
    assert p.role_of("银狼LV.999") == "2" and p.role_of("露") is None
    rng = np.random.default_rng(0)
    card = (rng.random((129, 113, 3)) * 255).astype(np.uint8)
    card = np.asarray(Image.fromarray(card).resize((28, 32)).resize((113, 129)))   # 有结构的「立绘」
    assert p.identify(card)[0] is None
    p.learn_card(card, "1")
    rid, score = p.identify(card)
    assert rid == "1" and score > 0.9
    assert Portraits(tmp_path, {"1": "那刻夏"}).identify(card)[0] == "1"    # 存下来了,下次也认得


def test_trailblazer_alias():
    ocr = Aliased(lambda image, region=None: [TextBox("64673", 0.36, 0.28, 0.05, 0.02, 0.9),
                                              TextBox("UID:103175008", 0.05, 0.97, 0.1, 0.02, 0.9)], {"64673": "开拓者"})
    assert [b.text for b in ocr(None)] == ["开拓者", "UID:103175008"]
