"""界面区域坐标(config/regions.yaml)的读取、换算和可视化校对。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

from .. import paths


@dataclass(frozen=True)
class Region:
    name: str
    box: tuple[int, int, int, int]   # 基准分辨率下的 x, y, 宽, 高
    desc: str = ""
    calibrated: bool = False


@dataclass(frozen=True)
class RegionSet:
    base: tuple[int, int]
    regions: dict[str, Region]

    def scaled(self, name: str, size: tuple[int, int]) -> tuple[int, int, int, int]:
        """把区域换算到实际图像尺寸,返回 (left, top, right, bottom)。"""
        r = self.regions[name]
        sx, sy = size[0] / self.base[0], size[1] / self.base[1]
        x, y, w, h = r.box
        return round(x * sx), round(y * sy), round((x + w) * sx), round((y + h) * sy)

    def crop(self, image: Image.Image, name: str) -> Image.Image:
        return image.crop(self.scaled(name, image.size))


def load(path: Path | None = None) -> RegionSet:
    path = path or paths.regions_file()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    base = tuple(raw.get("base") or (1920, 1080))
    regions = {}
    for name, item in (raw.get("regions") or {}).items():
        box = item.get("box")
        if not (isinstance(box, list) and len(box) == 4):
            raise ValueError(f"区域 {name} 的 box 必须是 [x, y, 宽, 高]")
        regions[name] = Region(name=name, box=tuple(int(v) for v in box), desc=item.get("desc", ""),
                               calibrated=bool(item.get("calibrated")))
    return RegionSet(base=(int(base[0]), int(base[1])), regions=regions)


def draw(image: Image.Image, region_set: RegionSet) -> Image.Image:
    """在截图上画出所有区域:已校准为绿色,未校准为橙色,标签用区域名。"""
    out = image.convert("RGB").copy()
    pen = ImageDraw.Draw(out)
    font_size = max(14, out.size[1] // 60)
    font = ImageFont.load_default(size=font_size)
    for name, region in region_set.regions.items():
        color = (40, 220, 90) if region.calibrated else (255, 150, 30)
        left, top, right, bottom = region_set.scaled(name, out.size)
        pen.rectangle((left, top, right, bottom), outline=color, width=max(2, out.size[1] // 540))
        label_box = pen.textbbox((left, top), name, font=font)
        label_top = top - (label_box[3] - label_box[1]) - 6
        if label_top < 0:
            label_top = bottom + 2
        pen.rectangle((left, label_top, left + label_box[2] - label_box[0] + 8,
                       label_top + label_box[3] - label_box[1] + 6), fill=(0, 0, 0))
        pen.text((left + 4, label_top + 1), name, fill=color, font=font)
    return out
