"""文字识别(RapidOCR)。坐标全部归一化到 0~1,与截图分辨率无关。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from PIL import Image


@dataclass(frozen=True)
class TextBox:
    text: str
    x: float       # 中心点
    y: float
    w: float
    h: float
    score: float

    def __str__(self) -> str:
        return f"({self.x:.3f},{self.y:.3f}) {self.text}"


class Ocr:
    def __init__(self, min_score: float = 0.55):
        self.min_score = min_score
        self._engine = None

    def _get(self):
        if self._engine is None:
            logging.getLogger("RapidOCR").setLevel(logging.WARNING)
            from rapidocr import RapidOCR

            self._engine = RapidOCR()
        return self._engine

    def __call__(self, image: Image.Image, region: tuple[float, float, float, float] | None = None) -> list[TextBox]:
        """识别整张图或其中一个区域(x, y, 宽, 高,归一化)。返回的坐标仍相对整张图。"""
        img = image.convert("RGB")
        W, H = img.size
        ox = oy = 0.0
        if region:
            x, y, w, h = region
            box = (round(x * W), round(y * H), round((x + w) * W), round((y + h) * H))
            img = img.crop(box)
            ox, oy = box[0] / W, box[1] / H
        res = self._get()(img)
        out: list[TextBox] = []
        if res.boxes is None:
            return out
        for pts, text, score in zip(res.boxes, res.txts, res.scores):
            if score < self.min_score or not text.strip():
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            out.append(TextBox(text=text.strip(), x=ox + (min(xs) + max(xs)) / 2 / W, y=oy + (min(ys) + max(ys)) / 2 / H,
                               w=(max(xs) - min(xs)) / W, h=(max(ys) - min(ys)) / H, score=float(score)))
        out.sort(key=lambda b: (round(b.y, 2), b.x))
        return out


def find(boxes: list[TextBox], pattern: str, *, region: tuple[float, float, float, float] | None = None) -> TextBox | None:
    """第一个文字匹配正则 pattern(且中心落在 region 内)的框。"""
    rx = re.compile(pattern)
    for b in boxes:
        if region and not (region[0] <= b.x <= region[0] + region[2] and region[1] <= b.y <= region[1] + region[3]):
            continue
        if rx.search(b.text):
            return b
    return None


def has(boxes: list[TextBox], *patterns: str) -> bool:
    return all(find(boxes, p) for p in patterns)
