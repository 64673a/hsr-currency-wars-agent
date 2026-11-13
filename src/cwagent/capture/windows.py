"""Windows 上定位游戏窗口并截取客户区。只依赖 ctypes 和 mss。

- 先声明 DPI 感知,否则高缩放比例下窗口坐标和截图会被系统缩放;
- 按进程名 StarRail.exe 找窗口,找不到时按窗口类 UnityWndClass + 标题匹配;
- 截图截的是屏幕上客户区所在的区域,游戏窗口被遮挡时会截到遮挡物,所以每帧都带上「是否在前台」。
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from dataclasses import dataclass

from PIL import Image

from .base import CaptureError, Frame

GAME_EXE = "StarRail.exe"
GAME_CLASS = "UnityWndClass"
GAME_TITLES = ("崩坏：星穹铁道", "崩坏:星穹铁道", "Honkai: Star Rail")

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SW_RESTORE = 9
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002

if sys.platform != "win32":  # 让本模块在其他平台也能被导入(例如跑测试),调用时再报错
    raise ImportError("cwagent.capture.windows 只能在 Windows 上使用")

from ctypes import wintypes as wt  # noqa: E402

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int32), ("top", ctypes.c_int32),
                ("right", ctypes.c_int32), ("bottom", ctypes.c_int32)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


def _proto(dll, name, restype, *argtypes):
    fn = getattr(dll, name)
    fn.restype = restype
    fn.argtypes = list(argtypes)
    return fn


_WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
_EnumWindows = _proto(_user32, "EnumWindows", wt.BOOL, _WNDENUMPROC, wt.LPARAM)
_IsWindowVisible = _proto(_user32, "IsWindowVisible", wt.BOOL, wt.HWND)
_IsIconic = _proto(_user32, "IsIconic", wt.BOOL, wt.HWND)
_GetClassNameW = _proto(_user32, "GetClassNameW", ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int)
_GetWindowTextW = _proto(_user32, "GetWindowTextW", ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int)
_GetWindowThreadProcessId = _proto(_user32, "GetWindowThreadProcessId", wt.DWORD, wt.HWND,
                                   ctypes.POINTER(wt.DWORD))
_GetClientRect = _proto(_user32, "GetClientRect", wt.BOOL, wt.HWND, ctypes.POINTER(_RECT))
_ClientToScreen = _proto(_user32, "ClientToScreen", wt.BOOL, wt.HWND, ctypes.POINTER(_POINT))
_GetForegroundWindow = _proto(_user32, "GetForegroundWindow", wt.HWND)
_SetForegroundWindow = _proto(_user32, "SetForegroundWindow", wt.BOOL, wt.HWND)
_ShowWindow = _proto(_user32, "ShowWindow", wt.BOOL, wt.HWND, ctypes.c_int)
_keybd_event = _proto(_user32, "keybd_event", None, wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_size_t)
_OpenProcess = _proto(_kernel32, "OpenProcess", wt.HANDLE, wt.DWORD, wt.BOOL, wt.DWORD)
_QueryFullProcessImageNameW = _proto(_kernel32, "QueryFullProcessImageNameW", wt.BOOL, wt.HANDLE, wt.DWORD,
                                     wt.LPWSTR, ctypes.POINTER(wt.DWORD))
_CloseHandle = _proto(_kernel32, "CloseHandle", wt.BOOL, wt.HANDLE)

_dpi_done = False


def set_dpi_awareness() -> None:
    """声明每显示器 DPI 感知(v2),必须在其他窗口 API 之前调用;重复调用无害。"""
    global _dpi_done
    if _dpi_done:
        return
    _dpi_done = True
    try:
        fn = _proto(_user32, "SetProcessDpiAwarenessContext", wt.BOOL, ctypes.c_void_p)
        if fn(ctypes.c_void_p(-4)):  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
            return
    except AttributeError:
        pass
    try:
        if ctypes.WinDLL("shcore").SetProcessDpiAwareness(2) == 0:
            return
    except (AttributeError, OSError):
        pass
    try:
        _user32.SetProcessDPIAware()
    except AttributeError:
        pass


def is_admin() -> bool:
    try:
        return bool(ctypes.WinDLL("shell32").IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _process_name(hwnd) -> str:
    pid = wt.DWORD()
    _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = _OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if _QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        _CloseHandle(handle)


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    left: int
    top: int
    width: int
    height: int
    foreground: bool
    minimized: bool


def find_game_window(exe: str = GAME_EXE):
    """返回游戏主窗口的句柄;找不到时返回 None。"""
    set_dpi_awareness()
    found = []

    def callback(hwnd, _):
        try:
            if not _IsWindowVisible(hwnd):
                return True
            cls = ctypes.create_unicode_buffer(256)
            _GetClassNameW(hwnd, cls, 256)
            title = ctypes.create_unicode_buffer(512)
            _GetWindowTextW(hwnd, title, 512)
            if _process_name(hwnd).lower() == exe.lower() or (
                    cls.value == GAME_CLASS and any(t in title.value for t in GAME_TITLES)):
                rect = _RECT()
                _GetClientRect(hwnd, ctypes.byref(rect))
                found.append((cls.value == GAME_CLASS, rect.right * rect.bottom, hwnd))
        except Exception:  # noqa: BLE001 - 单个窗口出错不影响枚举
            pass
        return True

    _EnumWindows(_WNDENUMPROC(callback), 0)
    if not found:
        return None
    found.sort(key=lambda f: (f[0], f[1]), reverse=True)
    return found[0][2]


def window_info(hwnd) -> WindowInfo:
    rect = _RECT()
    _GetClientRect(hwnd, ctypes.byref(rect))
    origin = _POINT(0, 0)
    _ClientToScreen(hwnd, ctypes.byref(origin))
    title = ctypes.create_unicode_buffer(512)
    _GetWindowTextW(hwnd, title, 512)
    return WindowInfo(hwnd=hwnd, title=title.value, left=origin.x, top=origin.y,
                      width=rect.right - rect.left, height=rect.bottom - rect.top,
                      foreground=_GetForegroundWindow() == hwnd, minimized=bool(_IsIconic(hwnd)))


def focus(hwnd) -> bool:
    """把游戏窗口切到前台。后台进程不能随意抢前台,先模拟一次 Alt 键让系统允许切换。"""
    if _IsIconic(hwnd):
        _ShowWindow(hwnd, SW_RESTORE)
    _keybd_event(VK_MENU, 0, 0, 0)
    _SetForegroundWindow(hwnd)
    _keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.2)
    return _GetForegroundWindow() == hwnd


class GameWindowSource:
    """截取游戏窗口客户区。每次截图都重新读取窗口位置,窗口被拖动后也能截对。"""

    def __init__(self, exe: str = GAME_EXE):
        import mss

        set_dpi_awareness()
        self.exe = exe
        self._mss = mss.mss()
        self._hwnd = None

    def close(self) -> None:
        self._mss.close()

    def window(self) -> WindowInfo:
        if self._hwnd is None or not _IsWindowVisible(self._hwnd):
            self._hwnd = find_game_window(self.exe)
        if self._hwnd is None:
            raise CaptureError("没有找到游戏窗口,请先启动游戏")
        info = window_info(self._hwnd)
        if info.minimized or info.width <= 0 or info.height <= 0:
            raise CaptureError("游戏窗口已最小化")
        return info

    def grab(self) -> Frame:
        info = self.window()
        shot = self._mss.grab({"left": info.left, "top": info.top, "width": info.width, "height": info.height})
        image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        return Frame(image=image, timestamp=time.time(), foreground=info.foreground, source=info.title)
