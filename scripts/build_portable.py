"""组装 Windows 便携包:dist/hsr-cw-agent-windows-x64-v<版本>.zip。在 Windows 上运行(GitHub Actions 的 release 工作流)。

包里内置运行需要的全部东西,用户解压后双击 start.bat 即可:
- python/:独立的 CPython(uv 管理的 python-build-standalone,和开发环境同一个版本),依赖直接装在它的 site-packages;
- 程序本体(装进 site-packages)、knowledge/、config/、构建好的网页 web/dist;
- data/tools/:ffmpeg、MediaMTX(实时画面);OCR 模型(构建时先跑一次识别下载好);
- start.bat、uninstall.bat、使用说明.txt、LICENSE、THIRD_PARTY_NOTICES.txt。
ViGEmBus 驱动的安装包随 vgamepad 在 site-packages 里,第一次启动时由程序安装。

前置:已经构建好 web/dist(npm run build)。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = (ROOT / ".python-version").read_text().strip()
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
NAME = f"hsr-cw-agent-windows-x64-v{VERSION}"
DIST = ROOT / "dist"
OUT = DIST / NAME


def run(*cmd, env=None, **kw) -> str:
    print("+", " ".join(map(str, cmd)), flush=True)
    r = subprocess.run([str(c) for c in cmd], text=True, capture_output=True, encoding="utf-8", errors="replace",
                       env={**os.environ, **(env or {})}, **kw)
    if r.stdout.strip():
        print(r.stdout.strip()[-2000:])
    if r.returncode != 0:
        print(r.stderr.strip()[-4000:], file=sys.stderr)
        raise subprocess.CalledProcessError(r.returncode, cmd)
    return r.stdout


def py(*args, **kw) -> str:
    return run(OUT / "python" / "python.exe", *args, env={"CWAGENT_HOME": str(OUT), "PYTHONIOENCODING": "utf-8"}, **kw)


def main() -> None:
    for stream in (sys.stdout, sys.stderr):   # CI 机器的控制台是 cp1252,打印中文会报错
        stream.reconfigure(encoding="utf-8", errors="replace")
    if sys.platform != "win32":
        sys.exit("便携包只能在 Windows 上组装")
    if not (ROOT / "web" / "dist" / "index.html").exists():
        sys.exit("先构建网页:cd web && npm ci && npm run build")
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)

    # 1. Python:复制 uv 管理的独立 CPython;去掉「外部管理」标记,这份是包里私有的
    run("uv", "python", "install", PYTHON_VERSION)
    # 在 uv run 里 VIRTUAL_ENV 指向项目的 venv,find 会优先返回它;要的是 uv 管理的独立 CPython 本体
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    exe = Path(subprocess.run(["uv", "python", "find", "--no-project", "--system", "--managed-python", PYTHON_VERSION],
                              text=True, capture_output=True, check=True, env=env).stdout.strip())
    if (exe.parent / "pyvenv.cfg").exists() or (exe.parent.parent / "pyvenv.cfg").exists():
        sys.exit(f"找到的是虚拟环境而不是独立的 Python:{exe}")
    shutil.copytree(exe.parent, OUT / "python", ignore=shutil.ignore_patterns("__pycache__"))
    (OUT / "python" / "Lib" / "EXTERNALLY-MANAGED").unlink(missing_ok=True)

    # 2. 依赖(按 uv.lock 的版本)和程序本体
    req = DIST / "requirements.txt"
    run("uv", "export", "--frozen", "--no-dev", "--no-emit-project", "--no-hashes", "-o", req, cwd=ROOT)
    target = ["--python", OUT / "python" / "python.exe"]
    build_env = {"VGAMEPAD_SKIP_VIGEMBUS_INSTALL": "true"}   # 驱动由程序自己装,构建时不弹安装程序
    run("uv", "pip", "install", *target, "-r", req, env=build_env)
    run("uv", "pip", "install", *target, "--no-deps", ROOT, env=build_env)

    # 3. 程序在运行目录下找的资源
    for d in ("knowledge", "config"):
        shutil.copytree(ROOT / d, OUT / d)
    shutil.copytree(ROOT / "web" / "dist", OUT / "web" / "dist")
    for f in ("LICENSE", "README.md", "config.example.yaml"):
        shutil.copy2(ROOT / f, OUT / f)
    for f in (ROOT / "packaging").iterdir():
        if f.suffix in (".bat", ".txt"):
            shutil.copy2(f, OUT / f.name)

    # 4. 外部程序和 OCR 模型:用包里的 Python 跑一遍下载
    py("-c", "from cwagent.stream.setup import install; install()")
    py("-c", "from PIL import Image; from cwagent.vision.ocr import Ocr; Ocr()(Image.new('RGB', (96, 32), 'white'))")

    # 5. 冒烟检查
    py("-m", "cwagent", "--help")
    py("-c", "import cwagent.server, cwagent.orchestrator.loop, cwagent.capture.windows, cwagent.device.windows; "
             "from cwagent.server import create_app; create_app(device_factory=None)")

    # 6. 清掉运行时生成的文件,写第三方声明
    shutil.rmtree(OUT / "data" / "stream", ignore_errors=True)
    for f in ("config.yaml", "data/installed.json"):
        (OUT / f).unlink(missing_ok=True)
    notices()

    archive = shutil.make_archive(str(DIST / NAME), "zip", root_dir=DIST, base_dir=NAME)
    digest = hashlib.sha256(Path(archive).read_bytes()).hexdigest()
    Path(archive + ".sha256").write_text(f"{digest}  {Path(archive).name}\n", encoding="ascii")
    print(f"完成:{archive}({Path(archive).stat().st_size >> 20} MB)")


STATIC_NOTICES = """\
CPython {python}
  许可:Python Software Foundation License。来源:https://github.com/astral-sh/python-build-standalone

ffmpeg 8.1(BtbN FFmpeg-Builds,GPL 构建) data/tools/ffmpeg/ffmpeg.exe
  许可:GPL-3.0-or-later,见 data/tools/ffmpeg/LICENSE.txt。
  对应源码:https://github.com/BtbN/FFmpeg-Builds(构建脚本)与 https://git.ffmpeg.org/ffmpeg.git(n8.1 分支)

MediaMTX {mediamtx} data/tools/mediamtx/mediamtx.exe
  许可:MIT,见 data/tools/mediamtx/LICENSE。来源:https://github.com/bluenviron/mediamtx

ViGEmBus(随 vgamepad 分发的安装包)
  许可:BSD-3-Clause。来源:https://github.com/nefarius/ViGEmBus

RapidOCR 模型(PP-OCR) python/Lib/site-packages/rapidocr/models
  许可:Apache-2.0。来源:https://github.com/RapidAI/RapidOCR
"""


LIST_PACKAGES = r"""
import importlib.metadata as m
rows = []
for d in m.distributions():
    meta = d.metadata
    lic = meta.get("License-Expression") or meta.get("License") or ""
    if not lic or len(lic) > 80 or "\n" in lic:
        cls = [c.split(" :: ")[-1] for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
        lic = ", ".join(cls) or "见包内的许可文件"
    home = meta.get("Home-page") or next((u.split(", ", 1)[-1] for u in (meta.get_all("Project-URL") or [])), "")
    rows.append(f"{meta['Name']} {d.version}\n  许可:{lic}\n  来源:{home}")
print("\n\n".join(sorted(rows, key=str.lower)))
"""


def notices() -> None:
    from cwagent.stream.setup import MEDIAMTX_VERSION

    head = ("本便携包内置的第三方组件。cwagent 本身以 GPL-3.0-only 发布,源码见 "
            "https://github.com/64673a/hsr-currency-wars-agent\n\n")
    packages = py("-c", LIST_PACKAGES)
    text = head + STATIC_NOTICES.format(python=PYTHON_VERSION, mediamtx=MEDIAMTX_VERSION) + "\nPython 包:\n\n" + packages
    (OUT / "THIRD_PARTY_NOTICES.txt").write_text(text, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
