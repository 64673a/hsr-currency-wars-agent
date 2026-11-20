"""给决策截图标号;标号坐标始终相对于原始客户区。

2026-11-19 改(维护者):原来标号框画在落点正上方、落点再画黄点,落点就是文字中心,选项名、说明、按钮字都被压住
(「军火[6]易+」);备战界面每个格子都打标号,可语义操作按格子编号、根本不用它们,模型真要点的阿哈面板、羁绊图标
又没有标号。现在:标号贴在文字左侧(或右侧)、格子和图标的左上角,不压字;只标能点的东西,说明文字不标;
备战界面不标格子和商店卡,改标羁绊和阿哈面板。
"""

from __future__ import annotations

import re

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..llm.base import ImageInput
from .equipment import cell_center, privilege_confirm, privilege_options
from .ocr import TextBox


@dataclass(frozen=True)
class Mark:
    id: int
    x: float              # 落点(目标中心)
    y: float
    label: str
    w: float = 0.0        # 目标的宽高(归一化),用来把标号贴在目标外面
    h: float = 0.0
    side: str = "above"   # 标号放哪:left / right(目标左右两侧)、corner(目标左上角里面)、above(落点上方,画黄点)


SENTENCE = re.compile(r"[，,；;！？【】（）]")   # 带这些标点的多半是说明文字,不是能点的东西
PREP_BUTTONS = {"出战", "选择主角", "商店", "收起", "刷新", "免费刷新", "开启"}
CELL = (35 / 1920, 35 / 1080)     # 装备栏、道具栏格子的半宽、半高(1920x1080 下 70x70)
BOND_AREA = (0.0, 0.15, 0.14, 0.65)   # 左侧羁绊列表
BOND_ICON_X, BOND_ICON_W = 0.04, 0.036   # 羁绊图标的中心 x 和宽;图标在名字和档位两行中间
# 阿哈面板(欢愉召唤阿哈后出现在棋盘左上方的深红横条,阿哈装备拖到这里):随商店开合移动。
# 2026-11-19 那局 126 张备战截图:有面板时这块区域红色占 0.26~0.5,没有时 0~0.03
AHA_PANEL = {False: (0.17, 0.25, 0.31, 0.305), True: (0.235, 0.348, 0.36, 0.393)}
AHA_RED = 0.15


def clickable(text: str) -> bool:
    """能点的东西多半是短词(按钮、选项名);长句和带句中标点的是说明文字。"""
    return len(text) <= 12 and not SENTENCE.search(text)


def aha_panel(image: Image.Image, shop_open: bool) -> tuple[float, float, float, float] | None:
    """阿哈面板的中心和宽高(归一化),没有面板返回 None。"""
    import numpy as np

    x0, y0, x1, y1 = AHA_PANEL[bool(shop_open)]
    rgb = np.asarray(image.convert("RGB")).astype(int)
    h, w = rgb.shape[:2]
    area = rgb[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]
    if area.size == 0:
        return None
    r, g, b = area[..., 0], area[..., 1], area[..., 2]
    if float(((r > 110) & (r > g + 45) & (r > b + 25)).mean()) < AHA_RED:
        return None
    return (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0


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
             equipment: list | None = None,
             items: list | None = None) -> tuple[ImageInput, list[Mark]]:
    marks: list[Mark] = []

    def add(x, y, label, w=0.0, h=0.0, side="above"):
        marks.append(Mark(len(marks) + 1, float(x), float(y), label, float(w), float(h), side))

    if screen == "prepare":
        # 格子和商店卡不标:buy、move 等语义操作按格子编号,看角色说明用 inspect 坐标(阵容里列着坐标)
        for item in equipment or []:
            i = item["index"] if isinstance(item, dict) else item.index
            add(*cell_center(i), f"装备{i}", CELL[0] * 2, CELL[1] * 2, "corner")
        for it in items or []:   # 右上角道具栏(vision.items.Item)
            add(it.x, it.y, f"道具{it.slot} {it.name}", CELL[0] * 2, CELL[1] * 2, "corner")
        panel = aha_panel(image, shop_open)
        if panel is not None:
            x, y, w, h = panel
            add(x, y, "阿哈面板(阿哈装备拖到这里)", w, h, "right")
        bx, by, bw, bh = BOND_AREA
        for b in sorted(boxes, key=lambda b: b.y):   # 左侧羁绊:落点是图标,点开看羁绊说明(巡海游侠在这里任命)
            if (bx <= b.x <= bx + bw and by <= b.y <= by + bh and len(re.findall(r"[\u4e00-\u9fff]", b.text)) >= 2
                    and b.score >= 0.55):
                # 标号贴在图标左边(屏幕边缘那一小条是空的);贴在名字右边会压到下一行的档位
                add(BOND_ICON_X, b.y + 0.016, f"羁绊 {b.text}", BOND_ICON_W, 0.0, "left")
    if screen == "privilege_choice":
        for b in privilege_options(boxes):
            add(b.x, b.y - 0.10, b.text, 0.13, 0.18, "left")
        confirm = privilege_confirm(boxes)
        boxes = [b for b in boxes if b is confirm or (b.text == "查看详情" and 0.19 <= b.x <= 0.87
                                                     and 0.34 <= b.y <= 0.40)]
    for b in boxes:
        if not b.text.strip() or b.y > 0.95 or b.score < 0.55:
            continue
        if screen == "prepare" and b.text not in PREP_BUTTONS:
            continue
        if screen != "prepare" and not clickable(b.text):
            continue
        if any(abs(b.x - m.x) < 0.012 and abs(b.y - m.y) < 0.012 for m in marks):
            continue
        add(b.x, b.y, b.text, b.w, b.h, "left")
        if len(marks) >= 100:
            break
    if screen == "spotlight_choice":
        for i, (x, y) in enumerate(((0.417, 0.574), (0.474, 0.574), (0.528, 0.574), (0.583, 0.574)), 1):
            add(x, y, f"光环界面前台{i}")
        for i, x in enumerate((0.370, 0.422, 0.474, 0.528, 0.583, 0.630), 1):
            add(x, 0.700, f"光环界面后台{i}")
        add(0.5, 0.407, "主角空位")
    for x, y in crosses(image) if screen != "privilege_choice" else ():
        if len(marks) < 100:
            add(x, y, "关闭图标", 0.02, 0.035, "left")
    img = image.convert("RGB").copy()
    img.thumbnail((1280, 720))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=15)
    for m in marks:
        text = str(m.id)
        w = int(draw.textlength(text, font=font)) + 8
        left, top = place(m, img.width, img.height, w, LABEL_H)
        draw.rectangle((left, top, left + w, top + LABEL_H), fill="#15151D", outline="#FFFFFF", width=1)
        draw.text((left + 4, top + 1), text, fill="#FFFFFF", font=font)
        if m.side == "above":   # 落点上没有文字、格子可参照时才画黄点
            x, y = round(m.x * img.width), round(m.y * img.height)
            draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill="#FFFF00")
    return ImageInput.from_pil(img, label="当前界面(标号)", max_size=None), marks


LABEL_H = 19


def place(m: Mark, width: int, height: int, label_w: int, label_h: int) -> tuple[int, int]:
    """标号框左上角的像素位置:贴在目标外侧(left / right)、目标左上角里(corner)或落点上方(above),不出画面。"""
    cx, cy = m.x * width, m.y * height
    hw, hh = m.w * width / 2, m.h * height / 2
    if m.side in ("left", "right"):
        before, after = cx - hw - 3 - label_w, cx + hw + 3
        left = before if m.side == "left" else after
        if left < 0 or left + label_w > width:       # 这一侧放不下就放另一侧
            left = after if m.side == "left" else before
        top = cy - label_h / 2
    elif m.side == "corner":
        left, top = cx - hw, cy - hh
    else:
        left, top = cx - label_w / 2, cy - label_h - 6
    return round(max(0, min(width - label_w, left))), round(max(0, min(height - label_h, top)))


def legend(marks: list[Mark]) -> str:
    """标号对照。编号和格子号一起连续的(1=前台1;2=前台2……)压成「1~4=前台1~4」:备战界面的对照表每轮都发,
    原来逐格写约 300 字(2026-11-19 报告 P1-8)。"""
    runs: list[list[tuple[int, str, int]]] = []   # (编号, 格子名, 格子号)
    parts = []
    for m in marks:
        slot = re.fullmatch(r"(\D+?)(\d+)", m.label)
        if slot is None:
            runs.append([])
            parts.append(f"{m.id}={m.label}")
            continue
        here = (m.id, slot.group(1), int(slot.group(2)))
        last = runs[-1][-1] if runs and runs[-1] else None
        if last and here[0] == last[0] + 1 and here[1] == last[1] and here[2] == last[2] + 1:
            runs[-1].append(here)
            parts[-1] = f"{runs[-1][0][0]}~{here[0]}={here[1]}{runs[-1][0][2]}~{here[2]}"
        else:
            runs.append([here])
            parts.append(f"{m.id}={m.label}")
    return "## 截图标号\n" + ";".join(parts)


def save_image(root: Path, name: str, image: ImageInput) -> str:
    import base64

    path = root / "vision" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(image.data))
    return path.relative_to(root).as_posix()
