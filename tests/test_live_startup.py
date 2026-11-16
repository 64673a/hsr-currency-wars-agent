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


def test_previous_run_summary_is_not_companion_banner():
    from cwagent.orchestrator.screens import classify, companion_banner
    from cwagent.vision.ocr import TextBox
    boxes = [TextBox(t, .5, .5, .1, .02, .9) for t in ("对局未完成", "本局奇遇", "我的阵容", "下一页")]
    assert companion_banner(boxes) is None
    assert classify(boxes) == "previous_run"
