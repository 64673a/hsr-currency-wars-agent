from cwagent.orchestrator.loop import Runner, Trace
from tests.test_loop import FakeBrain, FakeOcr, Sim


def test_old_settlement_without_ocr_button_can_be_left(tmp_path):
    sim = Sim({"end": [("挑战失败", .5, .218), ("对局评价", .5, .347)]}, "end", lambda *a: None)
    runner = Runner(sim, FakeBrain({}), Trace(tmp_path), ocr=FakeOcr(sim), settle=False)
    boxes = FakeOcr(sim)(sim.grab().image)
    actions = runner.reflex("settlement", sim.grab().image, boxes, {}, set())
    assert [(a.type, a.x, a.y) for a in actions] == [("click", .5, .832)]
    runner.in_run = True
    assert runner.reflex("settlement", sim.grab().image, boxes, {}, set()) == []
    runner.trace.close()
