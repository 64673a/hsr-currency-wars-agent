"""战斗界面:剩余行动值和进度。

左侧行动顺序条里有一行特殊条目(左边是金色六边形图标):「⧗N | 数字」,N 是剩余回合数,
数字是当前回合剩余的行动值,1 回合 = 100(维护者告知,2026-11-14)。这一行的高度随行动顺序变化,
所以先按颜色找到六边形图标,再分别识别金色的回合数和白色的行动值。
"""

from __future__ import annotations

import re

import cv2
import numpy as np
from PIL import Image

from .hud import _ocr_mask
from .ocr import Ocr, TextBox


def _row(image: Image.Image) -> np.ndarray | None:
    img = np.asarray(image.convert("RGB"))
    H, W = img.shape[:2]
    y0 = int(0.05 * H)
    strip = img[y0:int(0.65 * H), int(0.008 * W):int(0.032 * W)]
    hsv = cv2.cvtColor(strip, cv2.COLOR_RGB2HSV)
    mask = ((hsv[..., 0] >= 15) & (hsv[..., 0] <= 35) & (hsv[..., 1] >= 120) & (hsv[..., 2] >= 170)).astype(np.uint8)
    n, _, stats, cents = cv2.connectedComponentsWithStats(mask)
    s = H / 1080
    cand = [k for k in range(1, n) if 150 * s * s <= stats[k, cv2.CC_STAT_AREA] <= 800 * s * s
            and 0.6 <= stats[k, cv2.CC_STAT_WIDTH] / max(1, stats[k, cv2.CC_STAT_HEIGHT]) <= 1.6]
    if not cand:
        return None
    k = max(cand, key=lambda i: stats[i, cv2.CC_STAT_AREA])
    cy = int(cents[k][1]) + y0
    half = int(18 * s)
    return img[max(0, cy - half):cy + half, int(0.02 * W):int(0.095 * W)]


def has_marker(image: Image.Image) -> bool:
    """画面左侧有行动值条目,说明在战斗中(文字识别不到 HUD 时用)。"""
    return _row(image) is not None


def read_action_value(image: Image.Image, ocr: Ocr) -> int | None:
    """剩余行动值总数(回合数 × 100 + 当前回合剩余),认不出时返回 None。"""
    row = _row(image)
    if row is None:
        return None
    w = row.shape[1]
    hsv = cv2.cvtColor(row, cv2.COLOR_RGB2HSV)
    gold = hsv[:, int(w * 0.36):int(w * 0.52)]
    white = hsv[:, int(w * 0.56):int(w * 0.97)]
    g = ((gold[..., 0] >= 15) & (gold[..., 0] <= 40) & (gold[..., 1] >= 100) & (gold[..., 2] >= 160)).astype(np.uint8) * 255
    v = ((white[..., 2] >= 200) & (white[..., 1] <= 60)).astype(np.uint8) * 255
    rounds = re.sub(r"\D", "", _ocr_mask(ocr, g).replace("O", "0").replace("o", "0").replace("I", "1").replace("l", "1"))
    value = re.sub(r"\D", "", _ocr_mask(ocr, v).replace("O", "0").replace("o", "0"))
    if not rounds or not value or len(rounds) > 1 or len(value) > 3:
        return None
    total = int(rounds) * 100 + int(value)
    return total if int(value) <= 100 else None


def read_progress(boxes: list[TextBox]) -> int | None:
    """顶部节点号旁的进度百分比。"""
    for b in boxes:
        if b.y < 0.06 and 0.3 < b.x < 0.5:
            m = re.search(r"(\d{1,3})%$", b.text)
            if m and int(m.group(1)) <= 100:
                return int(m.group(1))
    return None


def summarize(curve: list[tuple[int | None, int | None]]) -> str:
    """一场战斗的行动值 / 进度采样 → 给模型看的一行。"""
    av = [a for a, _ in curve if a is not None]
    pr = [p for _, p in curve if p is not None]
    parts = []
    if pr:
        parts.append(f"进度 {pr[0]}% → {pr[-1]}%")
    if av:
        seq = [av[0]] + [a for prev, a in zip(av, av[1:]) if a != prev]
        parts.append("行动值 " + " → ".join(map(str, seq[:16])) + (" …" if len(seq) > 16 else ""))
        drops = [prev - a for prev, a in zip(seq, seq[1:]) if prev - a >= 35]
        if drops:
            parts.append(f"有 {len(drops)} 次单次下降 ≥35(可能有角色阵亡)")
    return ";".join(parts) if parts else "没有读到行动值和进度"
