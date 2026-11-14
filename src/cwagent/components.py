"""cwagent 在系统里安装的外部组件:默认自动安装,卸载时只撤销自己装的。

- ViGEmBus 虚拟手柄驱动(开晶矿用):系统里原来就有的(其他软件装的)不记录,卸载时也不动;
- 防火墙规则「cwagent live view」(实时画面的 UDP 端口);
- 下载的程序 data/tools(ffmpeg、MediaMTX)和运行时文件 data/stream。

装了什么记在 data/installed.json。`cwagent uninstall` 按记录撤销,配置、对局记录、知识库等数据保留;
`cwagent uninstall --all` 再把这些数据也删掉(data/、runs/、config.yaml、下载的网页),之后删掉程序目录即可。
uv 管理的 Python 是各项目共用的,不删;需要时自己运行 `uv python uninstall`。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from collections.abc import Callable

from . import paths
from .device import gamepad

NO_WINDOW = 0x08000000
FIREWALL_RULE = "cwagent live view"


def _state_file():
    return paths.data_dir() / "installed.json"


def load() -> dict:
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(state: dict) -> None:
    path = _state_file()
    if not state:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def record(key: str, value) -> None:
    state = load()
    state[key] = value
    _save(state)


def _is_admin() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


# ---------------------------------------------------------------- 安装

def ensure_vigembus(log: Callable[[str], None] = print) -> None:
    """没有 ViGEmBus 就装上(需要管理员权限),并记下是 cwagent 装的。"""
    if sys.platform != "win32" or gamepad.vigembus_installed():
        return
    if not _is_admin():
        log("没有以管理员身份运行,跳过安装虚拟手柄驱动(开晶矿会改用鼠标)")
        return
    log("安装 ViGEmBus 虚拟手柄驱动(开晶矿用,卸载:cwagent uninstall)")
    try:
        gamepad.install_vigembus()
    except Exception as e:  # noqa: BLE001 - 装不上只是开晶矿改用鼠标
        log(f"虚拟手柄驱动安装失败:{e}(开晶矿会改用鼠标)")
        return
    record("vigembus", {"installed_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    log("虚拟手柄驱动已安装")


def ensure_all(log: Callable[[str], None] = print) -> None:
    ensure_vigembus(log)


# ---------------------------------------------------------------- 卸载

def _vigembus_product_codes() -> list[str]:
    """注册表里 ViGEmBus 的 MSI 产品码(卸载用)。"""
    import winreg

    codes = []
    root = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root) as key:
        for i in range(winreg.QueryInfoKey(key)[0]):
            name = winreg.EnumKey(key, i)
            try:
                with winreg.OpenKey(key, name) as sub:
                    display = winreg.QueryValueEx(sub, "DisplayName")[0]
            except OSError:
                continue
            if "Virtual Gamepad Emulation Bus" in display and name.startswith("{"):
                codes.append(name)
    return codes


def _data_targets() -> list:
    """--all 时额外删除的数据。"""
    return [paths.data_dir(), paths.runs_dir(), paths.config_file(), paths.web_dist()]


def plan(*, everything: bool = False) -> list[str]:
    """卸载会做的事(给用户确认)。"""
    state = load()
    items = []
    if state.get("vigembus"):
        items.append("卸载 ViGEmBus 虚拟手柄驱动(cwagent 安装的)")
    elif sys.platform == "win32" and gamepad.vigembus_installed():
        items.append("保留 ViGEmBus 虚拟手柄驱动(系统里原来就有,不是 cwagent 装的)")
    if sys.platform == "win32":
        items.append(f"删除防火墙规则「{FIREWALL_RULE}」")
    if everything:
        labels = {paths.data_dir(): "知识库、模板、本机记忆、伙伴档案、下载的程序",
                  paths.runs_dir(): "对局记录", paths.config_file(): "配置,含 API key", paths.web_dist(): "网页界面"}
        items += [f"删除 {p}({labels[p]})" for p in _data_targets() if p.exists()]
    else:
        items += [f"删除 {d}" for d in (paths.tools_dir(), paths.stream_dir()) if d.exists()]
    return items


def uninstall(log: Callable[[str], None] = print, *, everything: bool = False) -> None:
    state = load()
    if sys.platform == "win32":
        # 先停掉实时画面的进程,否则 data/tools 删不掉
        exe = str(paths.tools_dir()).replace("'", "''")
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        f"Get-Process mediamtx,ffmpeg -ErrorAction SilentlyContinue | "
                        f"Where-Object {{ $_.Path -like '{exe}*' }} | Stop-Process -Force"],
                       capture_output=True, creationflags=NO_WINDOW)
        if state.get("vigembus"):
            codes = _vigembus_product_codes()
            for code in codes:
                r = subprocess.run(["msiexec", "/x", code, "/qn", "/norestart"], capture_output=True,
                                   creationflags=NO_WINDOW)
                log(f"卸载虚拟手柄驱动 {code}:" + ("完成" if r.returncode in (0, 3010) else f"失败(返回 {r.returncode})"))
            if not codes:
                log("没有找到虚拟手柄驱动,可能已经卸载了")
            state.pop("vigembus", None)
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        f"Get-NetFirewallRule -DisplayName '{FIREWALL_RULE}' -ErrorAction SilentlyContinue | "
                        "Remove-NetFirewallRule"], capture_output=True, creationflags=NO_WINDOW)
        log(f"已删除防火墙规则「{FIREWALL_RULE}」")
    for d in (paths.tools_dir(), paths.stream_dir()):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            log(f"已删除 {d}")
    state.pop("firewall", None)
    _save(state)
    if everything:
        for p in _data_targets():
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            elif p.exists():
                p.unlink()
            else:
                continue
            log(f"已删除 {p}")
