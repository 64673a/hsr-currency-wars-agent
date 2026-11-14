"""程序直接执行的固定流程(不经过模型):收集本位面的节点序列、打开晶矿。

都只在备战界面、商店收起时执行,每一步都截图确认效果,不对就放弃,交回模型。
坐标以 1920x1080 客户区实测(2026-11-14)。
"""

from __future__ import annotations

import time
from collections.abc import Callable

import cv2
import numpy as np
from PIL import Image

from ..vision.ocr import Ocr, TextBox, find, has

TOP_FIRST_NODE = (0.307, 0.058)     # 备战界面顶部节点轨道的第一个图标
DETAIL_ROW = (0.48, 0.61)           # 位面详情里中间那一排节点图标的纵向范围
DETAIL_PANEL = (0.0, 0.60, 1.0, 0.25)
CLOSE_X = (0.969, 0.063)
ORB_AREA = (0.655, 0.11, 0.225, 0.35)   # 晶矿面板(x, y, 宽, 高)
ORB_SWIPE_Y = (0.17, 0.23, 0.29, 0.35, 0.41)
ORB_SWIPE_X = (0.665, 0.87)


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


def open_crystals(device, image: Image.Image, log: Callable[[str], None]) -> bool | None:
    """在晶矿面板上来回划过去打开晶矿。返回:True 打开了,False 划了没变化,None 没有晶矿。"""
    before = orb_amount(image)
    if before < 0.006:
        return None
    for yy in ORB_SWIPE_Y:
        device.drag(ORB_SWIPE_X[0], yy, ORB_SWIPE_X[1], yy, duration=0.35)
        time.sleep(0.15)
    time.sleep(1.5)
    after = orb_amount(device.grab().image)
    log(f"晶矿:划开前 {before:.3f},划开后 {after:.3f}")
    return after < before * 0.7
