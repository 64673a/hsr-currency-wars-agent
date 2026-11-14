from PIL import Image

from cwagent.capture import Frame
from cwagent.orchestrator import difficulty
from cwagent.vision.ocr import TextBox

# 假的难度列表:A7 有 9 层(敌人难度 49~57),A8 有 20 层(61~80);账号最高 A8-20
LEVELS = [(7, 49 + i) for i in range(9)] + [(8, 61 + i) for i in range(20)]


class ListDevice:
    def __init__(self, start):
        self.pos, self.ticks = start, 0

    def grab(self):
        return Frame(image=Image.new("RGB", (8, 8)), timestamp=0)

    def click(self, x, y, *, button="left"):
        if abs(x - 0.725) < 0.01:          # 返回最高职级
            self.pos, self.ticks = len(LEVELS) - 1, 0

    def scroll(self, x, y, notches):
        self.ticks += notches               # 3 格变一层
        while self.ticks >= 3:
            self.pos, self.ticks = min(self.pos + 1, len(LEVELS) - 1), self.ticks - 3
        while self.ticks <= -3:
            self.pos, self.ticks = max(self.pos - 1, 0), self.ticks + 3
        if notches > 0 and 0 < self.ticks:  # 往上滚 1 格也能翻一层(简化)
            self.pos, self.ticks = min(self.pos + 1, len(LEVELS) - 1), 0


def ocr_for(dev):
    names = {7: "A7资本帝王", 8: "A8财富造物主"}

    def ocr(image):
        g, v = LEVELS[dev.pos]
        return [TextBox(names[g], 0.089, 0.297, 0.1, 0.03, 0.9), TextBox(str(v), 0.285, 0.907, 0.03, 0.03, 0.9),
                TextBox("敌人难度", 0.284, 0.940, 0.05, 0.02, 0.9), TextBox("返回最高职级", 0.725, 0.908, 0.08, 0.03, 0.9),
                TextBox("开始对局", 0.881, 0.908, 0.08, 0.03, 0.9)]
    return ocr


def run(target, start=15):
    dev = ListDevice(start)
    logs = []
    ok = difficulty.select(dev, ocr_for(dev), target, logs.append, sleep=lambda s: None)
    return ok, LEVELS[dev.pos], logs


def test_select_exact_layers():
    assert run("A8-1")[:2] == (True, (8, 61))
    assert run("A8-7")[:2] == (True, (8, 67))
    assert run("A8-20")[:2] == (True, (8, 80))
    assert run("A7-3")[:2] == (True, (7, 51))


def test_select_rejects_impossible():
    ok, _, logs = run("A9-1")
    assert not ok and "看不懂" not in logs[-1] and "最高只到 A8" in logs[-1]
    ok, _, logs = run("A8-25")
    assert not ok
    assert difficulty.parse_target("a8-3") == (8, 3) and difficulty.parse_target("A8") is None


def test_read_values():
    boxes = ocr_for(ListDevice(20))(None)
    assert difficulty.read(boxes) == (8, 72)
