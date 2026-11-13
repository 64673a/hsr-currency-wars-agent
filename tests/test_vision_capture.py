from PIL import Image

from cwagent.capture import CaptureError, DirectorySource, Frame
from cwagent.replay.collector import Collector
from cwagent.vision import regions


def test_regions_scale_and_draw(tmp_path):
    path = tmp_path / "regions.yaml"
    path.write_text("base: [1920, 1080]\nregions:\n  gold: {box: [100, 200, 50, 20], desc: 金币, calibrated: true}\n",
                    encoding="utf-8")
    rs = regions.load(path)
    assert rs.scaled("gold", (1920, 1080)) == (100, 200, 150, 220)
    assert rs.scaled("gold", (960, 540)) == (50, 100, 75, 110)
    img = Image.new("RGB", (960, 540), (0, 0, 0))
    assert rs.crop(img, "gold").size == (25, 10)
    out = regions.draw(img, rs)
    assert out.size == img.size and out.getpixel((50, 105)) != (0, 0, 0)  # 框线已画上


def test_repo_regions_file_loads():
    rs = regions.load()
    assert rs.base == (1920, 1080) and "stage" in rs.regions


class FakeSource:
    def __init__(self, frames):
        self.frames = list(frames)

    def grab(self):
        if not self.frames:
            raise CaptureError("没有了")
        return self.frames.pop(0)


def _frame(color, foreground=True):
    return Frame(image=Image.new("RGB", (192, 108), color), timestamp=1_700_000_000.123, foreground=foreground)


def test_collector_dedup_background_and_force(tmp_path):
    logs = []
    src = FakeSource([_frame((0, 0, 0)), _frame((0, 0, 1)), _frame((200, 0, 0), foreground=False),
                      _frame((200, 200, 200)), _frame((200, 200, 200))])
    c = Collector(src, tmp_path, log=logs.append)
    assert c.capture() is not None            # 第一张
    assert c.capture() is None                # 几乎没变化
    assert c.capture() is None                # 不在前台
    assert c.capture() is not None            # 变化明显
    assert c.capture(force=True) is not None  # 热键:不去重
    assert c.capture() is None                # 源读完,记为失败
    s = c.stats
    assert (s.saved, s.skipped_same, s.skipped_background, s.errors) == (3, 1, 1, 1)
    assert len(list(tmp_path.rglob("*.png"))) >= 1


def test_directory_source(tmp_path):
    for i in range(2):
        Image.new("RGB", (4, 4)).save(tmp_path / f"{i}.png")
    (tmp_path / "note.txt").write_text("x")
    src = DirectorySource(tmp_path)
    assert [src.grab().source, src.grab().source] == ["0.png", "1.png"]
