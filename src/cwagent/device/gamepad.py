"""虚拟 Xbox 手柄(ViGEmBus 驱动 + vgamepad),用来开晶矿。

星铁收到手柄输入会切到手柄界面,晶矿上显示「开启 LT + RT」:同时按 LT、RT 一次打开全部晶矿;
之后动一下鼠标,游戏热切换回键鼠界面(维护者告知,2026-11-14 实机验证:4 个晶矿一次全开)。
用摇杆而不是按键来触发切换,避免误触界面上的操作。

需要 ViGEmBus 驱动。没有驱动时 available() 为 False,开晶矿改用鼠标逐个划;
驱动可以用 `cwagent gamepad setup` 安装(安装包随 vgamepad 一起发布)。
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

NO_WINDOW = 0x08000000


def vigembus_installed() -> bool:
    if sys.platform != "win32":
        return False
    r = subprocess.run(["sc", "query", "ViGEmBus"], capture_output=True, text=True, creationflags=NO_WINDOW)
    return r.returncode == 0


def installer() -> Path | None:
    """vgamepad 包里自带的 ViGEmBus 安装包(x64)。"""
    try:
        import importlib.util

        spec = importlib.util.find_spec("vgamepad")
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.origin:
        return None
    msi = Path(spec.origin).parent / "win" / "vigem" / "install" / "x64" / "ViGEmBusSetup_x64.msi"
    return msi if msi.exists() else None


def install_vigembus() -> None:
    """静默安装 ViGEmBus(需要管理员权限)。"""
    msi = installer()
    if msi is None:
        raise RuntimeError("找不到 ViGEmBus 安装包(vgamepad 没有安装?先运行 uv sync)")
    r = subprocess.run(["msiexec", "/i", str(msi), "/qn", "/norestart"], capture_output=True, text=True,
                       creationflags=NO_WINDOW)
    if r.returncode not in (0, 3010):   # 3010:安装成功,需要重启
        raise RuntimeError(f"安装失败(msiexec 返回 {r.returncode}),请以管理员身份运行")


class Gamepad:
    """整局复用一个虚拟手柄(新插入的手柄要等一会儿游戏才认),对局结束时拔掉。"""

    def __init__(self):
        self._pad = None
        self.error = ""

    def _ensure(self) -> bool:
        if self._pad is not None:
            return True
        if self.error:
            return False
        if not vigembus_installed():
            self.error = "没有安装 ViGEmBus 驱动(cwagent gamepad setup)"
            return False
        try:
            import vgamepad as vg

            self._pad = vg.VX360Gamepad()
        except Exception as e:  # noqa: BLE001 - 驱动或客户端库有问题就不用手柄
            self.error = f"虚拟手柄创建失败:{type(e).__name__}: {e}"
            return False
        time.sleep(2.0)   # 等系统和游戏认到新手柄
        return True

    def available(self) -> bool:
        return self._ensure()

    def nudge(self) -> bool:
        """推一下摇杆:游戏收到手柄输入后切到手柄界面(晶矿上显示「开启 LT + RT」)。"""
        if not self._ensure():
            return False
        pad = self._pad
        pad.left_joystick(x_value=0, y_value=-30000)
        pad.update()
        time.sleep(0.4)
        pad.reset()
        pad.update()
        time.sleep(0.3)      # 界面切过去要一会儿:调用方接着看「开启」提示出来没有(原来固定等 1 秒)
        return True

    def press_triggers(self) -> None:
        """同时按 LT、RT:打开全部晶矿。"""
        pad = self._pad
        pad.left_trigger(value=255)
        pad.right_trigger(value=255)
        pad.update()
        time.sleep(0.4)
        pad.reset()
        pad.update()
        time.sleep(0.3)      # 调用方接着看晶矿消失没有(原来固定等 1.5 秒)

    def press(self, button: str) -> None:
        """按一下手柄按键(A、B、X、Y)。在手柄界面里 B 是「返回」,可以收起商店。"""
        import vgamepad as vg

        pad = self._pad
        code = getattr(vg.XUSB_BUTTON, f"XUSB_GAMEPAD_{button.upper()}")
        pad.press_button(button=code)
        pad.update()
        time.sleep(0.15)
        pad.release_button(button=code)
        pad.update()
        time.sleep(1.0)

    def close(self) -> None:
        self._pad = None   # vgamepad 在对象释放时拔掉手柄
