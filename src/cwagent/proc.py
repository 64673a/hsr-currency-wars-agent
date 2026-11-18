"""进程是否还在:对局记录据此判断一局是不是还在跑(进程崩溃、被结束时 meta.json 里写不进结束时间)。"""

from __future__ import annotations

import os
import sys


def started(pid: int) -> float | None:
    """进程的启动时间(秒,只用来和开局时记下的比对,防止进程号被别的进程重用);进程不在了返回 None。
    非 Windows 只能看进程在不在,返回 0.0。"""
    if pid <= 0:
        return None
    if sys.platform == "win32":
        return _started_windows(pid)
    try:
        os.kill(pid, 0)   # Windows 上 os.kill 会直接结束进程,只在这里用
    except ProcessLookupError:
        return None
    except PermissionError:
        pass               # 别的用户的进程:在
    return 0.0


def running(pid: int | None, when: float | None) -> bool:
    """开局时记下的进程(进程号 + 启动时间)是否还在跑。"""
    if not pid:
        return False
    now = started(pid)
    if now is None:
        return False
    return not when or not now or abs(now - when) < 1.0


def _started_windows(pid: int) -> float | None:
    import ctypes
    from ctypes import wintypes as wt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wt.HANDLE
    kernel32.OpenProcess.argtypes = (wt.DWORD, wt.BOOL, wt.DWORD)
    kernel32.GetExitCodeProcess.argtypes = (wt.HANDLE, ctypes.POINTER(wt.DWORD))
    kernel32.GetProcessTimes.argtypes = (wt.HANDLE,) + (ctypes.POINTER(wt.FILETIME),) * 4
    kernel32.CloseHandle.argtypes = (wt.HANDLE,)
    handle = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION,管理员进程也能查
    if not handle:
        return None
    try:
        code = wt.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:   # STILL_ACTIVE
            return None
        times = [wt.FILETIME() for _ in range(4)]
        if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return 0.0
        ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime   # 1601 年起的 100 纳秒数
        return ticks / 1e7 - 11644473600
    finally:
        kernel32.CloseHandle(handle)
