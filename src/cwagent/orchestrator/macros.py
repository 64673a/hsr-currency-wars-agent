"""程序直接执行的固定流程(不经过模型):收集本位面的节点序列、打开晶矿。

有没有晶矿:把晶矿面板和「空面板」参考图比对(维护者建议),对不上、且面板里有晶矿颜色的像素才算有。
参考图自带第一位面的(assets/crystals/);换了位面背景对不上但没有晶矿颜色时,把当前面板记成新的空面板
(data/templates/crystals/)。

开晶矿优先用虚拟手柄(LT+RT 一次全开,device/gamepad.py);没有手柄驱动时用鼠标一笔划过所有晶矿位置,
做法和坐标取自 March7thAssistant 的 collect_reward(GPL-3.0):从左上角空白处按下(直接在面板上按下
可能先拖起装备),再连续经过各晶矿位置。

都只在备战界面、商店收起时执行,每一步都截图确认效果,不对就放弃,交回模型。
坐标以 1920x1080 客户区实测(2026-11-14)。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .. import paths
from ..vision.ocr import Ocr, TextBox, find, has

TOP_FIRST_NODE = (0.307, 0.058)     # 备战界面顶部节点轨道的第一个图标
DETAIL_ROW = (0.48, 0.61)           # 位面详情里中间那一排节点图标的纵向范围
DETAIL_PANEL = (0.0, 0.60, 1.0, 0.25)
CLOSE_X = (0.969, 0.063)
ORB_AREA = (0.655, 0.11, 0.225, 0.35)   # 晶矿面板(x, y, 宽, 高)
# 晶矿可能出现的位置(左右两列交替,1920x1080 像素中心,取自 M7A),鼠标路径按这个顺序经过
_ORB_PIXELS = [(1577, 148), (1302, 148), (1302, 192), (1572, 191), (1581, 236), (1309, 238), (1314, 286),
               (1592, 285), (1601, 319), (1318, 318), (1322, 353), (1609, 354), (1615, 389), (1328, 388),
               (1332, 423), (1602, 423), (1563, 460), (1336, 460)]
ORB_PATH = [(0.006, 0.011)] + [(x / 1920, y / 1080) for x, y in _ORB_PIXELS]   # 第一个点是左上角空白处
PANEL_SIZE = (108, 94)          # 比对用的缩小灰度图
EMPTY_DIFF = 2.0                # 和空面板逐像素平均差不超过这个值算空(实测:空 ≤0.2,1 个晶矿 2.9,4 个 6.3)
ORB_MIN = 0.003                 # 晶矿颜色像素占比低于这个值算没有晶矿(实测:空 0,1 个晶矿 0.009)
BUNDLED_EMPTY = Path(__file__).resolve().parents[1] / "assets" / "crystals"


def _arr(image: Image.Image) -> np.ndarray:
    im = image.convert("RGB")
    if im.size != (1920, 1080):
        im = im.resize((1920, 1080))
    return np.asarray(im)


# ---------------------------------------------------------------- 位面节点序列

def detail_nodes(image: Image.Image) -> list[float]:
    """位面详情界面中间一排节点图标的横坐标(从左到右)。"""
    a = _arr(image)
    y0, y1 = int(DETAIL_ROW[0] * 1080), int(DETAIL_ROW[1] * 1080)
    gray = cv2.cvtColor(a[y0:y1], cv2.COLOR_RGB2GRAY)
    circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1.2, minDist=60, param1=120, param2=30,
                               minRadius=18, maxRadius=40)
    if circles is None:
        return []
    cs = circles[0]
    row = float(np.median(cs[:, 1]))          # 节点都在同一条线上,偏离这一行的是别的圆形图案
    return sorted(round(float(c[0]) / 1920, 3) for c in cs if abs(float(c[1]) - row) <= 10)


def _panel_text(boxes: list[TextBox]) -> str:
    x, y, w, h = DETAIL_PANEL
    inside = [b for b in boxes if x <= b.x <= x + w and y <= b.y <= y + h and not b.text.startswith("UID")]
    inside.sort(key=lambda b: (round(b.y, 2), b.x))
    return " ".join(b.text for b in inside)


def survey_plane(device, ocr: Ocr, log: Callable[[str], None]) -> list[str] | None:
    """打开位面详情,逐个点节点读说明,关闭后回到备战界面。失败返回 None。"""
    device.click(*TOP_FIRST_NODE)
    time.sleep(1.2)
    frame = device.grab()
    boxes = ocr(frame.image)
    if not has(boxes, "位面详情"):
        log("收集节点:没有打开位面详情,放弃")
        return None
    xs = detail_nodes(frame.image)
    y = (DETAIL_ROW[0] + DETAIL_ROW[1]) / 2 - 0.003
    lines: list[str] = []
    for x in xs:
        device.click(x, y)
        time.sleep(0.6)
        text = _panel_text(ocr(device.grab().image))
        if text and text not in lines:
            lines.append(text)
    device.click(*CLOSE_X)
    time.sleep(1.0)
    back = ocr(device.grab().image)
    if not find(back, "备战阶段"):
        log("收集节点:关闭位面详情后没回到备战界面")
    return lines


# ---------------------------------------------------------------- 晶矿

def orb_amount(image: Image.Image) -> float:
    """晶矿面板里晶矿球(蓝、灰白、金)占的比例。商店收起时才准:2 个球约 0.012,很多球 0.04~0.1。"""
    a = _arr(image)
    x, y, w, h = ORB_AREA
    area = a[int(y * 1080):int((y + h) * 1080), int(x * 1920):int((x + w) * 1920)]
    hsv = cv2.cvtColor(area, cv2.COLOR_RGB2HSV)
    blue = (hsv[..., 0] >= 95) & (hsv[..., 0] <= 112) & (hsv[..., 1] >= 120) & (hsv[..., 2] >= 170)
    grey = (hsv[..., 1] <= 45) & (hsv[..., 2] >= 170)
    gold = (hsv[..., 0] >= 15) & (hsv[..., 0] <= 35) & (hsv[..., 1] >= 120) & (hsv[..., 2] >= 180)
    return float((blue | grey | gold).mean())


def _learned_dir() -> Path:
    return paths.templates_dir() / "crystals"


def panel(image: Image.Image) -> np.ndarray:
    x, y, w, h = ORB_AREA
    crop = Image.fromarray(_arr(image)).crop((int(x * 1920), int(y * 1080), int((x + w) * 1920), int((y + h) * 1080)))
    return np.asarray(crop.convert("L").resize(PANEL_SIZE), dtype=np.float32)


def empty_diff(image: Image.Image) -> float:
    """当前晶矿面板和已知各个空面板的最小平均差。"""
    current = panel(image)
    diffs = [float(np.abs(current - np.asarray(Image.open(p).convert("L").resize(PANEL_SIZE), dtype=np.float32)).mean())
             for d in (BUNDLED_EMPTY, _learned_dir()) if d.exists() for p in sorted(d.glob("*.png"))]
    return min(diffs, default=float("inf"))


def learn_empty(image: Image.Image) -> Path:
    d = _learned_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"empty-{time.strftime('%Y%m%d-%H%M%S')}.png"
    Image.fromarray(panel(image).astype(np.uint8)).save(path)
    return path


def has_crystals(image: Image.Image, log: Callable[[str], None] | None = None) -> bool:
    diff = empty_diff(image)
    if diff <= EMPTY_DIFF:
        return False
    if orb_amount(image) < ORB_MIN:
        # 对不上空面板但看不到晶矿:多半是换了位面、背景不一样,记成新的空面板
        path = learn_empty(image)
        if log:
            log(f"晶矿面板和已知的空面板对不上(差 {diff:.1f})但没有晶矿,记成新的空面板:{path.name}")
        return False
    return True


def open_crystals(device, image: Image.Image, log: Callable[[str], None]) -> bool | None:
    """打开晶矿。返回:True 打开了,False 试了没变化,None 没有晶矿。"""
    if not has_crystals(image, log):
        return None
    before = orb_amount(image)
    pad = getattr(device, "pad_open_crystals", None)
    if pad is not None and pad():
        how = "手柄"
    else:
        how = "鼠标"
        error = getattr(getattr(device, "pad", None), "error", "")
        if error:
            log(f"晶矿:{error},改用鼠标")
        device.drag_path(ORB_PATH, duration=1.5)
        time.sleep(1.5)
    after_image = device.grab().image
    after, diff = orb_amount(after_image), empty_diff(after_image)
    opened = diff <= EMPTY_DIFF or after < ORB_MIN
    if opened and diff > EMPTY_DIFF:
        learn_empty(after_image)   # 开完了,但这个位面的空面板还没见过
    log(f"晶矿({how}):开之前颜色占比 {before:.3f},开之后 {after:.3f},和空面板差 {diff:.1f}")
    return opened
