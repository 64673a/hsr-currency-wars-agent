"""按钮点没点亮。灰色的「确认」在选好之前也读得出文字,点了没用;底色点亮了才能提交(M7A S2 的判断:
按钮文字一带 114x21 像素里,灰度 ≥178 的像素占到 34% 以上)。"""

from __future__ import annotations

import numpy as np
from PIL import Image

from .ocr import TextBox

HALF_W, HALF_H = 57, 10      # 1920x1080 下,按钮文字中心左右 57、上下 10 像素
BRIGHT = 178
MIN_SHARE = 0.34


def lit(image: Image.Image, button: TextBox) -> bool:
    W, H = image.size
    sx, sy = W / 1920, H / 1080
    x, y = button.x * W, button.y * H
    box = (int(x - HALF_W * sx), int(y - HALF_H * sy), int(x + HALF_W * sx), int(y + HALF_H * sy))
    gray = np.asarray(image.convert("L").crop(box))
    return gray.size > 0 and float((gray >= BRIGHT).mean()) >= MIN_SHARE
