from pathlib import Path

import pytest
from PIL import Image

from cwagent.vision.ocr import Ocr
from cwagent.vision.shop import read_prices

FIXTURE = Path(__file__).parent / "fixtures" / "shop" / "prices-1-4.jpg"


def test_read_prices_from_coin_icons():
    """2026-11-16 Codex 那局 1-4 的商店(对局记录缩到 960 宽):价格是金币图标旁的单个数字,整屏识别一个都没认出。"""
    prices = read_prices(Image.open(FIXTURE), Ocr())
    assert prices == {1: 3, 2: 3, 3: 2, 4: 2, 5: 2}    # 符玄 3、赛飞儿 3、万敌 2、云璃 2、娜塔莎 2


def test_read_item_bar():
    """同一张图右上角道具栏:拆装扳手 ×2(最右)、冶金炉 ×1。"""
    from cwagent.vision.items import describe, read_items

    items = read_items(Image.open(FIXTURE), Ocr())
    assert [(it.slot, it.name, it.count) for it in items] == [(1, "拆装扳手", 2), (2, "冶金炉", 1)]
    assert describe(items).endswith("拆装扳手×2(0.961,0.152)、冶金炉×1(0.921,0.152)")


def test_consumable_catalog():
    from cwagent.vision.items import catalog

    c = catalog()
    assert len(c) == 18 and c[350105]["params"] == [3, 1] and "3费及以下" in c[350105]["desc"]


@pytest.mark.parametrize("size", [(960, 540), (1920, 1080)])
def test_single_coin_prices_and_projector_count(size):
    """1-1 实机图:五张 1 金币卡、实习生投影仪 ×2、拆装扳手 ×1。"""
    from cwagent.vision.items import read_items

    image = Image.open(FIXTURE.with_name("prices-1-1.jpg")).resize(size)
    ocr = Ocr()
    assert read_prices(image, ocr) == {i: 1 for i in range(1, 6)}
    assert [(it.name, it.count) for it in read_items(image, ocr)] == [("实习生投影仪", 2), ("拆装扳手", 1)]
