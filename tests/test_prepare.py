from cwagent.brain.screen_agent import Action
from cwagent.orchestrator.prepare import PrepareExecutor, full, shift
from cwagent.vision.board import Slot


class Dev:
    def __init__(self):
        self.drags = []

    def drag(self, *a, **k):
        self.drags.append(a)


def test_move_up_blocked_when_full():
    """上场已满时把备战席的角色拖到场上空格不会生效(实测):直接告诉模型,不去拖。换位不受影响。"""
    dev = Dev()
    ex = PrepareExecutor(dev, ocr=lambda im: [], raw=lambda a, b: "", sleep=lambda s: None)
    ex.deployed = "6/6"
    board = [Slot("bench", 0, 0.2285, 0.845, name="流萤", score=0.9),
             Slot("front", 0, 0.3885, 0.372, name="开拓者", score=0.9)]
    r = ex.run([Action(type="move", zone="bench", index=1, to_zone="front", to_index=2)], [], board)
    assert "上场已满(6/6)" in r.results[0] and not dev.drags
    r = ex.run([Action(type="move", zone="bench", index=1, to_zone="front", to_index=1)], [], board)
    assert len(dev.drags) == 1 and ex.deployed == "6/6"
    ex.deployed = "5/6"
    r = ex.run([Action(type="move", zone="bench", index=1, to_zone="back", to_index=1)], [], board)
    assert len(dev.drags) == 2 and ex.deployed == "6/6"


def test_full_and_shift():
    assert full("6/6") and not full("5/6") and not full(None)
    assert shift("5/6", 1) == "6/6" and shift("0/6", -1) == "0/6"
