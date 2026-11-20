"""画面有没有在动:两张截图缩成 96x54 灰度后的逐像素平均差(0~255)。等画面静止、核对动画放完都用它。"""

from __future__ import annotations

import numpy as np
from PIL import Image


def frame_diff(a: Image.Image, b: Image.Image) -> float:
    small = [np.asarray(im.convert("L").resize((96, 54)), dtype=np.int16) for im in (a, b)]
    return float(np.abs(small[0] - small[1]).mean())
