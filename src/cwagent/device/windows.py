"""Windows 上直接操作游戏:截图用 capture.windows,键鼠用 SendInput(需要管理员权限,游戏是提权进程)。"""

from __future__ import annotations

import ctypes
import time

from ..capture import CaptureError, Frame
from ..capture import windows as win

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x0001, 0x8000, 0x4000
MOUSEEVENTF_WHEEL = 0x0800
BUTTONS = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010)}
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x0001, 0x0002, 0x0008
VK = {"esc": 0x1B, "escape": 0x1B, "space": 0x20, "enter": 0x0D, "tab": 0x09, "shift": 0x10, "ctrl": 0x11, "alt": 0x12}
VK.update({f"f{i}": 0x6F + i for i in range(1, 13)})


class _MOUSE(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_int32), ("dy", ctypes.c_int32), ("mouseData", ctypes.c_uint32),
                ("dwFlags", ctypes.c_uint32), ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t)]


class _KEYBD(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_uint16), ("wScan", ctypes.c_uint16), ("dwFlags", ctypes.c_uint32),
                ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t)]


class _UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSE), ("ki", _KEYBD), ("pad", ctypes.c_byte * 32)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_uint32), ("u", _UNION)]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(_INPUT), ctypes.c_int]
_user32.MapVirtualKeyW.argtypes = [ctypes.c_uint, ctypes.c_uint]
_user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_user32.GetSystemMetrics.argtypes = [ctypes.c_int]


def _send(*inputs: _INPUT) -> None:
    arr = (_INPUT * len(inputs))(*inputs)
    if _user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT)) != len(inputs):
        raise OSError(f"SendInput 失败(错误码 {ctypes.get_last_error()}),agent 需要以管理员身份运行")


def _mouse(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> _INPUT:
    inp = _INPUT(type=INPUT_MOUSE)
    inp.mi = _MOUSE(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)
    return inp


def _key(vk: int, up: bool) -> _INPUT:
    inp = _INPUT(type=INPUT_KEYBOARD)
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    if vk in (0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E):
        flags |= KEYEVENTF_EXTENDEDKEY
    inp.ki = _KEYBD(0, _user32.MapVirtualKeyW(vk, 0), flags, 0, 0)
    return inp


class WindowsDevice:
    def __init__(self):
        self.source = win.GameWindowSource()
        self._info = None

    def grab(self) -> Frame:
        frame = self.source.grab()
        self._info = self.source.window()
        return frame

    def _screen_point(self, x: float, y: float) -> tuple[int, int]:
        info = self.source.window()
        if not info.foreground:
            win.focus(info.hwnd)
            info = self.source.window()
            if not info.foreground:
                raise CaptureError("游戏窗口不在前台,无法发送输入")
        return info.left + round(x * (info.width - 1)), info.top + round(y * (info.height - 1))

    def _move_abs(self, sx: int, sy: int) -> None:
        vx, vy = _user32.GetSystemMetrics(76), _user32.GetSystemMetrics(77)
        vw, vh = _user32.GetSystemMetrics(78), _user32.GetSystemMetrics(79)
        ax = round((sx - vx) * 65535 / max(vw - 1, 1))
        ay = round((sy - vy) * 65535 / max(vh - 1, 1))
        _send(_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, ax, ay))
        _user32.SetCursorPos(sx, sy)

    def move(self, x: float, y: float) -> None:
        self._move_abs(*self._screen_point(x, y))

    def click(self, x: float, y: float, *, button: str = "left") -> None:
        self._move_abs(*self._screen_point(x, y))
        time.sleep(0.05)
        down, up = BUTTONS[button]
        _send(_mouse(down))
        time.sleep(0.05)
        _send(_mouse(up))

    def drag(self, x1: float, y1: float, x2: float, y2: float, *, duration: float = 0.4) -> None:
        a, b = self._screen_point(x1, y1), self._screen_point(x2, y2)
        self._move_abs(*a)
        time.sleep(0.05)
        _send(_mouse(BUTTONS["left"][0]))
        try:
            time.sleep(0.1)
            steps = max(10, int(duration * 60))
            for i in range(1, steps + 1):
                t = i / steps
                self._move_abs(round(a[0] + (b[0] - a[0]) * t), round(a[1] + (b[1] - a[1]) * t))
                time.sleep(duration / steps)
            time.sleep(0.1)
        finally:
            _send(_mouse(BUTTONS["left"][1]))

    def scroll(self, x: float, y: float, notches: int) -> None:
        self._move_abs(*self._screen_point(x, y))
        for _ in range(abs(notches)):
            _send(_mouse(MOUSEEVENTF_WHEEL, data=120 if notches > 0 else -120))
            time.sleep(0.04)

    def key(self, key: str) -> None:
        self._screen_point(0.5, 0.5)  # 确保游戏在前台
        name = key.lower()
        vk = VK.get(name) or (ord(name.upper()) if len(name) == 1 else None)
        if vk is None:
            raise ValueError(f"不认识的按键:{key}")
        _send(_key(vk, up=False))
        time.sleep(0.05)
        _send(_key(vk, up=True))
