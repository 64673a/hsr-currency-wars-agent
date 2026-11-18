"""备战界面的数字:节点、小队生命、金币、等级、经验、上场人数。

整张图的 OCR 认不出大号的单个数字(金币、等级),这两项单独裁剪、放大后再识别。
坐标以 1920x1080 客户区实测(2026-11-14)。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

import cv2
import numpy as np
from PIL import Image

from .ocr import Ocr, TextBox, find

GOLD_AREA = (0.815, 0.815, 0.09, 0.06)    # 右下「商店」按钮上的金币图标和数字
LEVEL_AREA = (0.13, 0.82, 0.06, 0.045)    # 左下「购买经验」里的 Lv.N
# 以下取自 M7A S2 版(1920x1080 设计坐标换算)。商店展开时这两处被商店那一排挡住,读不到(2026-11-14 实测)。
HP_AREA = (0.7406, 0.0574, 0.0359, 0.0352)        # 顶部爱心后面的数字(从爱心下方开始截,避开图标)
DEPLOYED_DIGITS = (0.4656, 0.2028, 0.0797, 0.0657)   # 「已上场/上限」的数字(不含左边的小人图标)
DEPLOYED_WIDE = (0.4479, 0.1991, 0.1172, 0.0806)     # 10 人以上数字向两边展开
DEPLOYED_REGION = (0.44, 0.2, 0.1, 0.08)


@dataclass
class Hud:
    node: str | None = None
    hp: int | None = None
    gold: int | None = None
    level: int | None = None
    exp: str | None = None          # 如 12/40
    deployed: str | None = None     # 已上场/上限,如 5/6
    over: bool = False              # 已上场超过上限(光环额外占人口时数字变红,M7A)

    def as_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    def text(self) -> str:
        # 数字发红不一定是超员:备战席满了的警告状态下 7/7 也是红的(2026-11-18 实机),已上场大于上限才说超过
        beyond = self.over and _beyond(self.deployed)
        parts = [f"节点 {self.node}" if self.node else "",
                 f"小队生命 {self.hp}" if self.hp is not None else "",
                 f"金币 {self.gold}" if self.gold is not None else "金币 认不出",
                 f"等级 {self.level}" if self.level is not None else "",
                 f"经验 {self.exp}" if self.exp else "",
                 f"上场 {self.deployed}" + ("(超过上限,数字是红的)" if beyond else "") if self.deployed else ""]
        return ",".join(p for p in parts if p)


def _crop(image: Image.Image, box: tuple[float, float, float, float]) -> np.ndarray:
    W, H = image.size
    x, y, w, h = box
    return np.asarray(image.convert("RGB").crop((int(x * W), int(y * H), int((x + w) * W), int((y + h) * H))))


def _ocr_mask(ocr: Ocr, mask: np.ndarray, scale: int = 4, *, detect: bool = True) -> str:
    """把二值化的字形(白色前景)转成白底黑字、放大后识别。"""
    ys, xs = np.nonzero(mask)
    if len(xs) < (15 if detect else 3):
        return ""
    m = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    img = cv2.resize(255 - m, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    pad = 40 if detect else 8
    img = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)
    image = Image.fromarray(img).convert("RGB")
    return "".join(b.text for b in ocr(image)) if detect else ocr.line(image)


def read_gold(image: Image.Image, ocr: Ocr) -> int | None:
    """先按颜色找到金币图标,只识别它右边的数字。按钮底色有深有浅(商店开 / 关、有弹窗压暗),字色随之相反。"""
    hsv = cv2.cvtColor(_crop(image, GOLD_AREA), cv2.COLOR_RGB2HSV)
    coin = ((hsv[..., 0] >= 12) & (hsv[..., 0] <= 35) & (hsv[..., 1] >= 150) & (hsv[..., 2] >= 70)).astype(np.uint8)
    n, _, stats, _ = cv2.connectedComponentsWithStats(coin)
    if n < 2:
        return None
    k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    if stats[k, cv2.CC_STAT_AREA] < 200:
        return None
    right = stats[k, cv2.CC_STAT_LEFT] + stats[k, cv2.CC_STAT_WIDTH]
    top, bottom = stats[k, cv2.CC_STAT_TOP], stats[k, cv2.CC_STAT_TOP] + stats[k, cv2.CC_STAT_HEIGHT]
    d = hsv[top:bottom, right + 3:]
    if d.shape[1] < 5:
        return None
    if float(np.median(d[..., 2])) > 100:   # 浅底深字
        mask = (d[..., 2] <= 90) & (d[..., 1] <= 90)
    else:                                   # 深底白字
        mask = (d[..., 2] >= 190) & (d[..., 1] <= 60)
    text = _ocr_mask(ocr, mask.astype(np.uint8) * 255).replace("一", "").strip()
    return int(text) if re.fullmatch(r"\d{1,3}", text) else None


def read_level(image: Image.Image, ocr: Ocr) -> int | None:
    W, H = image.size
    x, y, w, h = LEVEL_AREA
    crop = image.convert("RGB").crop((int(x * W), int(y * H), int((x + w) * W), int((y + h) * H)))
    crop = crop.resize((crop.width * 3, crop.height * 3), Image.LANCZOS)
    pad = Image.new("RGB", (crop.width + 80, crop.height + 80))
    pad.paste(crop, (40, 40))
    m = re.search(r"Lv\.?\s*(\d{1,2})", "".join(b.text for b in ocr(pad)))
    return int(m.group(1)) if m else None


def read_crop(image: Image.Image, ocr: Ocr, box: tuple[float, float, float, float]) -> str:
    """单独截一块、放大 2 倍再识别(M7A:整屏识别会把 91 漏成 9、把相邻的两个 1 拆开)。"""
    crop = Image.fromarray(_crop(image, box))
    crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
    pad = Image.new("RGB", (crop.width + 60, crop.height + 60))
    pad.paste(crop, (30, 30))
    return "".join(b.text for b in ocr(pad))


def _shop_open(boxes: list[TextBox]) -> bool:
    """商店展开时生命值和上场人数被商店那一排挡住,不去单独截图识别。"""
    return find(boxes, "^收起$", region=(0.78, 0.86, 0.14, 0.08)) is not None


def over_capacity(image: Image.Image) -> bool:
    """「已上场/上限」的数字是红的:光环让已上场暂时超过上限(M7A 的颜色门槛)。"""
    px = _crop(image, DEPLOYED_DIGITS).astype(np.int16)
    r, g, b = px[..., 0], px[..., 1], px[..., 2]
    scale = (image.size[1] / 1080) ** 2
    return int(((r >= 186) & (r - g >= 80) & (r - b >= 70)).sum()) >= 150 * scale


def _beyond(deployed: str | None) -> bool:
    m = re.fullmatch(r"(\d+)/(\d+)", deployed or "")
    return bool(m) and int(m.group(1)) > int(m.group(2))


def parse_deployed(text: str, over: bool = False) -> str | None:
    """「已上场/上限」。左边的小人图标常被认成 i、1 或冒号:「i3/5久」→ 3/5,「12/3」→ 2/3(M7A)。
    斜杠也会被认成 1:「i919」是 9/9(2026-11-18 实机),没有斜杠时把其中一个 1 当斜杠试。
    上限 3~14;已上场一般不超过上限,数字变红(超员)时最多 14。"""
    def check(used: int, cap: int) -> str | None:
        if not 3 <= cap <= 14:
            return None
        if used > cap and not over and used >= 10 and used - 10 <= cap:
            used -= 10
        return f"{used}/{cap}" if used <= cap or (over and used <= 14) else None

    for m in re.finditer(r"(\d{1,2})/(\d{1,2})", text or ""):
        if value := check(int(m.group(1)), int(m.group(2))):
            return value
    if "/" not in (text or ""):
        digits = re.sub(r"\D", "", text or "")
        for start in (0, 1):                     # 开头的 1 可能是小人图标
            s = digits[start:]
            for i in range(1, len(s) - 1):
                if s[i] == "1" and len(s[:i]) <= 2 and len(s[i + 1:]) <= 2:
                    if value := check(int(s[:i]), int(s[i + 1:])):
                        return value
    return None


def read_deployed(image: Image.Image, boxes: list[TextBox], ocr: Ocr) -> tuple[str | None, bool]:
    over = over_capacity(image)
    dep = find(boxes, r"\d{1,2}/\d{1,2}", region=DEPLOYED_REGION)
    value = parse_deployed(dep.text, over) if dep else None
    if value is None and not _shop_open(boxes):
        value = parse_deployed(read_crop(image, ocr, DEPLOYED_DIGITS), over)
    if value is not None and int(value.split("/")[1]) >= 10:    # 10 人以上数字向两边展开,窄裁剪会把 11/11 读成 1/11
        wide = parse_deployed(read_crop(image, ocr, DEPLOYED_WIDE), over)
        value = wide or value
    return value, over


def read_hp(image: Image.Image, boxes: list[TextBox], ocr: Ocr) -> int | None:
    hp = find(boxes, r"^\d{1,3}$", region=(0.73, 0.045, 0.05, 0.05))   # 顶部爱心图标后的数字
    if hp and int(hp.text) <= 150:
        return int(hp.text)
    if _shop_open(boxes):
        return None
    text = re.sub(r"\D", "", read_crop(image, ocr, HP_AREA))
    return int(text) if text and len(text) <= 3 and int(text) <= 150 else None


def read_hud(image: Image.Image, boxes: list[TextBox], ocr: Ocr, node: str | None = None) -> Hud:
    hud = Hud(node=node)
    hud.hp = read_hp(image, boxes, ocr)
    exp = find(boxes, r"^\d{1,3}/\d{1,3}$", region=(0.12, 0.86, 0.07, 0.04))
    if exp:
        hud.exp = exp.text
    hud.deployed, hud.over = read_deployed(image, boxes, ocr)
    hud.gold = read_gold(image, ocr)
    hud.level = read_level(image, ocr)
    return hud
