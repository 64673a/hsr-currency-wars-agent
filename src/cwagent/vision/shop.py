"""商店展开时读出 5 张卡片的名字和价格(从界面文字里按位置分格)。"""

from __future__ import annotations

import re

from .board import SHOP_NAME_Y, SHOP_X
from .ocr import TextBox

HALF_CARD = 0.065


def read_shop(boxes: list[TextBox]) -> list[tuple[int, str | None, int | None]]:
    """[(格子 1~5, 名字, 价格)];已买走的格子名字为 None。"""
    y0, y1 = SHOP_NAME_Y[0] - 0.01, SHOP_NAME_Y[1] + 0.01
    out = []
    for i, cx in enumerate(SHOP_X, start=1):
        cell = sorted((b for b in boxes if y0 <= b.y <= y1 and abs(b.x - cx) <= HALF_CARD), key=lambda b: b.x)
        names = [b.text for b in cell if not re.fullmatch(r"\d+", b.text)]
        prices = [int(b.text) for b in cell if re.fullmatch(r"\d{1,2}", b.text)]
        out.append((i, names[0] if names else None, prices[-1] if prices else None))
    return out


def format_shop(cards: list[tuple[int, str | None, int | None]]) -> str:
    return ";".join(f"{i}号 {name}" + (f"({price} 金币)" if price is not None else "") if name else f"{i}号 (空)"
                    for i, name, price in cards)
