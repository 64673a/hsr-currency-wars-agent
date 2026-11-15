import pytest
from PIL import Image, ImageDraw

from cwagent import components, paths
from cwagent.capture import Frame
from cwagent.orchestrator import macros
from cwagent.vision.ocr import TextBox


BACKGROUND = (60, 50, 160)    # 面板的蓝紫色底


def screen(*orbs: tuple[int, int], grey: tuple[tuple[int, int], ...] = ()) -> Image.Image:
    """1920x1080 的备战截图:面板上画几个蓝色、灰白色的晶矿球(白边)。"""
    img = Image.new("RGB", (1920, 1080), BACKGROUND)
    d = ImageDraw.Draw(img)
    for (cx, cy), fill in [(o, (45, 156, 235)) for o in orbs] + [(o, (205, 205, 210)) for o in grey]:
        d.ellipse((cx - 28, cy - 28, cx + 28, cy + 28), fill=fill, outline="white", width=3)
    return img


def test_find_orbs():
    assert macros.find_orbs(screen()) == []
    found = macros.find_orbs(screen((1400, 300), (1520, 380), grey=((1330, 420),)))
    assert sorted((round(x, -1), round(y, -1)) for x, y, _ in found) == [(1330, 420), (1400, 300), (1520, 380)]
    # 上方道具栏里的圆形图标不算(M7A)
    assert macros.find_orbs(screen((1600, 160))) == []
    # 浮窗文字等不是晶矿颜色的圆不算
    img = screen()
    ImageDraw.Draw(img).ellipse((1372, 272, 1428, 328), fill=(20, 20, 20), outline="white", width=3)
    assert macros.find_orbs(img) == []


def test_timed_gift_is_not_an_orb():
    """奇遇的倒计时礼盒会在面板上停好几个回合,礼盒上的圆形不算晶矿(M7A 的测试)。"""
    img = screen((1524, 353))
    img.paste(Image.open(macros.GIFT_TEMPLATE).convert("RGB"), (1350, 139))
    ImageDraw.Draw(img).ellipse((1390, 212, 1430, 252), fill=(205, 205, 210), outline="white", width=3)
    found = macros.find_orbs(img)
    assert len(found) == 1 and abs(found[0][0] - 1524) <= 5


class Dev:
    """假游戏:手柄 LT+RT 一次全开;鼠标横向划过哪个晶矿就开哪个;备战席满时都开不了。"""

    def __init__(self, orbs, *, pad_ok=True, full=False, shop_pops=False):
        self.orbs, self.pad_ok, self.full, self.shop_pops = list(orbs), pad_ok, full, shop_pops
        self.calls, self.shop = [], False

    def pad_enter(self):
        self.calls.append("pad")
        self.shop = self.shop_pops and self.pad_ok
        return self.pad_ok

    def pad_press(self, button):
        self.calls.append(button)
        self.shop = False

    def pad_press_triggers(self):
        self.calls.append("lt+rt")
        if not self.full and not self.shop:
            self.orbs = []

    def pad_exit(self):
        self.calls.append("back")

    def drag(self, x1, y1, x2, y2, *, duration=0.4):
        self.calls.append("drag")
        y = y1 * 1080
        hit = [o for o in self.orbs if abs(o[1] - y) < 5 and x1 * 1920 <= o[0] <= x2 * 1920]
        if hit and not self.full:
            self.orbs.remove(hit[0])

    def grab(self):
        return Frame(image=screen(*self.orbs), timestamp=0)

    def ocr(self, image):
        texts = [("收起", 0.845, 0.909)] if self.shop else [("开启", 0.76, 0.32)] if self.orbs else []
        if self.full and self.calls[-1] in ("drag", "back"):
            texts.append(("备战席已满,请出售角色或提升等级", 0.5, 0.28))
        return [TextBox(t, x, y, 0.05, 0.02, 0.9) for t, x, y in texts]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(macros.time, "sleep", lambda s: None)


ORBS = [(1400, 300), (1520, 380)]


def test_open_crystals_by_pad():
    logs = []
    dev = Dev(ORBS)
    r = macros.open_crystals(dev, dev.grab().image, logs.append, ocr=dev.ocr)
    assert dev.calls == ["pad", "lt+rt", "back"] and r.opened and (r.before, r.after) == (2, 0)
    assert r.text() == "划开晶矿(2 个)" and "手柄" in logs[-1]
    assert macros.open_crystals(dev, dev.grab().image, logs.append, ocr=dev.ocr) is None


def test_pad_closes_shop_that_popped_open():
    """切到手柄界面时商店自己展开了(挡住「开启」提示):按 B 收起再开。"""
    dev = Dev(ORBS, shop_pops=True)
    r = macros.open_crystals(dev, dev.grab().image, lambda m: None, ocr=dev.ocr)
    assert dev.calls == ["pad", "B", "lt+rt", "back"] and r.after == 0


def test_mouse_swipes_each_orb_without_pad():
    """没有手柄驱动:鼠标按最新位置逐个横向划过(M7A 的 _gather)。"""
    dev = Dev(ORBS, pad_ok=False)
    r = macros.open_crystals(dev, dev.grab().image, lambda m: None, ocr=dev.ocr)
    assert dev.calls == ["pad", "drag", "drag"] and r.after == 0 and r.how == "鼠标"


def test_bench_full_is_not_a_failure():
    dev = Dev(ORBS, pad_ok=False, full=True)
    r = macros.open_crystals(dev, dev.grab().image, lambda m: None, ocr=dev.ocr)
    assert not r.opened and r.full and r.after == 2
    assert dev.calls.count("drag") == 2          # 连续两次没开成就停
    assert "备战席满了" in r.text()


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
