"""网页上的实时画面:抓游戏窗口、显卡编码,经 MediaMTX 以 WebRTC 推给浏览器。"""

from .live import LiveStream, OfferResult

__all__ = ["LiveStream", "OfferResult"]
