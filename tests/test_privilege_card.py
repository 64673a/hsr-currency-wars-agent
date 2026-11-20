from types import SimpleNamespace

import pytest

from cwagent.brain.screen_agent import Action, Episode
from cwagent.orchestrator import loop
from cwagent.orchestrator.screens import classify
from cwagent.vision.board import Slot
from cwagent.vision.equipment import Item as Equipment
from cwagent.vision.items import Item as Consumable

from .test_loop import FakeBrain, FakeOcr, Sim

PREPARE = [("备战阶段", 0.159, 0.065), ("3-5", 0.159, 0.100), ("出战", 0.947, 0.694), ("商店", 0.845, 0.909)]
PANEL = PREPARE + [("特权赋予卡", 0.535, 0.056), ("请选择1个", 0.535, 0.091),
                   ("战场进化手册·特权", 0.536, 0.310), ("查看详情", 0.540, 0.363),
                   ("请选择强化", 0.768, 0.428), ("确认选择", 0.768, 0.479)]
DRAG = Action(type="drag", x=0.881, y=0.152, x2=0.389, y2=0.372)
PICK = Action(type="click_text", text="战场进化手册·特权")
CONFIRM = Action(type="click_text", text="确认选择")
READY = Action(type="ready")


def test_privilege_choice_takes_priority_over_board_but_not_on_tooltip():
    pages = {"choice": PANEL, "tooltip": PREPARE + [("特权赋予卡", 0.806, 0.148), ("确认选择", 0.768, 0.479)]}
    sim = Sim(pages, "choice", lambda *_: None)
    ocr = FakeOcr(sim)
    assert classify(ocr(sim.grab().image)) == "privilege_choice"
    sim.state = "tooltip"
    assert classify(ocr(sim.grab().image)) == "prepare"


@pytest.mark.parametrize("card_recognized", [False, True])
def test_privilege_card_hands_off_to_model_then_reports_returned_equipment(tmp_path, monkeypatch, card_recognized):
    def rules(state, action):
        if action == ("drag", 0.881, 0.152, 0.389, 0.372):
            return "panel"
        if state == "panel" and action == ("click", 0.536, 0.210):
            return "selected"
        if state == "selected" and action == ("click", 0.768, 0.479):
            return "returned"
        return None

    def read(image):
        common = Equipment(1, 0.961, 0.23, "wind", "火力风暴潮")
        returned = Equipment(2, 0.961, 0.30, "book", "战场进化手册", privileged=True)
        return [common, returned] if image.getpixel((0, 0))[0] == sim.names.index("returned") else [common]

    monkeypatch.setattr(loop.time, "sleep", lambda _: None)
    sim = Sim({"board": PREPARE, "panel": PANEL, "selected": PANEL, "returned": PREPARE}, "board", rules)
    brain = FakeBrain({"prepare": [[DRAG, READY], [PICK, CONFIRM, READY], [Action(type="wait")]]})
    r = loop.Runner(sim, brain, loop.Trace(tmp_path / "run"), ocr=FakeOcr(sim), log=lambda _: None, settle=False)
    r.episode = Episode(id=1, key=("prepare", "3-5"), screen="prepare", node="3-5", role="tactical", max_turns=2)
    r._node = "3-5"
    r._board = [Slot("front", 0, 0.389, 0.372, "那刻夏")]
    r.ledger.equipped = {"那刻夏": ["战场进化手册", "火力风暴潮"], "昔涟": ["永动机"]}
    r.equipment_reader = SimpleNamespace(read=read)
    monkeypatch.setattr(r, "perceive", lambda *_: ("", {}, []))
    monkeypatch.setattr(loop, "read_items", lambda *_: [])
    if card_recognized:
        r._items = [Consumable(3, 350104, "特权赋予卡", 1, 0.881, 0.152, 0.86)]

    assert r.step(1) == "prepare"
    assert "尚未强化" in r._results[0] and len(sim.actions) == 1
    assert "战场进化手册" in r.ledger.equipped["那刻夏"]
    assert r.step(2) == "privilege_choice" and sim.state == "returned"
    assert sim.actions == [("drag", 0.881, 0.152, 0.389, 0.372), ("click", 0.536, 0.210), ("click", 0.768, 0.479)]
    assert "2号" in r._results[-1] and "尚未穿戴" in r._results[-1]
    assert r.ledger.equipped == {"那刻夏": ["火力风暴潮"], "昔涟": ["永动机"]}
    assert any(m.label == "战场进化手册·特权" for m in r._vision_marks.values())
    assert all(m.label != "出战" for m in r._vision_marks.values())
    assert r.step(3) == "prepare"
    assert brain.calls == [("prepare", "observation", 1), ("privilege_choice", "observation", 1),
                           ("prepare", "observation", 1)]
    assert "已回到装备栏" in brain.results[-1]


def test_privilege_card_does_not_execute_background_actions_or_claim_unverified_return(tmp_path, monkeypatch):
    monkeypatch.setattr(loop.time, "sleep", lambda _: None)
    sim = Sim({"panel": PANEL, "returned": PREPARE}, "panel",
              lambda _, a: "returned" if a == ("click", 0.768, 0.479) else None)
    r = loop.Runner(sim, FakeBrain({}), loop.Trace(tmp_path / "run"), ocr=FakeOcr(sim), log=lambda _: None, settle=False)
    r._privilege_role = "那刻夏"
    r.ledger.equipped = {"那刻夏": ["战场进化手册"]}
    r.equipment_reader = SimpleNamespace(read=lambda _: [Equipment(1, 0.961, 0.23, "book", "战场进化手册", privileged=True)])
    assert "没找到" in r._privilege_actions([Action(type="click_text", text="出战")])[0]
    assert "选择框外" in r._privilege_actions([Action(type="click", x=0.947, y=0.694)])[0]
    assert sim.actions == []
    assert "未能核实" in r._privilege_actions([CONFIRM, READY])[-1]
    assert sim.actions == [("click", 0.768, 0.479)]
    assert r.ledger.equipped == {"那刻夏": ["战场进化手册"]}
