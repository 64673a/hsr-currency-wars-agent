"""截图采集:手动游玩时定时或按热键截取游戏画面,保存为 fixtures。设计见 docs/TECH_DESIGN.md 6.5。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from ..capture import CaptureError, Frame


def _thumb(image: Image.Image) -> Image.Image:
    return image.convert("L").resize((96, 54))


def difference(a: Image.Image, b: Image.Image) -> float:
    """两张缩略图的平均像素差(0~255),用于跳过几乎没变化的画面。"""
    return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]


@dataclass
class CollectStats:
    saved: int = 0
    skipped_same: int = 0
    skipped_background: int = 0
    errors: int = 0


class Collector:
    def __init__(self, source, out_dir: Path, *, min_change: float = 2.0, keep_background: bool = False,
                 log: Callable[[str], None] = print):
        self.source = source
        self.out_dir = out_dir
        self.min_change = min_change
        self.keep_background = keep_background
        self.log = log
        self.stats = CollectStats()
        self._last_thumb: Image.Image | None = None
        self._lock = threading.Lock()
        self._warned_background = False

    def capture(self, *, force: bool = False) -> Path | None:
        """截一张。force=True(热键触发)时不做去重。"""
        with self._lock:
            try:
                frame: Frame = self.source.grab()
            except CaptureError as e:
                self.stats.errors += 1
                self.log(f"截图失败:{e}")
                return None
            if not frame.foreground and not self.keep_background:
                self.stats.skipped_background += 1
                if not self._warned_background:
                    self.log("游戏窗口不在前台,跳过(画面可能被其他窗口挡住)")
                    self._warned_background = True
                return None
            self._warned_background = False
            thumb = _thumb(frame.image)
            if not force and self._last_thumb is not None and difference(thumb, self._last_thumb) < self.min_change:
                self.stats.skipped_same += 1
                return None
            self._last_thumb = thumb
            stamp = time.localtime(frame.timestamp)
            folder = self.out_dir / time.strftime("%Y%m%d", stamp)
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / (time.strftime("%H%M%S", stamp) + f"_{int(frame.timestamp * 1000) % 1000:03d}.png")
            frame.image.save(path, optimize=False, compress_level=3)
            self.stats.saved += 1
            self.log(f"已保存 {path.name}  {frame.image.size[0]}x{frame.image.size[1]}" + ("(热键)" if force else ""))
            return path

    def run(self, *, interval: float, seconds: float, stop: threading.Event) -> CollectStats:
        """interval 秒截一次(0 表示只靠热键);seconds 秒后停止(0 表示一直运行到 stop 被设置)。"""
        deadline = time.monotonic() + seconds if seconds > 0 else None
        next_shot = time.monotonic()
        while not stop.is_set():
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                break
            if interval > 0 and now >= next_shot:
                self.capture()
                next_shot = now + interval
            stop.wait(0.1)
        return self.stats
