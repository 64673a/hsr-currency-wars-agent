"""商店展开时读出 5 张卡片的名字和价格(从界面文字里按位置分格)。

OCR 可能把名字和价格合成一段(如「那刻夏1」),也可能拆成两段(M7A S2 资料)。名字末尾本身可以带数字
(「银狼LV.999」),所以合成的那段按 M7A 的写法拆:名字后面可以有 1~3 位数字,最后一位 1~5 才是价格。
S2 的商店会刷出二星角色卡,价格按三张一星卡算:价格是角色费用的 3 倍就是二星(维护者)。
"""

from __future__ import annotations

import re

from .board import SHOP_NAME_Y, SHOP_X
from .ocr import TextBox, find

HALF_CARD = 0.065
NAME = r"[\u3400-\u9fffA-Za-z&＆·•.★☆]{2,18}(?:\d{1,3})?"   # M7A 的角色名写法


def shop_state(boxes: list[TextBox]) -> bool | None:
    """商店是否展开:True / False;被角色详情等遮住、看不出来时返回 None。

    展开时右下按钮是「收起」、右侧有「刷新」、中间有一行抽到各费角色的概率(2026-11-14 实测);
    收起时右下按钮是「商店」。
    """
    if (find(boxes, "^收起$", region=(0.78, 0.86, 0.14, 0.08)) or find(boxes, "^刷新$", region=(0.78, 0.40, 0.12, 0.10))
            or sum(1 for b in boxes if 0.33 <= b.y <= 0.39 and 0.45 <= b.x <= 0.75 and re.fullmatch(r"\d{1,2}%", b.text)) >= 3):
        return True
    if find(boxes, "^商店$", region=(0.78, 0.86, 0.14, 0.08)):
        return False
    return None


def _split(text: str) -> tuple[str, int | None]:
    """「那刻夏1」→(那刻夏, 1);「银狼LV.9993」→(银狼LV.999, 3);没有合进价格的原样返回。"""
    m = re.fullmatch(f"({NAME})([1-5])", text)
    return (m.group(1), int(m.group(2))) if m else (text, None)


def read_shop(boxes: list[TextBox]) -> list[tuple[int, str | None, int | None]]:
    """[(格子 1~5, 名字, 价格)];已买走的格子名字为 None。"""
    y0, y1 = SHOP_NAME_Y[0] - 0.01, SHOP_NAME_Y[1] + 0.01
    out = []
    for i, cx in enumerate(SHOP_X, start=1):
        cell = sorted((b for b in boxes if y0 <= b.y <= y1 and abs(b.x - cx) <= HALF_CARD), key=lambda b: b.x)
        names = [b.text for b in cell if not re.fullmatch(r"\d+", b.text)]
        prices = [int(b.text) for b in cell if re.fullmatch(r"\d{1,2}", b.text)]
        name, price = (names[0] if names else None), (prices[-1] if prices else None)
        if name is not None and price is None:
            name, price = _split(name)
        out.append((i, name, price))
    return out


def two_star(name: str, price: int | None, costs: dict[str, set[int]]) -> bool:
    """价格是角色费用的 3 倍(又不等于它的某个费用,如星神★阿哈有 4、5、7 费三个版本)就是二星卡。"""
    c = costs.get(re.sub(r"[·•・]", "·", name), set())
    return price is not None and price not in c and any(price == 3 * x for x in c)


def format_shop(cards: list[tuple[int, str | None, int | None]], costs: dict[str, set[int]] | None = None) -> str:
    def one(i: int, name: str | None, price: int | None) -> str:
        if not name:
            return f"{i}号 (空)"
        star = " 2星" if costs and two_star(name, price, costs) else ""
        return f"{i}号 {name}{star}" + (f"({price} 金币)" if price is not None else "")

    return ";".join(one(*c) for c in cards)
