"""实时画面服务。设计见 docs/TECH_DESIGN.md 第 13.1 节。

链路:ffmpeg 的 gfxcapture(Windows Graphics Capture,按进程名抓游戏窗口,窗口被挡住也能抓)→ H.264 硬件编码
→ RTSP(只在本机)→ MediaMTX → WebRTC → 浏览器。有人观看时 MediaMTX 才拉起 ffmpeg,没人看时不占显卡。

浏览器的 WHEP 信令经后端转发(/api/live/whep),所以 MediaMTX 的 HTTP 端口只监听本机;
对局域网只开放 WebRTC 的 UDP 端口。

ffmpeg 和 MediaMTX 不随仓库分发:`cwagent serve` 启动时发现缺少就在后台从官方发布页下载(见 setup.py),
以管理员身份运行时同时添加防火墙规则;下载期间网页照常可用,准备好后实时画面自动出现。
"""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

from .. import paths
from ..config import StreamSettings

GAME_EXE = "StarRail.exe"
PATH_NAME = "game"
RTSP_PORT = 8554    # ffmpeg 推给 MediaMTX,只监听 127.0.0.1
HTTP_PORT = 8889    # MediaMTX 的 WHEP 信令,只监听 127.0.0.1
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


def ffmpeg_exe() -> Path:
    return paths.tools_dir() / "ffmpeg" / "ffmpeg.exe"


def mediamtx_exe() -> Path:
    return paths.tools_dir() / "mediamtx" / "mediamtx.exe"


def ffmpeg_args(s: StreamSettings, encoder: str) -> list[str]:
    """抓游戏窗口并推流的 ffmpeg 参数。gfxcapture 输出显存里的 BGRA 帧,硬件编码器直接吃,不经过内存。"""
    rate = f"{s.bitrate_mbps:g}M"
    source = f"gfxcapture=window_exe={GAME_EXE}:max_framerate={s.fps}"
    if encoder == "libx264":
        source += ",hwdownload,format=bgra"
        codec = ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-pix_fmt", "yuv420p"]
    elif encoder == "h264_amf":
        codec = ["-c:v", "h264_amf", "-usage", "lowlatency", "-rc", "cbr"]
    else:
        codec = ["-c:v", "h264_nvenc", "-preset", "p4", "-tune", "ll", "-rc", "cbr", "-bf", "0"]
    # WebRTC 不支持 B 帧;关键帧间隔 2 秒,新观众最多等 2 秒出画面
    return ["-hide_banner", "-loglevel", "warning", "-filter_complex", source, *codec,
            "-b:v", rate, "-maxrate", rate, "-bufsize", rate, "-g", str(s.fps * 2), "-profile:v", "high",
            "-f", "rtsp", "-rtsp_transport", "tcp", f"rtsp://127.0.0.1:{RTSP_PORT}/{PATH_NAME}"]


def mediamtx_config(s: StreamSettings, encoder: str, ffmpeg: Path) -> dict:
    # MediaMTX 按 shell 规则拆分命令,反斜杠会被当成转义符,路径一律用正斜杠
    command = " ".join([f'"{ffmpeg.as_posix()}"', *ffmpeg_args(s, encoder)])
    return {
        "logLevel": "info",
        "logDestinations": ["stdout"],
        "api": False, "metrics": False, "pprof": False, "playback": False,
        "rtsp": True, "rtspAddress": f"127.0.0.1:{RTSP_PORT}", "rtspTransports": ["tcp"],
        "rtmp": False, "hls": False, "srt": False, "moq": False,
        "webrtc": True,
        "webrtcAddress": f"127.0.0.1:{HTTP_PORT}",
        "webrtcLocalUDPAddress": f":{s.udp_port}",
        "webrtcIPsFromInterfaces": not s.hosts,
        "webrtcAdditionalHosts": list(s.hosts),
        "paths": {PATH_NAME: {
            "runOnDemand": command,
            "runOnDemandRestart": True,          # 游戏重启后 ffmpeg 会退出,自动重新拉起
            "runOnDemandStartTimeout": "15s",
            "runOnDemandCloseAfter": "15s",      # 最后一个观众离开 15 秒后停止抓取和编码
        }},
    }


def pick_encoder(ffmpeg: Path, preferred: str, candidates: tuple[str, ...] = ("h264_nvenc", "h264_amf")) -> str:
    """auto 时依次试 NVIDIA、AMD 的硬件编码器(录像用 HEVC 的),都不行用 CPU 编码。"""
    if preferred != "auto":
        return preferred
    for encoder in candidates:
        try:
            r = subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                                "-i", "color=black:s=640x360:r=30", "-frames:v", "5", "-c:v", encoder, "-f", "null", "-"],
                               capture_output=True, timeout=30, creationflags=NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0:
            return encoder
    return "libx264"


def _kill_on_close_job():
    """Windows 作业对象:本进程退出(包括被强制结束)时,MediaMTX 和它拉起的 ffmpeg 一起结束。"""
    import ctypes
    from ctypes import wintypes as wt

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wt.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wt.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
    k32.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
    job = k32.CreateJobObjectW(None, None)
    info = Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))  # JobObjectExtendedLimitInformation
    return job, k32


@dataclass
class OfferResult:
    status: int            # 201 成功;404 没有游戏画面;其他为失败
    answer: str = ""
    session: str | None = None
    error: str = ""


class LiveStream:
    """管理 MediaMTX 进程,并转发浏览器的 WHEP 信令。"""

    def __init__(self, settings: StreamSettings, *, log: Callable[[str], None] = print,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.log = log
        self.encoder = ""
        self.reason: str | None = "还没有启动"
        self._proc: subprocess.Popen | None = None
        self._job = None
        self._transport = transport
        self._base = f"http://127.0.0.1:{HTTP_PORT}/{PATH_NAME}/whep"

    # ---------------------------------------------------------------- 进程

    def start(self) -> str | None:
        """在后台准备并启动 MediaMTX(缺程序时先下载)。不能启动时直接返回原因,否则返回 None。"""
        if sys.platform != "win32":
            self.reason = "只能在 Windows 上使用"
        elif not self.settings.enabled:
            self.reason = "已在配置里关闭(stream.enabled)"
        else:
            self.reason = "正在准备"
            threading.Thread(target=self._prepare, name="live-stream", daemon=True).start()
            return None
        return self.reason

    def _prepare(self) -> None:
        from ..capture.windows import is_admin
        from .setup import allow_firewall, install

        try:
            if not ffmpeg_exe().exists() or not mediamtx_exe().exists():
                self.log("实时画面:缺少 ffmpeg 或 MediaMTX,开始下载(也可以手动安装,见 README)")

                def progress(message: str) -> None:
                    self.reason = f"正在下载:{message.strip()}"
                    self.log(f"实时画面:{message.strip()}")

                install(progress=progress)
            if is_admin():
                # 先放行再监听端口,否则第一次监听时会弹防火墙提示,被取消后系统会建阻止规则
                allow_firewall(self.settings.udp_port)
            self._launch()
            self.reason = None
        except Exception as e:  # noqa: BLE001 - 实时画面出问题不影响 agent 其他功能
            self.reason = f"准备失败:{type(e).__name__}: {e}"
            self.log(f"实时画面不可用:{self.reason}。可以运行 cwagent stream setup 重试,或按 README 手动安装")

    def _launch(self) -> None:
        ffmpeg, mtx = ffmpeg_exe(), mediamtx_exe()
        self.encoder = pick_encoder(ffmpeg, self.settings.encoder)
        out = paths.stream_dir()
        out.mkdir(parents=True, exist_ok=True)
        conf = out / "mediamtx.yml"
        conf.write_text(yaml.safe_dump(mediamtx_config(self.settings, self.encoder, ffmpeg), allow_unicode=True,
                                       sort_keys=False), encoding="utf-8")
        with (out / "mediamtx.log").open("w", encoding="utf-8") as log:
            self._proc = subprocess.Popen([str(mtx), str(conf)], cwd=out, stdout=log, stderr=subprocess.STDOUT,
                                          creationflags=NO_WINDOW)
        try:
            self._job, k32 = _kill_on_close_job()
            k32.AssignProcessToJobObject(self._job, int(self._proc._handle))  # noqa: SLF001
        except (OSError, AttributeError) as e:
            self.log(f"实时画面:没能绑定子进程的生命周期({e}),退出时请确认 mediamtx.exe 已结束")
        self.log(f"实时画面:已启动,编码器 {self.encoder},{self.settings.fps}fps,{self.settings.bitrate_mbps:g}Mbps")

    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def status(self) -> dict:
        if self._proc is not None and not self.running():
            self.reason = f"MediaMTX 已退出(退出码 {self._proc.returncode}),见 {paths.stream_dir() / 'mediamtx.log'}"
        available = self.running()
        return {"available": available, "reason": None if available else self.reason,
                "encoder": self.encoder or None}

    def stop(self) -> None:
        if self.running():
            self._proc.terminate()
            try:
                self._proc.wait(5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

    # ---------------------------------------------------------------- WHEP 信令

    async def offer(self, sdp: str) -> OfferResult:
        # MediaMTX 收到第一个观众时才拉起 ffmpeg,等画面就绪最多 runOnDemandStartTimeout(15 秒)
        async with httpx.AsyncClient(transport=self._transport, timeout=25) as client:
            try:
                r = await client.post(self._base, content=sdp.encode(), headers={"Content-Type": "application/sdp"})
            except httpx.HTTPError as e:
                return OfferResult(502, error=f"连不上 MediaMTX:{e}")
        if r.status_code == 201:
            location = r.headers.get("location", "")
            return OfferResult(201, r.text, location.rstrip("/").rsplit("/", 1)[-1] or None)
        if r.status_code == 404:
            return OfferResult(404, error="没有游戏画面(游戏没开或窗口被最小化)")
        return OfferResult(502, error=f"MediaMTX 返回 {r.status_code}:{r.text[:200]}")

    async def close(self, session: str) -> None:
        async with httpx.AsyncClient(transport=self._transport, timeout=5) as client:
            try:
                await client.delete(f"{self._base}/{session}")
            except httpx.HTTPError:
                pass
