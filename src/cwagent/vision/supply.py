"""补给阶段:五组补给包,每组一个角色 + 简易装备(一般 1 件,投资策略、奇遇伙伴会让它变成 2 件,维护者 2026-11-17)。

原来只把整屏文字和标号交给模型,角色、羁绊、装备散在一排排标号里,没配成「第几组 = 谁 + 什么装备」,
模型的判断只看角色(2026-11-17 sonnet 那局 1-5)。装备只有 1 件时图标旁边写着名字,2 件时不一定有,
所以装备按官方图标(data/templates/equipment)在每组下半部分找,文字只作补充。
两张实机截图(960 宽)上 10 组全对,得分 0.92~0.96(图标边长约画面宽的 0.042)。
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .ocr import TextBox

# 每组的文字中心 x(比卡片中心偏左约 0.015)。五张:M7A SUPPLY_CENTERS (250, 605, 960, 1315, 1670);
# 四张(M7A v2026.11.18 新加的布局):SUPPLY_FOUR_CENTERS (428, 783, 1137, 1492)
COLS = (0.115, 0.300, 0.485, 0.670, 0.855)
COLS_FOUR = (0.208, 0.393, 0.577, 0.762)
HALF = 0.09                                  # 每组左右各这么宽
NAME_Y = (0.505, 0.545)                      # 角色名那一行
TRAIT_Y = (0.405, 0.50)                      # 羁绊那几行
TRIAL_Y = (0.33, 0.365)                      # 卡面右上角「试用」
EQUIP_Y = (0.575, 0.715)                     # 装备那一块
SIZES = (0.034, 0.038, 0.042, 0.046)         # 装备图标边长占画面宽度的比例
MATCH = 0.75
CARD_Y = 0.40                                # 点这一组时点卡面中间


@dataclass
class Pack:
    index: int                # 第几组,从左到右从 1 开始
    role: str = ""
    traits: list[str] = field(default_factory=list)
    trial: bool = False
    equipment: list[str] = field(default_factory=list)
    x: float = 0.0
    y: float = CARD_Y

    def text(self) -> str:
        role = self.role or "角色没认出"
        extra = "、".join(self.traits)
        return (f"{self.index} 组 {role}" + (f"({extra})" if extra else "") + ("[试用]" if self.trial else "")
                + " + " + ("、".join(self.equipment) if self.equipment else "装备没认出") + f" ({self.x:.3f},{self.y:.3f})")


@lru_cache(maxsize=1)
def _basic_templates() -> dict[str, np.ndarray]:
    """简易装备的官方图标:{名字: BGR 图}。知识库或模板不在时返回空(只靠文字)。"""
    from .. import paths

    db, folder = paths.knowledge_db(), paths.templates_dir() / "equipment"
    if not db.exists() or not folder.exists():
        return {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = [(i, n) for i, n, s in con.execute("SELECT id, name, summary FROM equipment")
                if "简易" in json.dumps(json.loads(s), ensure_ascii=False)]
    except (sqlite3.Error, ValueError):
        rows = []
    finally:
        con.close()
    out = {}
    for eid, name in rows:
        t = cv2.imread(str(folder / f"{eid}.png"), cv2.IMREAD_UNCHANGED)
        if t is None:
            continue
        if t.ndim == 3 and t.shape[2] == 4:      # 透明底铺到深色底上(和 vision/equipment.py 一样)
            a = t[:, :, 3:4] / 255.0
            t = (t[:, :, :3] * a + np.array([30, 24, 28]) * (1 - a)).astype(np.uint8)
        out[name] = t
    return out


def _equipment(img: np.ndarray, cx: float, templates: dict[str, np.ndarray]) -> list[tuple[float, str]]:
    """一组补给里的装备:[(x, 名字)],从左到右。"""
    H, W = img.shape[:2]
    x0, x1 = int((cx - HALF) * W), int((cx + HALF) * W)
    y0, y1 = int(EQUIP_Y[0] * H), int(EQUIP_Y[1] * H)
    area = img[y0:y1, x0:x1]
    hits: list[tuple[float, float, float, str]] = []      # (分数, x, 边长, 名字)
    for name, t in templates.items():
        for frac in SIZES:
            s = int(frac * W)
            if s >= min(area.shape[:2]):
                continue
            r = cv2.matchTemplate(area, cv2.resize(t, (s, s), interpolation=cv2.INTER_AREA), cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(r >= MATCH)
            for y, x in zip(ys, xs):
                hits.append((float(r[y, x]), (x0 + x + s / 2) / W, s / W, name))
    kept: list[tuple[float, float, float, str]] = []
    for h in sorted(hits, reverse=True):                    # 同一个位置只留最像的那件
        if all(abs(h[1] - k[1]) > 0.6 * max(h[2], k[2]) for k in kept):
            kept.append(h)
    return sorted((k[1], k[3]) for k in kept)


def layout(boxes: list[TextBox]) -> tuple[float, ...]:
    """五张还是四张:看角色名那一行的文字落在哪种布局的列上(都对不上时按五张)。"""
    names = [b.x for b in boxes if NAME_Y[0] <= b.y <= NAME_Y[1]]

    def hits(cols):
        return sum(1 for x in names if min(abs(x - c) for c in cols) <= 0.05)

    return COLS_FOUR if hits(COLS_FOUR) > hits(COLS) else COLS


def required(boxes: list[TextBox]) -> int | None:
    """右下角「已选择 x/N」的 N:这次要选几组(一般 1,有时 2,M7A)。"""
    for b in boxes:
        m = re.search(r"已选择\s*(\d)\s*/\s*(\d)", b.text)
        if m:
            return int(m.group(2))
    return None


def read_supply(image: Image.Image, boxes: list[TextBox]) -> list[Pack]:
    img = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2BGR)
    templates = _basic_templates()
    packs = []
    for i, cx in enumerate(layout(boxes), start=1):
        col = [b for b in boxes if abs(b.x - cx) <= HALF]
        p = Pack(i, x=cx)
        names = [b for b in col if NAME_Y[0] <= b.y <= NAME_Y[1]]
        p.role = names[0].text if names else ""
        p.traits = [b.text for b in sorted(col, key=lambda b: (b.y, b.x)) if TRAIT_Y[0] <= b.y <= TRAIT_Y[1]]
        p.trial = any(b.text == "试用" and TRIAL_Y[0] <= b.y <= TRIAL_Y[1] for b in col)
        found = [n for _, n in _equipment(img, cx, templates)] if templates else []
        texts = [b.text for b in col if EQUIP_Y[0] <= b.y <= EQUIP_Y[1] and len(b.text) >= 2]
        p.equipment = found + [t for t in texts if t not in found and not any(t in f or f in t for f in found)] \
            if found else texts
        packs.append(p)
    return packs


def describe(packs: list[Pack], need: int | None = None) -> str:
    if not any(p.role for p in packs):
        return ""
    pick = f"这次要选 {need} 组" if need else "选一组"
    return (f"## 补给包(程序按位置配好的;{len(packs)} 组,{pick};每组一个角色 + 装备,点卡面选中再点「确认」)\n"
            + "\n".join(f"- {p.text()}" for p in packs))
