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


@dataclass
class Hud:
    node: str | None = None
    hp: int | None = None
    gold: int | None = None
    level: int | None = None
    exp: str | None = None          # 如 12/40
    deployed: str | None = None     # 已上场/上限,如 5/6

    def as_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    def text(self) -> str:
        parts = [f"节点 {self.node}" if self.node else "",
                 f"小队生命 {self.hp}" if self.hp is not None else "",
                 f"金币 {self.gold}" if self.gold is not None else "金币 认不出",
                 f"等级 {self.level}" if self.level is not None else "",
                 f"经验 {self.exp}" if self.exp else "",
                 f"上场 {self.deployed}" if self.deployed else ""]
        return ",".join(p for p in parts if p)


def _crop(image: Image.Image, box: tuple[float, float, float, float]) -> np.ndarray:
    W, H = image.size
    x, y, w, h = box
    return np.asarray(image.convert("RGB").crop((int(x * W), int(y * H), int((x + w) * W), int((y + h) * H))))


def _ocr_mask(ocr: Ocr, mask: np.ndarray, scale: int = 4) -> str:
    """把二值化的字形(白色前景)转成白底黑字、放大后识别。"""
    ys, xs = np.nonzero(mask)
    if len(xs) < 15:
        return ""
    m = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    img = cv2.resize(255 - m, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    img = cv2.copyMakeBorder(img, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)
    return "".join(b.text for b in ocr(Image.fromarray(img).convert("RGB")))


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


def read_hud(image: Image.Image, boxes: list[TextBox], ocr: Ocr, node: str | None = None) -> Hud:
    hud = Hud(node=node)
    hp = find(boxes, r"^\d{1,3}$", region=(0.73, 0.045, 0.05, 0.05))   # 顶部爱心图标后的数字
    if hp:
        hud.hp = int(hp.text)
    exp = find(boxes, r"^\d{1,3}/\d{1,3}$", region=(0.12, 0.86, 0.07, 0.04))
    if exp:
        hud.exp = exp.text
    dep = find(boxes, r"\d{1,2}/\d{1,2}$", region=(0.44, 0.2, 0.1, 0.08))
    if dep:
        m = re.search(r"(\d{1,2}/\d{1,2})$", dep.text)
        hud.deployed = m.group(1) if m else None
    hud.gold = read_gold(image, ocr)
    hud.level = read_level(image, ocr)
    return hud
