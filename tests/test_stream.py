import asyncio
import sys
from pathlib import Path

import httpx
import pytest
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


def test_record_args_and_timing():
    from cwagent.stream.record import first_frame_at, mux_args, stamp, video_args

    nv = video_args("hevc_nvenc", "gfxcapture=hwnd=123:max_framerate=60", Path("run/video.mp4"))
    graph = nv[nv.index("-filter_complex") + 1]
    # 显存里的帧只能靠 setparams 改颜色标记,否则 mp4 的 colr 写成 RGB 矩阵、全范围
    assert graph.startswith("gfxcapture=hwnd=123") and "setparams=colorspace=bt470bg:range=tv" in graph
    assert nv[nv.index("-c:v") + 1] == "hevc_nvenc" and nv[nv.index("-r") + 1] == "60" and "cfr" in nv
    assert "+frag_keyframe+empty_moov+default_base_moof" in nv and nv[-1] == str(Path("run/video.mp4"))
    assert nv[nv.index("-progress") + 1] == "pipe:1"   # 读进度推算第 0 帧的时刻
    cpu = video_args("libx264", "gfxcapture=hwnd=1", Path("v.mp4"))
    assert cpu[cpu.index("-filter_complex") + 1].endswith(",hwdownload,format=bgra")

    late = mux_args(Path("v.mp4"), Path("v.pcm"), 0.25, Path("o.mp4"))
    assert late[late.index("-itsoffset") + 1] == "0.250" and "-ss" not in late
    early = mux_args(Path("v.mp4"), Path("v.pcm"), -0.5, Path("o.mp4"))
    assert early[early.index("-ss") + 1] == "0.500" and early.index("-ss") < early.index(str(Path("v.pcm")))
    assert early[early.index("-c:v") + 1] == "copy"

    # 进度是事后打印的,每个样本只会偏晚:取墙钟减已输出时长的最小值;还没出帧的样本不算
    assert first_frame_at([(100.30, 0.0), (100.40, 0.2), (100.52, 0.3), (100.61, 0.4)]) == pytest.approx(100.20)
    assert first_frame_at([(100.0, 0.0)]) is None
    import re

    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.500[+-]\d\d:\d\d", stamp(1795587624.5))   # 同 timing.start


def test_record_start_needs_windows(monkeypatch, tmp_path):
    from cwagent.stream.record import Recorder

    monkeypatch.setattr(sys, "platform", "linux")
    r = Recorder(tmp_path)
    assert r.start() == "只能在 Windows 上录像"
    assert r.stop() == {"file": "video.mp4", "encoder": "", "started_at": None}


def test_run_session_records_into_the_run_dir(monkeypatch, tmp_path):
    import json

    from cwagent.orchestrator import session
    from cwagent.orchestrator.loop import Trace
    from cwagent.stream import record

    calls = []

    class FakeRecorder:
        def __init__(self, out_dir, *, log=print):
            calls.append(("init", out_dir))

        def start(self):
            calls.append("start")
            return None

        def stop_info(self):
            return {"file": "video.mp4", "encoder": "hevc_nvenc", "started_at": "2026-11-25T14:20:24.313+08:00"}

        def stop(self):
            calls.append("stop")
            return {**self.stop_info(), "audio": "已合入(12 秒)"}

    class FakeRunner:
        outcome = None

        def __init__(self, root):
            self.trace = Trace(root)

        def run(self, opts):
            calls.append("run")
            meta = json.loads((self.trace.root / "meta.json").read_text(encoding="utf-8"))
            assert meta["video"]["started_at"]   # 开录就写进 meta,对局中途崩了也对得上时间
            return "stopped"

    monkeypatch.setattr(record, "Recorder", FakeRecorder)
    runner = FakeRunner(tmp_path / "run")
    assert session.run_session(runner, object(), record=True, log=lambda m: None) == "stopped"
    assert calls == [("init", tmp_path / "run"), "start", "run", "stop"]
    meta = json.loads((tmp_path / "run" / "meta.json").read_text(encoding="utf-8"))
    assert meta["video"]["audio"] == "已合入(12 秒)" and meta["result"] == "stopped"

    class NoGame(FakeRecorder):
        def start(self):
            return "没找到游戏窗口"

    monkeypatch.setattr(record, "Recorder", NoGame)
    calls.clear()
    runner = FakeRunner(tmp_path / "run2")
    runner.run = lambda opts: "stopped"
    assert session.run_session(runner, object(), record=True, log=lambda m: None) == "stopped"
    meta = json.loads((tmp_path / "run2" / "meta.json").read_text(encoding="utf-8"))
    assert meta["video"] == {"error": "没找到游戏窗口"}
