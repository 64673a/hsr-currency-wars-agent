"""按进程录声音(Windows 进程回环,Win10 2004 起):只录游戏进程播放的声音,别的程序的提示音不会录进去。

做法照 currency-wars-agent 的 AudioCapture.cs:ActivateAudioInterfaceAsync 激活「VAD\\Process_Loopback」,
拿到 IAudioClient 后按 48kHz 16bit 双声道初始化(AUTOCONVERTPCM 让系统转换格式),事件驱动地取数据写成原始 PCM。
游戏没出声的时段系统不送数据:落后墙钟 200 毫秒以上就补静音,让 PCM 的长度一直跟着真实时间走,和视频对得上。

COM 接口用 ctypes 直接按虚表调用,不引入 comtypes / pywin32。
"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes as wt
from pathlib import Path

RATE = 48000
CHANNELS = 2
BLOCK = 4                       # 每帧字节数:16bit × 2 声道
PAD_BEHIND = RATE // 5          # 落后墙钟这么多帧(200 毫秒)就补静音
PAD_KEEP = RATE // 20           # 补到离墙钟还差这么多帧(50 毫秒),给系统送来的数据留余地


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text: str) -> _GUID:
        import uuid

        u = uuid.UUID(text)
        return cls(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))

    def __eq__(self, other) -> bool:
        return bytes(self) == bytes(other)


IID_IUNKNOWN = _GUID.parse("00000000-0000-0000-C000-000000000046")
IID_IAGILE = _GUID.parse("94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90")
IID_HANDLER = _GUID.parse("41D949AB-9862-444A-80F6-C261334DA5EB")    # IActivateAudioInterfaceCompletionHandler
IID_AUDIO_CLIENT = _GUID.parse("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2")
IID_CAPTURE_CLIENT = _GUID.parse("C8ADBD64-E71E-48a0-A4DE-185C395CD317")


class _LoopbackParams(ctypes.Structure):
    """AUDIOCLIENT_ACTIVATION_PARAMS:类型 1 = 进程回环;模式 0 = 包含目标进程的子进程。"""
    _fields_ = [("ActivationType", ctypes.c_int), ("TargetProcessId", wt.DWORD), ("ProcessLoopbackMode", ctypes.c_int)]


class _Blob(ctypes.Structure):
    _fields_ = [("cbSize", wt.ULONG), ("pBlobData", ctypes.c_void_p)]


class _PropVariant(ctypes.Structure):
    _fields_ = [("vt", wt.USHORT), ("r1", wt.USHORT), ("r2", wt.USHORT), ("r3", wt.USHORT), ("blob", _Blob)]


class _WaveFormat(ctypes.Structure):
    _fields_ = [("wFormatTag", wt.WORD), ("nChannels", wt.WORD), ("nSamplesPerSec", wt.DWORD),
                ("nAvgBytesPerSec", wt.DWORD), ("nBlockAlign", wt.WORD), ("wBitsPerSample", wt.WORD), ("cbSize", wt.WORD)]


def _method(obj: int, index: int, *argtypes):
    """COM 对象虚表里第 index 个方法(0~2 是 IUnknown)。返回值按 HRESULT 处理:失败时 ctypes 直接抛 OSError。"""
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    fn = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(vtable[index])
    return lambda *args: fn(obj, *args)


def _release(obj: int | None) -> None:
    if obj:
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(wt.ULONG, ctypes.c_void_p)(vtable[2])(obj)


class _Handler:
    """激活完成的回调对象(IActivateAudioInterfaceCompletionHandler)。系统在别的线程回调它,
    所以还要声明 IAgileObject,否则激活直接失败(E_ILLEGAL_METHOD_CALL)。"""

    _QI = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))
    _REF = ctypes.WINFUNCTYPE(wt.ULONG, ctypes.c_void_p)
    _DONE = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.c_void_p)

    def __init__(self):
        self.done = threading.Event()
        # 回调函数和虚表都挂在 self 上,对象活着它们就不会被回收
        self._fns = (self._QI(self._query), self._REF(lambda this: 1), self._REF(lambda this: 1),
                     self._DONE(self._completed))
        self._vtable = (ctypes.c_void_p * 4)(*(ctypes.cast(f, ctypes.c_void_p) for f in self._fns))
        self._obj = ctypes.c_void_p(ctypes.addressof(self._vtable))
        self.ptr = ctypes.addressof(self._obj)

    def _query(self, this, riid, out):
        if riid.contents in (IID_IUNKNOWN, IID_IAGILE, IID_HANDLER):
            out[0] = this
            return 0
        out[0] = None
        return -2147467262   # E_NOINTERFACE

    def _completed(self, this, operation):
        self.done.set()
        return 0


class ProcessAudio:
    """录指定进程的声音到 PCM 文件(48kHz、16bit、双声道、小端)。start 失败时抛 OSError / RuntimeError。"""

    def __init__(self, pid: int, path: Path):
        self.pid = pid
        self.path = path
        self.frames = 0
        self.started_at: float | None = None   # 开始取数据时的墙钟(time.time())
        self.error: str | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, timeout: float = 5.0) -> None:
        self._thread = threading.Thread(target=self._run, name="game-audio", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            self._stop.set()
            raise RuntimeError("激活进程回环超时")
        if self.error:
            raise RuntimeError(self.error)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(5)

    def _run(self) -> None:
        ole32 = ctypes.WinDLL("ole32")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateEventW.restype = wt.HANDLE
        kernel32.CreateEventW.argtypes = (ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR)
        kernel32.WaitForSingleObject.argtypes = (wt.HANDLE, wt.DWORD)
        kernel32.CloseHandle.argtypes = (wt.HANDLE,)
        ole32.CoInitializeEx(None, 0)   # COINIT_MULTITHREADED
        client = capture = operation = None
        event = None
        try:
            mmdev = ctypes.WinDLL("Mmdevapi")
            activate = mmdev.ActivateAudioInterfaceAsync
            activate.restype = ctypes.HRESULT
            activate.argtypes = (wt.LPCWSTR, ctypes.POINTER(_GUID), ctypes.POINTER(_PropVariant), ctypes.c_void_p,
                                 ctypes.POINTER(ctypes.c_void_p))
            params = _LoopbackParams(1, self.pid, 0)
            prop = _PropVariant(vt=65, blob=_Blob(ctypes.sizeof(params), ctypes.addressof(params)))   # VT_BLOB
            handler = _Handler()
            op = ctypes.c_void_p()
            activate("VAD\\Process_Loopback", ctypes.byref(IID_AUDIO_CLIENT), ctypes.byref(prop), handler.ptr,
                     ctypes.byref(op))
            operation = op.value
            if not handler.done.wait(5):
                raise RuntimeError("激活进程回环超时")
            hr, unknown = ctypes.HRESULT(), ctypes.c_void_p()
            _method(operation, 3, ctypes.POINTER(ctypes.HRESULT), ctypes.POINTER(ctypes.c_void_p))(
                ctypes.byref(hr), ctypes.byref(unknown))
            if hr.value < 0:
                raise RuntimeError(f"激活进程回环失败 0x{hr.value & 0xFFFFFFFF:08X}")
            client = unknown.value
            fmt = _WaveFormat(1, CHANNELS, RATE, RATE * BLOCK, BLOCK, 16, 0)
            # LOOPBACK | EVENTCALLBACK | SRC_DEFAULT_QUALITY | AUTOCONVERTPCM;缓冲 200 毫秒
            flags = 0x00020000 | 0x00040000 | 0x08000000 | 0x80000000
            _method(client, 3, ctypes.c_int, wt.DWORD, ctypes.c_longlong, ctypes.c_longlong,
                    ctypes.POINTER(_WaveFormat), ctypes.c_void_p)(0, flags, 2_000_000, 0, ctypes.byref(fmt), None)
            event = kernel32.CreateEventW(None, False, False, None)
            _method(client, 13, wt.HANDLE)(event)
            svc = ctypes.c_void_p()
            _method(client, 14, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))(
                ctypes.byref(IID_CAPTURE_CLIENT), ctypes.byref(svc))
            capture = svc.value
            get_buffer = _method(capture, 3, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32),
                                 ctypes.POINTER(wt.DWORD), ctypes.c_void_p, ctypes.c_void_p)
            release_buffer = _method(capture, 4, ctypes.c_uint32)
            next_size = _method(capture, 5, ctypes.POINTER(ctypes.c_uint32))
            with self.path.open("wb") as out:
                _method(client, 10)()   # Start
                self.started_at = time.time()
                clock = time.perf_counter()
                self._ready.set()
                packet, data, count, bits = ctypes.c_uint32(), ctypes.c_void_p(), ctypes.c_uint32(), wt.DWORD()
                silence = bytes(RATE * BLOCK // 10)
                while not self._stop.is_set():
                    kernel32.WaitForSingleObject(event, 100)
                    next_size(ctypes.byref(packet))
                    while packet.value:
                        get_buffer(ctypes.byref(data), ctypes.byref(count), ctypes.byref(bits), None, None)
                        size = count.value * BLOCK
                        if bits.value & 0x2 or not data.value:   # AUDCLNT_BUFFERFLAGS_SILENT
                            out.write(bytes(size))
                        else:
                            out.write(ctypes.string_at(data.value, size))
                        self.frames += count.value
                        release_buffer(count.value)
                        next_size(ctypes.byref(packet))
                    due = int((time.perf_counter() - clock) * RATE)
                    while self.frames < due - PAD_BEHIND:
                        gap = min(len(silence) // BLOCK, due - PAD_KEEP - self.frames)
                        if gap <= 0:
                            break
                        out.write(silence[:gap * BLOCK])
                        self.frames += gap
                _method(client, 11)()   # Stop
        except Exception as e:  # noqa: BLE001 - 录不了声音不影响录画面
            self.error = f"{type(e).__name__}: {e}"
        finally:
            self._ready.set()
            for obj in (capture, client, operation):
                _release(obj)
            if event:
                kernel32.CloseHandle(event)
            ole32.CoUninitialize()
