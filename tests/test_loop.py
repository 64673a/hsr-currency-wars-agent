from pathlib import Path

from PIL import Image

from cwagent.brain.screen_agent import Action, Decision
from cwagent.capture import DirectorySource
from cwagent.device import RecordingDevice
from cwagent.orchestrator.loop import Runner, RunOptions, Trace
from cwagent.vision.ocr import TextBox

FIXTURES = Path(__file__).resolve().parents[1] / "local" / "fixtures"


class FakeOcr:
    """按截图文件名返回预设的文字,避免测试依赖 OCR 模型。"""

    def __init__(self, pages):
        self.pages = pages
        self.i = 0

    def __call__(self, image, region=None):
        page = self.pages[min(self.i, len(self.pages) - 1)]
        self.i += 1
        return [TextBox(text=t, x=x, y=y, w=0.05, h=0.02, score=0.9) for t, x, y in page]


class FakeBrain:
    def __init__(self):
        self.calls = []

    def decide(self, boxes, *, screen, run_notes, history, hints="", names=None):
        self.calls.append(screen)
        return Decision("选追击概念股", [Action(type="click_text", text="追击概念股"), Action(type="click_text", text="确认")],
                        "环境:追击概念股", [], "s", "u", None)


def test_runner_reflexes_and_llm(tmp_path):
    for i in range(4):
        Image.new("RGB", (192, 108)).save(tmp_path / f"{i}.png")
    pages = [
        [("本场对局首领", 0.3, 0.18), ("本局奇遇", 0.63, 0.58), ("菇菇兔", 0.65, 0.635), ("下一步", 0.73, 0.91)],
        [("点击空白处继续", 0.5, 0.89)],
        [("投资环境", 0.5, 0.09), ("追击概念股", 0.77, 0.36), ("确认", 0.56, 0.91)],
        [("对局评价", 0.5, 0.3)],
    ]
    device = RecordingDevice(DirectorySource(tmp_path))
    brain = FakeBrain()
    met = []
    runner = Runner(device, brain, Trace(tmp_path / "run"), ocr=FakeOcr(pages), on_companion=met.append,
                    log=lambda m: None)
    import cwagent.orchestrator.loop as loop
    loop.ACTION_GAP = 0
    assert runner.run(RunOptions(max_steps=10)) == "settlement"
    assert met == ["菇菇兔"] and brain.calls == ["environment_choice"]
    assert device.actions == [("click", 0.73, 0.91, "left"), ("click", 0.5, 0.62, "left"),
                              ("click", 0.77, 0.36, "left"), ("click", 0.56, 0.91, "left")]
    assert "环境:追击概念股" in runner.run_notes
    lines = (tmp_path / "run" / "steps.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4 and (tmp_path / "run" / "shots" / "0001.jpg").exists()


def test_action_requires_fields():
    import pytest
    from pydantic import ValidationError

    from cwagent.brain.screen_agent import Action, action_text

    with pytest.raises(ValidationError):
        Action(type="click", y=0.5)
    with pytest.raises(ValidationError):
        Action(type="click_text")
    assert action_text(Action(type="scroll", notches=-6)).startswith("滚轮 -6")
    assert Action(type="wait").type == "wait"


def test_battle_screens_recognized():
    from cwagent.orchestrator.screens import classify
    from cwagent.vision.ocr import TextBox

    def boxes(*texts):
        return [TextBox(t, 0.5, 0.1 * i, 0.1, 0.03, 0.9) for i, t in enumerate(texts)]

    assert classify(boxes("1-6 0%", "29551")) == "battle"
    assert classify(boxes("敌方行动中", "UID:1")) == "battle"
    assert classify(boxes("总伤害", "19066")) == "battle"
    assert classify(boxes("1-7", "0%", "55")) == "battle"
    assert classify(boxes("备战阶段", "1-7", "出战")) == "prepare"


def test_trace_accepts_numpy_numbers(tmp_path):
    import json

    import numpy as np

    t = Trace(tmp_path / "run")
    t.write({"x": np.float32(0.5), "n": np.int64(3)})
    t.close()
    assert json.loads((tmp_path / "run" / "steps.jsonl").read_text(encoding="utf-8")) == {"x": 0.5, "n": 3}
