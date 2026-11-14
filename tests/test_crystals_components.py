import numpy as np
import pytest
from PIL import Image, ImageDraw

from cwagent import components, paths
from cwagent.capture import Frame
from cwagent.orchestrator import macros


def screen_with_panel(panel: Image.Image) -> Image.Image:
    """把晶矿面板的灰度图放回 1920x1080 截图里对应的位置。"""
    x, y, w, h = macros.ORB_AREA
    box = (int(x * 1920), int(y * 1080), int((x + w) * 1920), int((y + h) * 1080))
    img = Image.new("RGB", (1920, 1080), (40, 30, 90))
    img.paste(panel.convert("RGB").resize((box[2] - box[0], box[3] - box[1])), box[:2])
    return img


def empty_screen() -> Image.Image:
    return screen_with_panel(Image.open(next(macros.BUNDLED_EMPTY.glob("*.png"))))


def with_orbs(img: Image.Image) -> Image.Image:
    img = img.copy()
    d = ImageDraw.Draw(img)
    for cx, cy in ((1400, 300), (1500, 300)):
        d.ellipse((cx - 35, cy - 35, cx + 35, cy + 35), fill=(40, 120, 255))   # 晶矿的蓝色
    return img


@pytest.fixture
def learned(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "templates_dir", lambda: tmp_path / "templates")
    return tmp_path / "templates" / "crystals"


def test_has_crystals(learned):
    assert macros.empty_diff(empty_screen()) <= macros.EMPTY_DIFF
    assert not macros.has_crystals(empty_screen())
    assert macros.has_crystals(with_orbs(empty_screen()))
    # 换了位面:背景对不上空面板、但没有晶矿颜色 → 不算有晶矿,记成新的空面板,下次直接对上
    dark = (20 + np.arange(108) * 0.7).astype(np.uint8)      # 暗色渐变,不含晶矿的亮色
    other = screen_with_panel(Image.fromarray(np.tile(dark, (94, 1))))
    assert not macros.has_crystals(other) and len(list(learned.glob("*.png"))) == 1
    assert macros.empty_diff(other) <= macros.EMPTY_DIFF


class Dev:
    def __init__(self, pad_ok):
        self.pad_ok, self.calls = pad_ok, []

    def pad_enter(self):
        self.calls.append("pad")
        return self.pad_ok

    def pad_press_triggers(self):
        self.calls.append("lt+rt")

    def pad_exit(self):
        self.calls.append("back")

    def drag_path(self, points, *, duration=1.5):
        self.calls.append(("path", len(points)))

    def grab(self):
        return Frame(image=empty_screen(), timestamp=0)


def test_open_crystals_pad_then_mouse(learned, monkeypatch):
    monkeypatch.setattr(macros.time, "sleep", lambda s: None)
    logs = []
    dev = Dev(True)
    assert macros.open_crystals(dev, with_orbs(empty_screen()), logs.append) is True
    assert dev.calls == ["pad", "lt+rt", "back"] and "手柄" in logs[-1]
    dev = Dev(False)                     # 没有手柄驱动:一笔划过所有晶矿位置
    assert macros.open_crystals(dev, with_orbs(empty_screen()), logs.append) is True
    assert dev.calls == ["pad", ("path", len(macros.ORB_PATH))] and "鼠标" in logs[-1]
    assert macros.open_crystals(dev, empty_screen(), logs.append) is None


@pytest.fixture
def home(tmp_path, monkeypatch):
    for name, rel in (("data_dir", "data"), ("runs_dir", "runs"), ("web_dist", "web/dist")):
        monkeypatch.setattr(paths, name, lambda rel=rel: tmp_path / rel)
    monkeypatch.setattr(paths, "config_file", lambda: tmp_path / "config.yaml")
    monkeypatch.setattr(paths, "tools_dir", lambda: tmp_path / "data" / "tools")
    monkeypatch.setattr(paths, "stream_dir", lambda: tmp_path / "data" / "stream")
    for d in ("data/tools/ffmpeg", "data/stream", "data/templates", "runs/r1", "web/dist"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / "data" / "knowledge.sqlite").write_text("x")
    (tmp_path / "config.yaml").write_text("x")
    monkeypatch.setattr(components.sys, "platform", "linux")
    return tmp_path


def test_uninstall_keeps_data_by_default(home):
    plan = "\n".join(components.plan())
    assert "tools" in plan and "config.yaml" not in plan
    components.uninstall(log=lambda m: None)
    assert not (home / "data" / "tools").exists() and not (home / "data" / "stream").exists()
    assert (home / "data" / "knowledge.sqlite").exists() and (home / "runs" / "r1").exists()
    assert (home / "config.yaml").exists() and (home / "web" / "dist").exists()


def test_uninstall_all_removes_everything(home):
    plan = "\n".join(components.plan(everything=True))
    assert "对局记录" in plan and "API key" in plan and "知识库" in plan
    components.uninstall(log=lambda m: None, everything=True)
    for p in ("data", "runs", "config.yaml", "web/dist"):
        assert not (home / p).exists(), p
