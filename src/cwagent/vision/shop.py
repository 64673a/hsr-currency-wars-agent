"""商店展开时读出 5 张卡片的名字和价格(从界面文字里按位置分格)。

OCR 可能把名字和价格合成一段(如「那刻夏1」),也可能拆成两段(M7A S2 资料)。名字末尾本身可以带数字
(「银狼LV.999」),所以合成的那段按 M7A 的写法拆:名字后面可以有 1~3 位数字,最后一位 1~5 才是价格。
S2 的商店会刷出二星角色卡,价格按三张一星卡算:价格是角色费用的 3 倍就是二星(维护者)。
"""

from __future__ import annotations

import re

import cv2
import numpy as np
from PIL import Image

from .board import SHOP_NAME_Y, SHOP_X
from .ocr import Ocr, TextBox, find

HALF_CARD = 0.065
NAME = r"[\u3400-\u9fffA-Za-z&＆·•.★☆]{2,18}(?:\d{1,3})?"   # M7A 的角色名写法


# 刷新按钮上的字:有免费刷新时写的是「免费刷新」。原来只认「刷新」两个字,免费刷新时判定不出来,
# 商店明明开着却被当成收起着,出战/选择主角就点在了商店面板上(2026-11-16 实机,1-9 连着 4 轮推进不了)。
# M7A 的 SHOP_REFRESH_LABELS 也是这两个:'刷新'、'免费刷新'。
REFRESH_LABEL = "刷新"


def shop_state(boxes: list[TextBox]) -> bool | None:
    """商店是否展开:True / False;被角色详情等遮住、看不出来时返回 None。

    照 M7A 的判法:右下角按钮是「收起」**并且**刷新区有刷新标签,才算展开;收起时右下按钮是「商店」。
    只认到一半时宁可算展开(多收一次没损失),漏判会把点击打在商店面板上。
    """
    folded = find(boxes, "^收起$", region=(0.78, 0.86, 0.14, 0.08))          # 右下角按钮
    refresh = find(boxes, REFRESH_LABEL, region=(0.78, 0.40, 0.12, 0.10))     # 右侧「刷新」/「免费刷新」
    if folded is not None and refresh is not None:
        return True
    if find(boxes, "^商店$", region=(0.78, 0.86, 0.14, 0.08)):
        return False
    if folded is not None or refresh is not None:
        return True
    # 角色详情把按钮和刷新都盖住时,中间那行「各费出现概率」还露着,也用得上
    if sum(1 for b in boxes if 0.33 <= b.y <= 0.39 and 0.45 <= b.x <= 0.75 and re.fullmatch(r"\d{1,2}%", b.text)) >= 3:
        return True
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


def read_prices(image: Image.Image, ocr: Ocr) -> dict[int, int]:
    """每张卡名字栏右边「金币图标 + 价格」里的价格:{格子: 价格}。

    价格是单个数字,整屏识别经常检测不到(2026-11-16 Codex 那局五张卡的价格全没读出来,模型只能看图):
    按 hud.read_gold 的办法先按颜色找到金币图标,只把它右边的白字放大识别。卡片买走了(没有金币图标)就不给。
    """
    from .hud import _ocr_mask

    img = np.asarray(image.convert("RGB"))
    H, W = img.shape[:2]
    out = {}
    for i, cx in enumerate(SHOP_X, start=1):
        crop = img[int(SHOP_NAME_Y[0] * H):int(SHOP_NAME_Y[1] * H) + 2, int(cx * W):int((cx + 0.075) * W)]
        hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
        coin = ((hsv[..., 0] >= 12) & (hsv[..., 0] <= 35) & (hsv[..., 1] >= 120) & (hsv[..., 2] >= 120)).astype(np.uint8)
        n, _, stats, _ = cv2.connectedComponentsWithStats(coin)
        if n < 2:
            continue
        k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        if stats[k, cv2.CC_STAT_AREA] < 60 * (H / 540) ** 2:
            continue
        d = hsv[:, stats[k, cv2.CC_STAT_LEFT] + stats[k, cv2.CC_STAT_WIDTH] + 1:]
        white = ((d[..., 2] >= 200) & (d[..., 1] <= 60)).astype(np.uint8) * 255
        text = _ocr_mask(ocr, white)
        if not text:
            text = _ocr_mask(ocr, white, detect=False)
        text = re.sub(r"\D", "", text.replace("O", "0").replace("o", "0"))
        if re.fullmatch(r"\d{1,2}", text) and 0 < int(text) <= 30:
            out[i] = int(text)
    return out


TRIAL_Y = (0.10, 0.15)   # 卡面右上角「试用」标签的高度(商店展开时;2026-11-16 实机在卡片中心右边约 0.048)


def trial_slots(boxes: list[TextBox]) -> set[int]:
    """商店里标着「试用」的格子:账号里没有这个角色,给的是试用版(自己有的角色继承账号养成,BWIKI)。"""
    out = set()
    for b in boxes:
        if b.text == "试用" and TRIAL_Y[0] <= b.y <= TRIAL_Y[1]:
            for i, cx in enumerate(SHOP_X, start=1):
                if 0.015 <= b.x - cx <= 0.075:
                    out.add(i)
    return out


def two_star(name: str, price: int | None, costs: dict[str, set[int]]) -> bool:
    """价格是角色费用的 3 倍(又不等于它的某个费用,如星神★阿哈有 4、5、7 费三个版本)就是二星卡。"""
    c = costs.get(re.sub(r"[·•・]", "·", name), set())
    return price is not None and price not in c and any(price == 3 * x for x in c)


def format_shop(cards: list[tuple[int, str | None, int | None]], costs: dict[str, set[int]] | None = None,
                trial: set[int] | None = None) -> str:
    def one(i: int, name: str | None, price: int | None) -> str:
        if not name:
            return f"{i}号 (空)"
        star = " 2星" if costs and two_star(name, price, costs) else ""
        return (f"{i}号 {name}{star}" + ("[试用]" if trial and i in trial else "")
                + (f"({price} 金币)" if price is not None else ""))

    return ";".join(one(*c) for c in cards)
