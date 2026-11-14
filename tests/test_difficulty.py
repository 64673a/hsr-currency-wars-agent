from PIL import Image

from cwagent.capture import Frame
from cwagent.orchestrator import difficulty
from cwagent.vision.ocr import TextBox

# 假的难度列表:A7 有 9 层(敌人难度 52~60,词缀 3),A8 有 20 层(61~80,词缀 4);账号最高 A8-20
LEVELS = [(3, 52 + i) for i in range(9)] + [(4, 61 + i) for i in range(20)]


class ListDevice:
    def __init__(self, start):
        self.pos, self.ticks = start, 0

    def grab(self):
        return Frame(image=Image.new("RGB", (8, 8)), timestamp=0)

    def click(self, x, y, *, button="left"):
        if abs(x - 0.726) < 0.01:          # 返回最高职级
            self.pos, self.ticks = len(LEVELS) - 1, 0

    def scroll(self, x, y, notches):
        self.ticks += notches               # 3 格变一层
        while self.ticks >= 3:
            self.pos, self.ticks = min(self.pos + 1, len(LEVELS) - 1), self.ticks - 3
        while self.ticks <= -3:
            self.pos, self.ticks = max(self.pos - 1, 0), self.ticks + 3


def run(target, start=15):
    dev = ListDevice(start)
    logs = []
    ocr = lambda image: [TextBox("返回最高职级", 0.726, 0.894, 0.08, 0.03, 0.9)]   # noqa: E731
    read = lambda image, o: (LEVELS[dev.pos][1], LEVELS[dev.pos][0])            # noqa: E731
    ok = difficulty.select(dev, ocr, target, logs.append, sleep=lambda s: None, read=read)
    return ok, LEVELS[dev.pos], logs


def test_select_exact_layers():
    assert run("A8-1")[:2] == (True, (4, 61))
    assert run("A8-6")[:2] == (True, (4, 66))
    assert run("A8-20")[:2] == (True, (4, 80))


def test_select_rejects_other_groups_and_bad_layers():
    ok, _, logs = run("A7-3")
    assert not ok and "只支持 A8" in logs[-1]
    assert not run("A8-25")[0]
    assert difficulty.parse_target("a8-3") == (8, 3) and difficulty.parse_target("A8") is None


def test_read_values_joins_split_digits():
    calls = []

    def ocr(image):
        calls.append(image.size)
        return [TextBox("2", 0.6, 0.5, 0.1, 0.1, 0.9), TextBox("7", 0.4, 0.5, 0.1, 0.1, 0.9), TextBox("敌人难度", 0.5, 0.9, 0.3, 0.1, 0.9)] \
            if len(calls) == 1 else [TextBox("4", 0.5, 0.5, 0.1, 0.1, 0.9)]

    assert difficulty.read_values(Image.new("RGB", (1920, 1080)), ocr) == (72, 4)
    assert calls[0][0] == calls[0][1] * 0 + calls[0][0] and calls[0][0] > 300   # 截出来放大过
