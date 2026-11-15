"""装备栏:右上角的装备格子,用知识库的官方装备图标做模板匹配。

布局(1920x1080,2026-11-14 实测):最上面一行是两个带数量角标的道具(不是装备,跳过);装备从 (1845, 248) 起
先从上往下排一列(每格约 76.5 像素),排满再往左一列(每列约 79.75 像素,取自 trail-cli,第二列还没实测)。
格子里的装备被用掉后,后面的往前顺移。

实测:轮滑鞋 0.88、红钻 0.68(第二名 0.43),空格最高 0.28;4 格约 0.3 秒。
特权版效果翻倍,官方图标和普通版完全一样,游戏里图标右下角多一个 V(维护者告知)。比法:格子右下角减去
匹配上的官方图标同一位置,多出来的亮色像素够多就算有 V。【待核实】门槛按 V 的大小估的,还没用实机特权装备校准。
合成:把一件拖到另一件上,弹出「A + B = C」确认框,点 ✓ 后成品放在两者中编号小的那格。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw
from PIL import Image

COL_X = 1845          # 第一列格子中心(像素)
COL_STEP = 79.75      # 往左一列
ROW_Y = 248           # 第一行格子中心
ROW_STEP = 76.5
ROWS = 6
COLS = 4
HALF = 35             # 格子 70x70
SIZES = (52, 58, 64)  # 官方图标(128x128)在格子里显示的大小
MIN_SCORE = 0.5
MIN_GAP = 0.1         # 第一名要比第二名(不同装备)高出这么多,才算认得出
CONFIRM = Path(__file__).resolve().parents[1] / "assets" / "equipment" / "compose-confirm.png"
PRIVILEGE = "·特权"
# 特权版和普通版的官方图标是同一张图,区别只在格子外面套的那层边框(维护者 2026-11-16 从活动页的
# card-border-*.svg 看出来):特权边框的右下角多一个白色 V,普通边框没有。样张存在 assets/borders/,
# 见那里的 README。V 是矢量图形,直接按它的形状做掩码去比,比"数多出来的亮色像素"稳得多——图标自己的
# 白高光、模板一两个像素的错位都不会再凑够门槛。下面是特权边框(44 视口)里那个白色 V 的路径,按 70x70 等比换算。
V_PATH = ((30.9053, 30.2451), (35.0544, 39.1594), (39.3274, 30.2451), (41.833, 30.2451),
          (36.356, 41.7632), (33.7529, 30.2451))
V_SVG = 44.0               # 上面的路径用的视口边长
V_WHITE = 195              # V 是纯白(V 的渲染带阴影,留一点余量);边框的渐变色达不到这个亮度
V_HIT = 0.5                # 掩码里这么多比例的像素够白,就算有 V【待核实:还没有实机特权装备样张校准】


def family(name: str) -> str:
    return name.removesuffix(PRIVILEGE)


@dataclass
class Item:
    index: int            # 从 1 开始
    x: float              # 格子中心(归一化)
    y: float
    id: str | None
    name: str | None      # 认不出时为 None
    category: str = ""
    score: float = 0.0
    privileged: bool = False   # 右下角有 V:特权版,效果翻倍


def cell_center(index: int) -> tuple[float, float]:
    """第 index 格(从 1 开始)的中心,归一化坐标。"""
    col, row = divmod(index - 1, ROWS)
    return (COL_X - col * COL_STEP) / 1920, (ROW_Y + row * ROW_STEP) / 1080


def _bgr(image: Image.Image) -> np.ndarray:
    im = image.convert("RGB")
    if im.size != (1920, 1080):
        im = im.resize((1920, 1080))
    return cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)


class EquipmentReader:
    def __init__(self, templates_dir: Path, names: dict[str, str], categories: dict[str, str] | None = None):
        self.names = names
        self.categories = categories or {}
        self.templates: dict[str, list[np.ndarray]] = {}
        for p in sorted(Path(templates_dir).glob("*.png")):
            t = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
            if t is None or p.stem not in names:
                continue
            if t.ndim == 3 and t.shape[2] == 4:   # 透明背景铺到深色底上,和游戏里的格子接近
                a = t[:, :, 3:4] / 255.0
                t = (t[:, :, :3] * a + np.array([30, 24, 28]) * (1 - a)).astype(np.uint8)
            self.templates[p.stem] = [cv2.resize(t, (s, s), interpolation=cv2.INTER_AREA) for s in SIZES]

    def identify(self, cell: np.ndarray) -> tuple[str | None, float, tuple | None]:
        """返回(装备 id, 分数, 匹配位置)。特权版和普通版的官方图标完全一样,按「家族」(去掉「·特权」)比,
        认出时报普通版;是不是特权版另看右下角的 V。"""
        best: dict[str, tuple[float, str, tuple]] = {}
        for k, ts in self.templates.items():
            score, where = -1.0, None
            for t in ts:
                r = cv2.matchTemplate(cell, t, cv2.TM_CCOEFF_NORMED)
                _, mx, _, loc = cv2.minMaxLoc(r)
                if mx > score:
                    score, where = float(mx), (t, loc)
            fam = family(self.names[k])
            prefer = not self.names[k].endswith(PRIVILEGE)
            if fam not in best or (score, prefer) > (best[fam][0], not self.names[best[fam][1]].endswith(PRIVILEGE)):
                best[fam] = (score, k, where)
        ranked = sorted(best.values(), key=lambda v: v[0], reverse=True)
        if not ranked or ranked[0][0] < MIN_SCORE:
            return None, ranked[0][0] if ranked else 0.0, None
        second = ranked[1][0] if len(ranked) > 1 else 0.0
        if ranked[0][0] - second < MIN_GAP:
            return None, ranked[0][0], None
        return ranked[0][1], ranked[0][0], ranked[0][2]

    @staticmethod
    def has_v(cell: np.ndarray, where: tuple = ()) -> bool:
        """格子右下角有没有那个白色 V(特权版边框的标记)。

        按 card-border-5-b 里 V 的矢量形状生成掩码,看掩码覆盖的地方够不够白;边框本身是渐变(青蓝或金色),
        达不到 V 的白度,所以描边不会再被算进去(原来正是这样把普通装备判成特权的)。
        边框可能没完全铺满格子,掩码在 ±3 像素内平移取最好的一次。
        """
        mask = EquipmentReader._v_mask(cell.shape[:2])
        white = cell.min(axis=2) >= V_WHITE
        total = float(mask.sum())
        if not total:
            return False
        for dx in range(-4, 5):                     # 边框不一定完全铺满格子,平移掩码找最好的一次
            for dy in range(-4, 5):
                m = np.roll(np.roll(mask, dy, axis=0), dx, axis=1)
                if float((white & m).sum()) / total >= V_HIT:
                    return True
        return False

    @staticmethod
    def _v_mask(size: tuple[int, int]) -> np.ndarray:
        h, w = size
        k = w / V_SVG
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).polygon([(x * k, y * k) for x, y in V_PATH], fill=255)
        return np.asarray(mask) > 0

    def read(self, image: Image.Image) -> list[Item]:
        """从第 1 格往后读,连续两格认不出(空格)就停。"""
        img = _bgr(image)
        items: list[Item] = []
        misses = 0
        for index in range(1, ROWS * COLS + 1):
            x, y = cell_center(index)
            cx, cy = round(x * 1920), round(y * 1080)
            cell = img[cy - HALF:cy + HALF, cx - HALF:cx + HALF]
            key, score, where = self.identify(cell)
            if key is None:
                misses += 1
                if misses >= 2 or score < 0.35:   # 很低的分数就是空格,不再往后找
                    break
                items.append(Item(index, x, y, None, None, score=score))
                continue
            misses = 0
            items.append(Item(index, x, y, key, self.names[key], self.categories.get(key, ""), score,
                              privileged=self.has_v(cell, where)))
        while items and items[-1].name is None:
            items.pop()
        return items


def find_compose_confirm(image: Image.Image) -> tuple[float, float] | None:
    """合成确认框里的 ✓ 按钮(归一化坐标);没有确认框时返回 None。"""
    t = cv2.imread(str(CONFIRM))
    if t is None:
        return None
    img = _bgr(image)
    x0, y0 = 1300, 100
    r = cv2.matchTemplate(img[y0:900, x0:1920], t, cv2.TM_CCOEFF_NORMED)
    _, mx, _, loc = cv2.minMaxLoc(r)
    if mx < 0.8:
        return None
    return (x0 + loc[0] + t.shape[1] / 2) / 1920, (y0 + loc[1] + t.shape[0] / 2) / 1080


CANCEL_OFFSET = 115 / 1920   # ✗ 按钮在 ✓ 左边这么远(实测 1705 与 1820 像素)


def cancel_point(confirm: tuple[float, float]) -> tuple[float, float]:
    return confirm[0] - CANCEL_OFFSET, confirm[1]


def format_items(items: list[Item]) -> str:
    return ";".join(f"{i.index}号 {i.name}" + ("·特权(效果翻倍)" if i.privileged else "")
                    + (f"({i.category})" if i.category else "") if i.name
                    else f"{i.index}号 认不出" for i in items) or "(空)"
