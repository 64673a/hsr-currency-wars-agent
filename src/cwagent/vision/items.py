"""右上角道具栏(消耗品):认出每一格是什么道具,能读出时附上数量。

道具栏在备战界面右上角,从右往左排(2026-11-16 Codex 那局实机截图:最右一格中心 x≈0.961、往左每格 0.040,y≈0.152),
每格是八边形裁切的道具图标,右下角是数量。图标模板在 assets/items/<id>.png(Fandom 的游戏原图,128px),
只拿中间 60% 比,避开八边形的角和数量。7 张实机截图上真道具得分 0.73~0.89,别的道具在同一格最高 0.65。

数量是图标右下角的白色小字。先识别完整角标,读不出时缩小到数字所在的区域直接识别,
避开道具图标里的白色部分;只在读出 1 个数字时给,其余让模型看截图。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import yaml
from PIL import Image

from .ocr import Ocr

ICONS = Path(__file__).resolve().parents[1] / "assets" / "items"
RIGHT_X, STEP_X, ROW_Y = 0.961, 0.040, 0.152   # 最右一格中心、每格间距、图标中心高度
ICON_W = 0.0355          # 图标宽度占画面宽度的比例
MAX_SLOTS = 6
SHOP_VISIBLE_SLOTS = 2   # 商店展开时右缘在 x≈0.892,只完整露出最右 2 格(第 3 格起被遮挡)
MATCH = 0.68             # 认作这个道具的最低分
SEARCH = 0.012           # 每格中心附近找的范围(画面宽度的比例)
# 1920 宽下 size=40,原来 (0.92, 1.0, 1.08) 对应 36/40/43 像素,跨过了 70px 图标(核心 42px)的 41~42 像素峰值(2026-11-21 实机 3-7 漏检特权赋予卡)
SCALES = (0.92, 0.96, 1.0, 1.03, 1.06, 1.09)


@dataclass
class Item:
    slot: int             # 从右往左第几格,从 1 开始
    id: int
    name: str
    count: int | None     # 读不出时为 None
    x: float              # 图标中心(拖动道具时从这里拖)
    y: float
    score: float


@lru_cache(maxsize=1)
def catalog() -> dict[int, dict]:
    """道具 id → knowledge/consumables.yaml 里的那一条。"""
    from .. import paths

    path = paths.root() / "knowledge" / "consumables.yaml"
    if not path.exists():
        return {}
    return {int(x["id"]): x for x in yaml.safe_load(path.read_text(encoding="utf-8")) or []}


@lru_cache(maxsize=1)
def _templates() -> dict[int, np.ndarray]:
    out = {}
    for f in sorted(ICONS.glob("*.png")):
        if not f.stem.isdigit():
            continue
        im = Image.open(f).convert("RGBA")
        bg = Image.new("RGBA", im.size, (0, 0, 0, 255))
        bg.alpha_composite(im)
        a = np.asarray(bg.convert("RGB"))
        h, w = a.shape[:2]
        out[int(f.stem)] = a[int(h * 0.2):int(h * 0.8), int(w * 0.2):int(w * 0.8)]
    return out


def _best(img: np.ndarray, cx: float, cy: float) -> tuple[float, int | None]:
    H, W = img.shape[:2]
    size = max(8, int(ICON_W * W * 0.6))
    half = size // 2 + int(SEARCH * W)
    x, y = int(cx * W), int(cy * H)
    area = img[max(0, y - half):y + half, max(0, x - half):x + half]
    best = (0.0, None)
    sizes = sorted({max(8, round(size * scale)) for scale in SCALES})
    for item_id, t in _templates().items():
        for s in sizes:
            if s >= min(area.shape[:2]):
                continue
            tt = cv2.resize(t, (s, s), interpolation=cv2.INTER_AREA)
            score = float(cv2.matchTemplate(area, tt, cv2.TM_CCOEFF_NORMED).max())
            if score > best[0]:
                best = (score, item_id)
    return best


def _count(image: Image.Image, ocr: Ocr, cx: float, cy: float) -> int | None:
    W, H = image.size
    box = (int(cx * W), int((cy + 0.008) * H), int((cx + ICON_W * 0.7) * W), int((cy + 0.053) * H))
    crop = image.convert("RGB").crop(box)
    crop = crop.resize((crop.width * 4, crop.height * 4), Image.LANCZOS)
    hsv = cv2.cvtColor(np.asarray(crop), cv2.COLOR_RGB2HSV)
    white = (hsv[..., 2] >= 180) & (hsv[..., 1] <= 60)
    if white.sum() < 20:
        return None
    m = cv2.copyMakeBorder(np.where(white, 0, 255).astype(np.uint8), 30, 30, 30, 30, cv2.BORDER_CONSTANT, value=255)
    text = re.sub(r"\D", "", "".join(b.text for b in ocr(Image.fromarray(m).convert("RGB"))))
    if re.fullmatch(r"[1-9]", text):
        return int(text)
    from .hud import _ocr_mask

    crop = image.convert("RGB").crop((int((cx + 0.010) * W), int((cy + 0.013) * H),
                                      int((cx + 0.025) * W), int((cy + 0.035) * H)))
    hsv = cv2.cvtColor(np.asarray(crop), cv2.COLOR_RGB2HSV)
    white = ((hsv[..., 2] >= 180) & (hsv[..., 1] <= 60)).astype(np.uint8) * 255
    text = _ocr_mask(ocr, white, detect=False)
    return int(text) if re.fullmatch(r"[1-9]", text) else None


def read_items(image: Image.Image, ocr: Ocr | None = None, max_slots: int = MAX_SLOTS) -> list[Item]:
    """从右往左一格格认,遇到认不出的格子就停(道具栏从右边排起,中间不留空)。"""
    img = np.asarray(image.convert("RGB"))
    names = catalog()
    out = []
    for i in range(min(max_slots, MAX_SLOTS)):
        cx = RIGHT_X - STEP_X * i
        score, item_id = _best(img, cx, ROW_Y)
        if item_id is None or score < MATCH:
            break
        count = _count(image, ocr, cx, ROW_Y) if ocr is not None else None
        name = (names.get(item_id) or {}).get("name") or str(item_id)
        out.append(Item(i + 1, item_id, name, count, cx, ROW_Y, round(score, 2)))
    return out


def merge_items(visible: list[Item], cached: list[Item], *, shop_open: bool) -> list[Item]:
    """合并当前读出的道具栏和上次商店收起时的缓存。

    商店收起时画面无遮挡,直接以 visible 为准;商店展开时只能读出最右 2 格,
    如果前 2 格都有道具且和缓存前 2 格是同一批道具(没有发生消耗位移),就把缓存里被遮挡的第 3 格起接上。
    """
    if not shop_open or len(visible) < SHOP_VISIBLE_SLOTS:
        return list(visible)
    if (len(cached) > SHOP_VISIBLE_SLOTS
            and [it.id for it in cached[:SHOP_VISIBLE_SLOTS]] == [it.id for it in visible[:SHOP_VISIBLE_SLOTS]]):
        return list(visible[:SHOP_VISIBLE_SLOTS]) + list(cached[SHOP_VISIBLE_SLOTS:])
    return list(visible)


def describe(items: list[Item], *, shop_open: bool = False) -> str:
    """给模型看的一行:道具名、数量、拖动起点。用法(官方文案)作为条目说明另给。"""
    if not items:
        return ""
    parts = [f"{it.name}" + (f"×{it.count}" if it.count else "") + f"({it.x:.3f},{it.y:.3f})" for it in items]
    hidden = (";第 3 格起当前被商店遮挡(来自收起商店时的缓存),拖动时程序会自动收起商店"
              if shop_open and any(it.slot > SHOP_VISIBLE_SLOTS for it in items) else "")
    return ("道具栏(右上角,从右往左;用法是把道具拖到角色或装备上,drag 从括号里的坐标拖;数量以截图为准"
            + hidden + "):" + "、".join(parts))
