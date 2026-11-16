"""给决策截图标号;标号坐标始终相对于原始客户区。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..llm.base import ImageInput
from .board import SHOP_X, slots
from .equipment import cell_center
from .ocr import TextBox


@dataclass(frozen=True)
class Mark:
    id: int
    x: float
    y: float
    label: str


def crosses(image: Image.Image) -> list[tuple[float, float]]:
    """模板匹配右上角关闭图标,未命中时不生成标号。"""
    import cv2
    import numpy as np

    gray = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    found = []
    for path in sorted((Path(__file__).resolve().parents[1] / "assets").rglob("*.png")):
        if path.stem not in ("close", "close-x", "close_corner"):
            continue
        template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if template is None or float(template.std()) < 1:
            continue
        x0, y1 = int(image.width * 0.8), int(image.height * 0.18)
        area = gray[:y1, x0:]
        for scale in (0.8, 1.0, 1.2):
            t = cv2.resize(template, None, fx=scale * image.width / 1920, fy=scale * image.height / 1080)
            if min(t.shape) < 3 or t.shape[0] > area.shape[0] or t.shape[1] > area.shape[1]:
                continue
            _, score, _, loc = cv2.minMaxLoc(cv2.matchTemplate(area, t, cv2.TM_CCOEFF_NORMED))
            if score >= 0.9:
                p = ((x0 + loc[0] + t.shape[1] / 2) / image.width, (loc[1] + t.shape[0] / 2) / image.height)
                if not any(abs(p[0] - a) < 0.02 and abs(p[1] - b) < 0.02 for a, b in found):
                    found.append(p)
    return found


def annotate(image: Image.Image, boxes: list[TextBox], *, screen: str, shop_open: bool = False,
             counts: dict | None = None, equipment: list | None = None) -> tuple[ImageInput, list[Mark]]:
    marks: list[Mark] = []

    def add(x, y, label):
        marks.append(Mark(len(marks) + 1, float(x), float(y), label))

    if screen == "prepare":
        for s in slots(shop_open, counts):
            add(s.x, s.y, f"{ {'front': '前台', 'back': '后台', 'bench': '备战席'}[s.zone]}{s.index + 1}")
        if shop_open:
            for i, x in enumerate(SHOP_X, 1):
                add(x, 0.15, f"商店{i}")
        for item in equipment or []:
            i = item["index"] if isinstance(item, dict) else item.index
            add(*cell_center(i), f"装备{i}")
    for b in boxes:
        if not b.text.strip() or b.y > 0.95 or b.score < 0.55:
            continue
        if screen == "prepare" and b.text not in {"出战", "选择主角", "商店", "收起", "刷新", "免费刷新", "开启"}:
            continue
        if any(abs(b.x - m.x) < 0.012 and abs(b.y - m.y) < 0.012 for m in marks):
            continue
        add(b.x, b.y, b.text)
        if len(marks) >= 100:
            break
    if screen == "spotlight_choice":
        for i, (x, y) in enumerate(((0.417, 0.574), (0.474, 0.574), (0.528, 0.574), (0.583, 0.574)), 1):
            add(x, y, f"光环界面前台{i}")
        for i, x in enumerate((0.370, 0.422, 0.474, 0.528, 0.583, 0.630), 1):
            add(x, 0.700, f"光环界面后台{i}")
        add(0.5, 0.407, "主角空位")
    for x, y in crosses(image):
        if len(marks) < 100:
            add(x, y, "关闭图标")
    img = image.convert("RGB").copy()
    img.thumbnail((1280, 720))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=15)
    for m in marks:
        x, y = round(m.x * img.width), round(m.y * img.height)
        text = str(m.id)
        w = int(draw.textlength(text, font=font)) + 8
        left, top = max(0, min(img.width - w, x - w // 2)), max(0, y - 22)
        draw.rectangle((left, top, left + w, top + 19), fill="#15151D", outline="#FFFFFF", width=1)
        draw.text((left + 4, top + 1), text, fill="#FFFFFF", font=font)
        draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill="#FFFF00")
    return ImageInput.from_pil(img, label="当前界面(标号)", max_size=None), marks


def legend(marks: list[Mark]) -> str:
    return "## 截图标号\n" + ";".join(f"{m.id}={m.label}" for m in marks)


def save_image(root: Path, name: str, image: ImageInput) -> str:
    import base64

    path = root / "vision" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(image.data))
    return path.relative_to(root).as_posix()
