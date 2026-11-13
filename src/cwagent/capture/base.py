from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".webp")


class CaptureError(RuntimeError):
    pass


@dataclass
class Frame:
    image: Image.Image          # RGB,游戏客户区
    timestamp: float
    foreground: bool = True     # 截图时游戏窗口是否在前台(不在前台时画面可能被其他窗口遮挡)
    source: str = ""            # 来源说明,如图片文件名


class DirectorySource:
    """按文件名顺序读取目录里的截图,用于离线测试和回放。"""

    def __init__(self, directory: Path):
        self.files = sorted(p for p in Path(directory).iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
        self._index = 0

    def grab(self) -> Frame:
        if self._index >= len(self.files):
            raise CaptureError("截图目录已读完")
        path = self.files[self._index]
        self._index += 1
        with Image.open(path) as img:
            return Frame(image=img.convert("RGB"), timestamp=time.time(), source=path.name)
