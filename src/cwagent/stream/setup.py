"""下载实时画面需要的外部程序,并放行防火墙(cwagent stream setup)。

- MediaMTX:固定版本(它的配置文件遇到不认识的字段会报错,版本变了字段可能对不上);
- ffmpeg:BtbN 的 8.1 静态构建(8.1 起才有 gfxcapture),只取 ffmpeg.exe。
"""

from __future__ import annotations

import subprocess
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx

from ..components import FIREWALL_RULE
from .live import NO_WINDOW, ffmpeg_exe, mediamtx_exe

MEDIAMTX_VERSION = "v1.21.3"
MEDIAMTX_URL = (f"https://github.com/bluenviron/mediamtx/releases/download/{MEDIAMTX_VERSION}/"
                f"mediamtx_{MEDIAMTX_VERSION}_windows_amd64.zip")
FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-win64-gpl-8.1.zip"


def _download(url: str, dest: Path, progress: Callable[[str], None]) -> None:
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done, shown = 0, -1
        with dest.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                done += len(chunk)
                pct = done * 100 // total if total else -1
                if pct >= 0 and pct // 10 != shown:
                    shown = pct // 10
                    progress(f"  {pct}%({done >> 20} / {total >> 20} MB)")


def _extract(archive: Path, wanted: dict[str, Path]) -> None:
    """从压缩包里按文件名结尾取出文件:{"bin/ffmpeg.exe": 目标路径}。"""
    with zipfile.ZipFile(archive) as z:
        for suffix, target in wanted.items():
            name = next((n for n in z.namelist() if n.replace("\\", "/").endswith(suffix)), None)
            if name is None:
                raise RuntimeError(f"压缩包里没有 {suffix}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(name))


def install(*, force: bool = False, progress: Callable[[str], None] = print) -> None:
    jobs = [
        ("MediaMTX " + MEDIAMTX_VERSION, MEDIAMTX_URL, mediamtx_exe(),
         {"mediamtx.exe": mediamtx_exe(), "LICENSE": mediamtx_exe().parent / "LICENSE"}),
        ("ffmpeg 8.1", FFMPEG_URL, ffmpeg_exe(),
         {"bin/ffmpeg.exe": ffmpeg_exe(), "LICENSE.txt": ffmpeg_exe().parent / "LICENSE.txt"}),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        for name, url, exe, wanted in jobs:
            if exe.exists() and not force:
                progress(f"{name}:已存在 {exe}")
                continue
            progress(f"下载 {name}:{url}")
            archive = Path(tmp) / "download.zip"
            _download(url, archive, progress)
            _extract(archive, wanted)
            progress(f"{name}:已放到 {exe}")


def allow_firewall(udp_port: int) -> None:
    """放行 MediaMTX 的 WebRTC UDP 端口(只限本地子网)。

    同时删掉系统为 mediamtx.exe 自动生成的阻止规则:第一次监听端口时如果弹出的防火墙提示被取消,
    系统会建「阻止」规则,它比放行规则优先。
    """
    exe = str(mediamtx_exe()).replace("'", "''")
    script = (
        f"$p='{exe}'; "
        f"Get-NetFirewallRule -DisplayName '{FIREWALL_RULE}' -ErrorAction SilentlyContinue | Remove-NetFirewallRule; "
        "Get-NetFirewallApplicationFilter -Program $p -ErrorAction SilentlyContinue | Get-NetFirewallRule | "
        "Where-Object Action -eq 'Block' | Remove-NetFirewallRule; "
        f"New-NetFirewallRule -DisplayName '{FIREWALL_RULE}' -Direction Inbound -Action Allow -Program $p "
        f"-Protocol UDP -LocalPort {udp_port} -RemoteAddress LocalSubnet | Out-Null"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, text=True, creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "添加防火墙规则失败")
