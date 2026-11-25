"""对局录像:开局时开始录游戏窗口和游戏声音,结束时合成一个 mp4,放在对局目录里(video.mp4)。

- 画面:ffmpeg 的 gfxcapture 抓游戏窗口的客户区(被挡住也能抓),显卡 HEVC 编码,固定 60fps,写分片 mp4,
  进程被强制结束时已录的部分也能播。和实时画面(live.py)各抓各的,互不影响。
- 声音:按进程录 StarRail.exe 的声音(audio.py),先写 PCM,停止时合进 mp4。录不了声音时照样录画面。
- 对时:每一步的记录里有带毫秒的开始时刻(timing.start),meta.json 的 video.started_at 是录像第 0 秒对应的时刻,
  两者相减就是这一步在录像里的位置。started_at 由 ffmpeg 的进度输出推算(墙钟减去已输出时长,取最小值),误差在几十毫秒。
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from .live import GAME_EXE, NO_WINDOW, ffmpeg_exe, pick_encoder

FPS = 60
FILE = "video.mp4"
AUDIO_FILE = "video.pcm"
CALIBRATE = 20          # 用 ffmpeg 前 20 次进度(约 2 秒)推算第 0 帧的时刻
START_TIMEOUT = 15.0    # 这么久还没出第一帧就当作录不了(游戏没开、窗口最小化)
ENCODERS = ("hevc_nvenc", "hevc_amf")


def video_args(encoder: str, source: str, out: Path, fps: int = FPS) -> list[str]:
    """录像的 ffmpeg 参数。画质优先(固定质量),关键帧 2 秒一个,方便剪辑时拖动。

    颜色标记:抓到的是 RGB 帧,硬件编码器按 BT.601 有限范围转成 YUV(码流里也这么标),但 mp4 的 colr 盒子
    默认照抄帧上的 RGB 矩阵、全范围,按容器解码的剪辑软件颜色会偏(2026-11-25 实测 hevc_nvenc)。
    输出端的 -colorspace 对显存里的帧不起作用,所以在滤镜里用 setparams 改帧上的标记。"""
    graph = source + ",setparams=colorspace=bt470bg:range=tv:color_primaries=bt709:color_trc=iec61966-2-1"
    if encoder == "libx264":
        graph += ",hwdownload,format=bgra"
        codec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
    elif encoder == "hevc_amf":
        codec = ["-c:v", "hevc_amf", "-quality", "quality", "-rc", "qvbr", "-qvbr_quality_level", "24", "-tag:v", "hvc1"]
    else:
        codec = ["-c:v", "hevc_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "24", "-b:v", "0", "-tag:v", "hvc1"]
    return ["-hide_banner", "-loglevel", "warning", "-nostats", "-progress", "pipe:1", "-stats_period", "0.1",
            "-filter_complex", graph, "-fps_mode", "cfr", "-r", str(fps), *codec, "-g", str(fps * 2),
            "-movflags", "+frag_keyframe+empty_moov+default_base_moof", "-y", str(out)]


def mux_args(video: Path, pcm: Path, offset: float, out: Path) -> list[str]:
    """把 PCM 声音合进录像。offset = 声音开始时刻 - 录像第 0 帧时刻(秒),负数时剪掉声音开头多出来的部分。"""
    shift = ["-itsoffset", f"{offset:.3f}"] if offset >= 0 else ["-ss", f"{-offset:.3f}"]
    return ["-hide_banner", "-loglevel", "error", "-i", str(video),
            "-f", "s16le", "-ar", "48000", "-ac", "2", *shift, "-i", str(pcm),
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", "-y", str(out)]


def first_frame_at(samples: list[tuple[float, float]]) -> float | None:
    """由 (墙钟, 已输出时长) 推算第 0 帧的墙钟。进度是事后打印的,每个样本都只会偏晚,取最小的那个。"""
    starts = [wall - out for wall, out in samples if out > 0]
    return min(starts) if starts else None


def stamp(t: float) -> str:
    """和 steps.jsonl 的 timing.start 同一格式:本地时间,带日期、毫秒和时区。"""
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="milliseconds")


class Recorder:
    """一局的录像。start() 返回 None 表示开始录了,否则返回录不了的原因;stop() 返回写进 meta.json 的信息。"""

    def __init__(self, out_dir: Path, *, log: Callable[[str], None] = print):
        self.dir = out_dir
        self.log = log
        self.encoder = ""
        self.started_at: float | None = None
        self.error: str | None = None
        self._proc: subprocess.Popen | None = None
        self._samples: list[tuple[float, float]] = []
        self._calibrated = threading.Event()
        self._audio = None

    @property
    def file(self) -> Path:
        return self.dir / FILE

    def start(self) -> str | None:
        if sys.platform != "win32":
            return "只能在 Windows 上录像"
        if not ffmpeg_exe().exists():
            return "缺少 ffmpeg(运行 cwagent stream setup 下载)"
        from ..capture.windows import find_game_window, window_pid

        hwnd = find_game_window()
        if not hwnd:
            return "没找到游戏窗口"
        self.encoder = pick_encoder(ffmpeg_exe(), "auto", ENCODERS)
        source = f"gfxcapture=hwnd={int(hwnd)}:max_framerate={FPS}"
        log = (self.dir / "video.log").open("w", encoding="utf-8")
        self._proc = subprocess.Popen([str(ffmpeg_exe()), *video_args(self.encoder, source, self.file)],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, cwd=self.dir,
                                      creationflags=NO_WINDOW)
        log.close()
        threading.Thread(target=self._progress, name="record-progress", daemon=True).start()
        self._start_audio(window_pid(hwnd))   # 和画面同时开始,开头几秒也有声音
        deadline = time.monotonic() + START_TIMEOUT
        while not self._calibrated.wait(0.2):
            if self._proc.poll() is not None or time.monotonic() > deadline:
                self._kill()
                if self._audio is not None:
                    self._audio.stop()
                    self._audio.path.unlink(missing_ok=True)
                    self._audio = None
                return f"ffmpeg 没能开始录像,见 {self.dir / 'video.log'}"
        self.started_at = first_frame_at(self._samples)
        self.log(f"录像:开始,编码器 {self.encoder},声音" + ("已开始" if self._audio else f"录不了({self.error})"))
        return None

    def _start_audio(self, pid: int) -> None:
        from .audio import ProcessAudio

        audio = ProcessAudio(pid, self.dir / AUDIO_FILE)
        try:
            audio.start()
        except (OSError, RuntimeError) as e:
            self.error = str(e)
            audio.stop()
            return
        self._audio = audio

    def _progress(self) -> None:
        """读 ffmpeg 的进度输出(每 0.1 秒一组 key=value),攒前几组推算第 0 帧的时刻。"""
        assert self._proc is not None and self._proc.stdout is not None
        for raw in self._proc.stdout:
            line = raw.decode("ascii", "replace").strip()
            if not line.startswith("out_time_us=") or self._calibrated.is_set():
                continue
            try:
                out = int(line.split("=", 1)[1]) / 1e6
            except ValueError:
                continue
            self._samples.append((time.time(), out))
            if sum(o > 0 for _, o in self._samples) >= CALIBRATE:
                self._calibrated.set()

    def _kill(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.kill()
            self._proc.wait(5)

    def stop_info(self) -> dict:
        """写进 meta.json 的录像信息:文件名、第 0 帧的时刻、编码器。开始录就写一次,进程被强制结束也对得上时间。"""
        return {"file": FILE, "encoder": self.encoder, "started_at": stamp(self.started_at) if self.started_at else None}

    def stop(self) -> dict:
        """停止录像,把声音合进去。返回 stop_info() 加上 audio(声音的情况)、error(录像中途断了)。"""
        info = self.stop_info()
        if self._proc is None:
            return info
        if self._proc.poll() is None:
            try:
                self._proc.stdin.write(b"q")   # 让 ffmpeg 自己收尾
                self._proc.stdin.flush()
            except OSError:
                pass
            try:
                self._proc.wait(15)
            except subprocess.TimeoutExpired:
                self._kill()
        else:
            info["error"] = f"录像中途中断(ffmpeg 退出码 {self._proc.returncode}),见 video.log"
        audio, self._audio = self._audio, None
        if audio is None:
            info["audio"] = f"没有录声音:{self.error}" if self.error else "没有录声音"
            return info
        audio.stop()
        info["audio"] = self._mux(audio)
        return info

    def _mux(self, audio) -> str:
        pcm = audio.path
        if audio.error:
            self.log(f"录像:录声音出错 {audio.error}")
        if not audio.frames or not self.started_at or audio.started_at is None:
            pcm.unlink(missing_ok=True)
            return audio.error or "没有录到声音"
        tmp = self.dir / "video.muxing.mp4"
        r = subprocess.run([str(ffmpeg_exe()), *mux_args(self.file, pcm, audio.started_at - self.started_at, tmp)],
                           capture_output=True, text=True, creationflags=NO_WINDOW)
        if r.returncode != 0 or not tmp.exists():
            tmp.unlink(missing_ok=True)
            return f"声音没合进去,留在 {AUDIO_FILE}:{r.stderr.strip()[-300:]}"
        tmp.replace(self.file)
        pcm.unlink(missing_ok=True)
        return f"已合入({audio.frames / 48000:.0f} 秒)"
