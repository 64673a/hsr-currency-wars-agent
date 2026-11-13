"""截图源。设计见 docs/TECH_DESIGN.md 6.1。

感知层只接收图像,不直接接触窗口;截图源可替换,非 Windows 平台用 DirectorySource 对着截图目录测试。
"""

from .base import CaptureError, DirectorySource, Frame

__all__ = ["CaptureError", "DirectorySource", "Frame"]
