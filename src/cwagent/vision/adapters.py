"""本机的显卡(DXGI 适配器),给文字识别选用哪块显卡。编号就是 DirectML 的 device_id。

2026-11-20 维护者的 Windows 上:0、1、3 号都是 RTX 5060 Ti(虚拟显示器也挂在它上面),2 号是核显,
4 号是软件渲染。同名同显存的只列第一个;软件渲染不列。自动选择专用显存最大的那块(独立显卡)。
"""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Adapter:
    index: int          # DXGI 枚举顺序,也就是 DirectML 的 device_id
    name: str
    memory_mb: int      # 专用显存


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]


class _DESC1(ctypes.Structure):
    _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", ctypes.c_uint), ("DeviceId", ctypes.c_uint),
                ("SubSysId", ctypes.c_uint), ("Revision", ctypes.c_uint), ("DedicatedVideoMemory", ctypes.c_size_t),
                ("DedicatedSystemMemory", ctypes.c_size_t), ("SharedSystemMemory", ctypes.c_size_t),
                ("LuidLow", ctypes.c_uint32), ("LuidHigh", ctypes.c_int32), ("Flags", ctypes.c_uint)]


_IID_FACTORY1 = _GUID(0x770AAE78, 0xF26F, 0x4DBA, (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87))
_SOFTWARE = 2          # DXGI_ADAPTER_FLAG_SOFTWARE
# COM 虚函数表里的位置:IDXGIFactory1::EnumAdapters1、IDXGIAdapter1::GetDesc1、IUnknown::Release
_ENUM_ADAPTERS1, _GET_DESC1, _RELEASE = 12, 10, 2


def _method(obj: ctypes.c_void_p, index: int, *argtypes):
    table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(table[index])


def list_adapters() -> list[Adapter]:
    """按 DXGI 顺序列出硬件显卡;不是 Windows 或读不出来时返回空列表。"""
    if sys.platform != "win32":
        return []
    try:
        factory = ctypes.c_void_p()
        if ctypes.windll.dxgi.CreateDXGIFactory1(ctypes.byref(_IID_FACTORY1), ctypes.byref(factory)) != 0:
            return []
        out: list[Adapter] = []
        seen: set[tuple[str, int]] = set()
        try:
            for i in range(16):
                adapter = ctypes.c_void_p()
                if _method(factory, _ENUM_ADAPTERS1, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(
                        factory, i, ctypes.byref(adapter)) != 0:
                    break
                desc = _DESC1()
                ok = _method(adapter, _GET_DESC1, ctypes.POINTER(_DESC1))(adapter, ctypes.byref(desc)) == 0
                _method(adapter, _RELEASE)(adapter)
                if not ok or desc.Flags & _SOFTWARE:
                    continue
                key = (desc.Description, desc.DedicatedVideoMemory)
                if key in seen:
                    continue
                seen.add(key)
                out.append(Adapter(i, desc.Description.strip(), desc.DedicatedVideoMemory >> 20))
        finally:
            _method(factory, _RELEASE)(factory)
        return out
    except Exception:  # noqa: BLE001 - 读不出显卡列表就交给 DirectML 自己选
        return []


def pick(adapters: list[Adapter], wanted: int | None) -> Adapter | None:
    """指定了编号且有这块就用它,否则用专用显存最大的那块(独立显卡);一块都没有返回 None。"""
    chosen = next((a for a in adapters if a.index == wanted), None) if wanted is not None else None
    return chosen or max(adapters, key=lambda a: (a.memory_mb, -a.index), default=None)
