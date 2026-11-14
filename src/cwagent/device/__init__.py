"""设备:截图和键鼠。agent 只依赖这个接口,Windows 上直接操作游戏窗口(windows.py)。"""

from __future__ import annotations

from typing import Protocol

from ..capture import Frame


class UserInterrupt(RuntimeError):
    """有人在用鼠标(或把鼠标甩到屏幕角落急停):agent 立即让出控制。"""


class Device(Protocol):
    def grab(self) -> Frame: ...
    def click(self, x: float, y: float, *, button: str = "left") -> None: ...
    def move(self, x: float, y: float) -> None: ...
    def drag(self, x1: float, y1: float, x2: float, y2: float, *, duration: float = 0.4) -> None: ...
    def drag_path(self, points: list[tuple[float, float]], *, duration: float = 1.5) -> None: ...
    def scroll(self, x: float, y: float, notches: int) -> None: ...
    def key(self, key: str) -> None: ...


class RecordingDevice:
    """只记录不执行的假设备,配合目录截图源做离线测试。"""

    def __init__(self, source):
        self.source = source
        self.actions: list[tuple] = []

    def grab(self) -> Frame:
        return self.source.grab()

    def click(self, x, y, *, button="left"):
        self.actions.append(("click", round(x, 3), round(y, 3), button))

    def move(self, x, y):
        self.actions.append(("move", round(x, 3), round(y, 3)))

    def drag(self, x1, y1, x2, y2, *, duration=0.4):
        self.actions.append(("drag", round(x1, 3), round(y1, 3), round(x2, 3), round(y2, 3)))

    def drag_path(self, points, *, duration=1.5):
        self.actions.append(("drag_path", len(points)))

    def scroll(self, x, y, notches):
        self.actions.append(("scroll", round(x, 3), round(y, 3), notches))

    def key(self, key):
        self.actions.append(("key", key))
