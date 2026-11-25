"""Windows 上定位游戏窗口并截取客户区。只依赖 ctypes 和 mss。

- 先声明 DPI 感知,否则高缩放比例下窗口坐标和截图会被系统缩放;
- 按进程名 StarRail.exe 找窗口,找不到时按窗口类 UnityWndClass + 标题匹配;
- 截图优先用 PrintWindow 让窗口自己把客户区画出来,不受遮挡和叠在上面的通知、桌面挂件影响;
  失败时退回 mss 截屏幕上客户区所在的区域,这只在游戏在前台时才可靠,每帧都带上「是否在前台」。
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


def window_pid(hwnd) -> int:
    """窗口所属进程的进程号(录游戏声音用)。"""
    pid = wt.DWORD()
    _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


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


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_int32), ("biYPelsPerMeter", ctypes.c_int32),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_GetDC = _proto(_user32, "GetDC", wt.HDC, wt.HWND)
_ReleaseDC = _proto(_user32, "ReleaseDC", ctypes.c_int, wt.HWND, wt.HDC)
_PrintWindow = _proto(_user32, "PrintWindow", wt.BOOL, wt.HWND, wt.HDC, wt.UINT)
_CreateCompatibleDC = _proto(_gdi32, "CreateCompatibleDC", wt.HDC, wt.HDC)
_DeleteDC = _proto(_gdi32, "DeleteDC", wt.BOOL, wt.HDC)
_CreateDIBSection = _proto(_gdi32, "CreateDIBSection", wt.HBITMAP, wt.HDC, ctypes.POINTER(_BITMAPINFOHEADER),
                           wt.UINT, ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD)
_SelectObject = _proto(_gdi32, "SelectObject", wt.HGDIOBJ, wt.HDC, wt.HGDIOBJ)
_DeleteObject = _proto(_gdi32, "DeleteObject", wt.BOOL, wt.HGDIOBJ)
_GdiFlush = _proto(_gdi32, "GdiFlush", wt.BOOL)

PW_CLIENTONLY_RENDERFULLCONTENT = 0x1 | 0x2


def print_window(hwnd, width: int, height: int) -> Image.Image | None:
    """让窗口把客户区画到内存位图里(PrintWindow + PW_RENDERFULLCONTENT),失败返回 None。

    截的是窗口自己的内容,不受遮挡和叠在上面的通知、弹窗影响;窗口最小化时截不到。
    做法参考 March7thAssistant 的后台截图(module/automation/screenshot.py)。
    """
    info = _BITMAPINFOHEADER(biSize=ctypes.sizeof(_BITMAPINFOHEADER), biWidth=width, biHeight=-height,  # 负数:自上而下
                             biPlanes=1, biBitCount=32, biCompression=0)
    bits = ctypes.c_void_p()
    screen = _GetDC(hwnd)
    mem = _CreateCompatibleDC(screen)
    bmp = _CreateDIBSection(mem, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
    try:
        if not mem or not bmp:
            return None
        old = _SelectObject(mem, bmp)
        ok = _PrintWindow(hwnd, mem, PW_CLIENTONLY_RENDERFULLCONTENT)
        _GdiFlush()
        _SelectObject(mem, old)
        if not ok:
            return None
        data = ctypes.string_at(bits, width * height * 4)
        return Image.frombuffer("RGB", (width, height), data, "raw", "BGRX", 0, 1).copy()
    finally:
        if bmp:
            _DeleteObject(bmp)
        if mem:
            _DeleteDC(mem)
        _ReleaseDC(hwnd, screen)


class GameWindowSource:
    """截取游戏窗口客户区(PrintWindow 为主,mss 兜底)。每次截图都重新读取窗口位置,窗口被拖动后也能截对。"""

    def __init__(self, exe: str = GAME_EXE):
        import threading

        set_dpi_awareness()
        self.exe = exe
        self._local = threading.local()   # mss 的设备上下文跟线程走:网页里创建、对局线程里截图,所以每个线程各建一个
        self._hwnd = None

    def _sct(self, fresh: bool = False):
        import mss

        old = getattr(self._local, "mss", None)
        if old is not None and not fresh:
            return old
        if old is not None:
            try:
                old.close()
            except Exception:  # noqa: BLE001 - 已经失效的上下文释放失败也无所谓
                pass
        self._local.mss = mss.mss()
        return self._local.mss

    def close(self) -> None:
        old = getattr(self._local, "mss", None)
        if old is not None:
            try:
                old.close()
            except Exception:  # noqa: BLE001
                pass

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
        image = print_window(info.hwnd, info.width, info.height)
        if image is not None and (image.getbbox() is not None or not info.foreground):
            return Frame(image=image, timestamp=time.time(), foreground=info.foreground, source=info.title)
        if not info.foreground:
            raise CaptureError("窗口截图失败,游戏又不在前台,截屏幕会截到挡在上面的窗口")
        # 窗口截图失败或全黑(个别显卡驱动下可能如此)时,游戏在前台就截屏幕上的这块区域
        return Frame(image=self._grab_screen(info), timestamp=time.time(), foreground=True, source=info.title)

    def _grab_screen(self, info: WindowInfo) -> Image.Image:
        import mss

        region = {"left": info.left, "top": info.top, "width": info.width, "height": info.height}
        try:
            shot = self._sct().grab(region)
        except mss.exception.ScreenShotError:
            # 切到安全桌面(UAC 提示、锁屏)后原来的设备上下文会一直失效,重建一次再截
            try:
                shot = self._sct(fresh=True).grab(region)
            except mss.exception.ScreenShotError as e:
                raise CaptureError(f"截图失败(可能有 UAC 提示或锁屏):{e}") from None
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


# ---------------------------------------------------------------- 全局热键

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x4000
WM_HOTKEY = 0x0312
_VK = {**{f"f{i}": 0x6F + i for i in range(1, 13)}, "pause": 0x13, "end": 0x23}


class _MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint32), ("wParam", ctypes.c_size_t),
                ("lParam", ctypes.c_ssize_t), ("time", ctypes.c_uint32), ("pt", _POINT)]


def _parse_hotkey(spec: str) -> tuple[int, int]:
    mods, vk = 0, None
    for part in spec.lower().split("+"):
        if part == "ctrl":
            mods |= MOD_CONTROL
        elif part == "alt":
            mods |= MOD_ALT
        elif part == "shift":
            mods |= MOD_SHIFT
        elif part in _VK:
            vk = _VK[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        else:
            raise ValueError(f"不认识的按键:{part}")
    if vk is None:
        raise ValueError(f"热键缺少主键:{spec}")
    return mods, vk


def register_hotkey(candidates: list[str], callback) -> str | None:
    """在后台线程注册第一个没被占用的全局热键,按下时调用 callback;都被占用时返回 None。"""
    register = _proto(_user32, "RegisterHotKey", wt.BOOL, wt.HWND, ctypes.c_int, wt.UINT, wt.UINT)
    get_message = _proto(_user32, "GetMessageW", wt.BOOL, ctypes.POINTER(_MSG), wt.HWND, wt.UINT, wt.UINT)
    result: dict[str, str] = {}
    ready = __import__("threading").Event()

    def run():
        for spec in candidates:
            try:
                mods, vk = _parse_hotkey(spec)
            except ValueError:
                continue
            if register(None, 1, mods | MOD_NOREPEAT, vk):
                result["name"] = spec
                break
        ready.set()
        if "name" not in result:
            return
        msg = _MSG()  # RegisterHotKey(NULL, ...) 把 WM_HOTKEY 投递到注册它的线程
        while get_message(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                callback()

    __import__("threading").Thread(target=run, name="hotkey", daemon=True).start()
    ready.wait(2)
    return result.get("name")
