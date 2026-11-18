from cwagent.brain.screen_agent import Action
from cwagent.orchestrator.prepare import PrepareExecutor, full, shift
from cwagent.vision.board import Slot


class Dev:
    def __init__(self):
        self.drags = []

    def drag(self, *a, **k):
        self.drags.append(a)

    def grab(self):
        from PIL import Image

        from cwagent.capture import Frame

        return Frame(image=Image.new("RGB", (8, 8)), timestamp=0)


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


def test_deployed_count_follows_moves():
    """2026-11-18 那局:后台的火花挪回备战席空格后还算 3/3,灵砂补不上去。挪到空格才改人数,换位不改。"""
    dev = Dev()
    ex = PrepareExecutor(dev, ocr=lambda im: [], raw=lambda a, b: "", sleep=lambda s: None)
    board = [Slot("front", 0, 0.47, 0.451, name="流萤", score=0.9),
             Slot("front", 1, 0.53, 0.451, name="知更鸟", score=0.9),
             Slot("back", 1, 0.47, 0.638, name="火花", score=0.9),
             Slot("bench", 0, 0.229, 0.845, name="黑天鹅", score=0.9),
             Slot("bench", 1, 0.293, 0.845, name="灵砂", score=0.9)]
    ex.deployed = "3/3"
    r = ex.run([Action(type="move", zone="back", index=2, to_zone="bench", to_index=5),
                Action(type="move", zone="bench", index=2, to_zone="back", to_index=2)], [], board)
    assert not any("上场已满" in x for x in r.results), r.results
    assert len(dev.drags) == 2 and ex.deployed == "3/3"
    # 场上和备战席的角色换位:人数不变
    ex.deployed = "3/3"
    ex.run([Action(type="move", zone="front", index=1, to_zone="bench", to_index=1)], [], board)
    assert ex.deployed == "3/3"
    # 备战席挪到备战席空格:人数不变
    ex.run([Action(type="move", zone="bench", index=1, to_zone="bench", to_index=6)], [], board)
    assert ex.deployed == "3/3"
