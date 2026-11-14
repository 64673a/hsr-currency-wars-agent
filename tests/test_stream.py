import asyncio
import sys
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from cwagent.config import ConfigStore, StreamSettings
from cwagent.memory import MemoryStore
from cwagent.server import create_app
from cwagent.stream import LiveStream, OfferResult
from cwagent.stream.live import ffmpeg_args, mediamtx_config


def test_mediamtx_config_local_only():
    s = StreamSettings()
    conf = mediamtx_config(s, "h264_nvenc", Path("C:/Program Files/cw/ffmpeg.exe"))
    assert conf["webrtcAddress"].startswith("127.0.0.1:")
    assert conf["rtspAddress"].startswith("127.0.0.1:")
    assert conf["webrtcLocalUDPAddress"] == ":8189"
    assert conf["webrtcIPsFromInterfaces"] is True and conf["webrtcAdditionalHosts"] == []
    cmd = conf["paths"]["game"]["runOnDemand"]
    assert cmd.startswith('"C:/Program Files/cw/ffmpeg.exe" ')   # 带空格的路径要加引号
    assert "\\" not in cmd                                       # MediaMTX 会把反斜杠当转义符

    fixed = mediamtx_config(StreamSettings(hosts=["192.168.0.200"]), "h264_nvenc", Path("ffmpeg.exe"))
    assert fixed["webrtcIPsFromInterfaces"] is False and fixed["webrtcAdditionalHosts"] == ["192.168.0.200"]


def test_ffmpeg_args_per_encoder():
    s = StreamSettings(fps=30, bitrate_mbps=12)
    nv = ffmpeg_args(s, "h264_nvenc")
    assert "gfxcapture=window_exe=StarRail.exe:max_framerate=30" in nv
    assert nv[nv.index("-b:v") + 1] == "12M" and nv[nv.index("-g") + 1] == "60" and "-bf" in nv
    cpu = ffmpeg_args(s, "libx264")
    assert any("hwdownload,format=bgra" in a for a in cpu)       # CPU 编码要先把显存里的帧拷出来


def test_start_needs_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    live = LiveStream(StreamSettings())
    assert live.start() == "只能在 Windows 上使用"
    assert live.status() == {"available": False, "reason": "只能在 Windows 上使用", "encoder": None}


def test_offer_maps_mediamtx_responses():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(200)
        if b"nogame" in request.content:
            return httpx.Response(404, text="no stream is available on path 'game'")
        assert request.headers["content-type"] == "application/sdp"
        return httpx.Response(201, text="v=0 answer", headers={"Location": "/game/whep/abc-123"})

    live = LiveStream(StreamSettings(), transport=httpx.MockTransport(handler))
    ok = asyncio.run(live.offer("v=0 offer"))
    assert ok == OfferResult(201, "v=0 answer", "abc-123")
    assert asyncio.run(live.offer("nogame")).status == 404
    asyncio.run(live.close("abc-123"))


class FakeLive:
    def __init__(self):
        self.closed = []

    def running(self):
        return True

    def status(self):
        return {"available": True, "reason": None, "encoder": "h264_nvenc"}

    async def offer(self, sdp):
        return OfferResult(201, f"answer to {sdp}", "s1") if sdp == "offer" else OfferResult(404, error="没有游戏画面")

    async def close(self, session):
        self.closed.append(session)


def test_live_endpoints(tmp_path):
    fake = FakeLive()
    app = create_app(config_store=ConfigStore(tmp_path / "config.yaml"), knowledge_path=tmp_path / "k.sqlite",
                     memory_store=MemoryStore(tmp_path / "m.yaml", tmp_path / "m.sqlite"), web_dist=tmp_path / "dist",
                     runs_dir=tmp_path / "runs", device_factory=None, live=fake)
    client = TestClient(app)
    assert client.get("/api/live").json()["available"] is True
    r = client.post("/api/live/whep", content="offer", headers={"Content-Type": "application/sdp"})
    assert r.status_code == 201 and r.text == "answer to offer"
    assert r.headers["content-type"].startswith("application/sdp")
    assert r.headers["location"] == "/api/live/whep/s1"
    assert client.post("/api/live/whep", content="x").status_code == 404
    assert client.delete("/api/live/whep/s1").status_code == 204 and fake.closed == ["s1"]

    off = create_app(config_store=ConfigStore(tmp_path / "config.yaml"), knowledge_path=tmp_path / "k.sqlite",
                     memory_store=MemoryStore(tmp_path / "m.yaml", tmp_path / "m.sqlite"), web_dist=tmp_path / "dist",
                     runs_dir=tmp_path / "runs", device_factory=None)
    c2 = TestClient(off)
    assert c2.get("/api/live").json()["available"] is False
    assert c2.post("/api/live/whep", content="offer").status_code == 503
