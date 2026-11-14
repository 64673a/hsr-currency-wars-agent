"""网页界面的预构建包。

GitHub Actions(.github/workflows/web.yml)每次推送 web/ 后构建,发布到 Releases 的 web-latest(滚动更新)。
本机没有 web/dist 时 `cwagent serve` 自动下载,所以使用者不需要装 Node;开发者照常 `npm run build`。
包里的 BUILD.json 记录构建时的提交,和本地代码不一致时只提醒,不阻止启动。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx

from . import paths

REPO = os.environ.get("CWAGENT_REPO", "64673a/hsr-currency-wars-agent")
URL = f"https://github.com/{REPO}/releases/download/web-latest/web-dist.zip"


def build_info(dist: Path | None = None) -> dict | None:
    path = (dist or paths.web_dist()) / "BUILD.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def local_commit() -> str | None:
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=paths.root(), capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def download(dist: Path | None = None, *, url: str = URL, transport: httpx.BaseTransport | None = None,
             progress: Callable[[str], None] = print) -> dict:
    """下载并替换 web/dist,返回 BUILD.json 的内容。下载或校验失败时原来的 web/dist 不动。"""
    dist = dist or paths.web_dist()
    progress(f"下载网页界面:{url}")
    with httpx.Client(transport=transport, follow_redirects=True, timeout=60) as client:
        data = client.get(url).raise_for_status().content
        expected = client.get(url + ".sha256").raise_for_status().text.split()[0].lower()
    if hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError("网页界面的压缩包校验失败(SHA256 不一致)")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        if "index.html" not in names or any(n.startswith(("/", "..")) or "/../" in n for n in names):
            raise RuntimeError("网页界面的压缩包内容不对")
        tmp = dist.with_name(dist.name + ".new")
        shutil.rmtree(tmp, ignore_errors=True)
        z.extractall(tmp)
    old = dist.with_name(dist.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if dist.exists():
        dist.rename(old)
    tmp.rename(dist)
    shutil.rmtree(old, ignore_errors=True)
    info = build_info(dist) or {}
    progress(f"网页界面已更新(构建于 {info.get('built_at', '?')},提交 {str(info.get('commit', '?'))[:7]})")
    return info
