"""战斗结果写回本局经历:结算页的胜负和小队生命(2026-11-21 deepseek 三局复盘,模型看不到输赢和血量变化)。"""

from cwagent.orchestrator.loop import Runner, Trace, battle_line
from cwagent.orchestrator.screens import node_outcome
from cwagent.vision.hud import Hud
from cwagent.vision.ocr import TextBox


def box(text, x, y):
    return TextBox(text, x, y, 0.05, 0.02, 0.9)


# 20261121-071907 那局的结算页文字(节选)
LOSS_END = [box("挑战结束", 0.499, 0.213), box("1-4战斗", 0.452, 0.264), box("挑战进度", 0.500, 0.371),
            box("小队生命值结算说明", 0.764, 0.480), box("未完成进度伤害", 0.693, 0.558), box("71", 0.499, 0.703),
            box("点击空白加速", 0.500, 0.832)]
LOSS_RESULT = [box("挑战结束", 0.499, 0.212), box("1-4战斗", 0.452, 0.264), box("小队生命值73", 0.384, 0.465),
               box("获得金币总览", 0.314, 0.525), box("继续挑战", 0.5, 0.83)]
WIN_RESULT = [box("挑战成功", 0.499, 0.213), box("2-1战斗", 0.452, 0.264), box("小队生命值3", 0.384, 0.465),
              box("继续挑战", 0.5, 0.83)]
LAST_LOSS = [box("挑战结束", 0.500, 0.213), box("2-5遭遇", 0.500, 0.264), box("挑战进度", 0.500, 0.358),
             box("-14①", 0.512, 0.682), box("前往结算", 0.500, 0.828)]


def test_node_outcome_reads_result_and_hp():
    assert node_outcome(LOSS_END) == ("负", None)           # battle_end 中间的大数字还在跳,不认
    assert node_outcome(LOSS_RESULT) == ("负", 73)
    assert node_outcome(WIN_RESULT) == ("胜", 3)
    assert node_outcome(LAST_LOSS) == ("负", None)
    # 「小队生命值」和数字分成两个框
    split = [box("挑战成功", 0.499, 0.213), box("小队生命值", 0.370, 0.465), box("82", 0.425, 0.466),
             box("3", 0.554, 0.574)]
    assert node_outcome(split) == ("胜", 82)
    # 整局结算的「挑战失败」在画面中间,不是节点结算的标题
    assert node_outcome([box("挑战失败", 0.499, 0.450), box("小队生命值", 0.486, 0.517), box("♥0", 0.533, 0.517)]) \
        == (None, None)


def test_battle_line_puts_result_and_hp_first():
    summary = "进度 0% → 最后读到 87%;行动值 125 → 最后读到 48;没认出阵亡横幅;用时 163 秒"
    assert battle_line("2-1", summary, "胜", 1, 3) == (
        "[2-1 战斗] 胜,小队生命 1 → 3(+2);进度 0% → 100%;行动值 125 → 最后读到 48;没认出阵亡横幅;用时 163 秒")
    lost = battle_line("1-6", "进度 0% → 最后读到 7%;用时 189 秒", "负", 60, 48)
    assert lost == "[1-6 战斗] 负,小队生命 60 → 48(-12);进度 0% → 最后读到 7%;用时 189 秒"
    assert battle_line("3-2", "进度 0% → 最后读到 0%", "负", None, None).startswith("[3-2 战斗] 负;")
    assert battle_line("1-1", "进度 0% → 最后读到 100%", None, 80, None).startswith("[1-1 战斗] 胜负没读到;")
    assert battle_line("2-5", "用时 9 秒", "负", None, 1) == "[2-5 战斗] 负,战后小队生命 1;用时 9 秒"


class Brain:
    def decide(self, *a, **k):
        raise AssertionError("不该问模型")


def runner(tmp_path):
    return Runner(object(), Brain(), Trace(tmp_path / "run"), ocr=object(), log=lambda _: None)


def test_battle_line_waits_for_the_node_result(tmp_path):
    r = runner(tmp_path)
    r._last_hud = Hud(node="1-4", hp=86, gold=25)
    r._battle_pending = ("1-4", "进度 0% → 最后读到 0%;用时 99 秒", 86)
    record = {"step": 40}
    r.reflex("battle_end", None, LOSS_END, record, set())
    assert r.ledger.history == []                            # 结算页还没出来,先不写
    record = {"step": 41}
    actions = r.reflex("node_result", None, LOSS_RESULT, record, set())
    assert actions[0].text == "继续挑战"
    assert r.ledger.history == ["[1-4 战斗] 负,小队生命 86 → 73(-13);进度 0% → 最后读到 0%;用时 99 秒"]
    assert record["outcome"] == {"result": "负", "hp": 73}
    # 之后的选择界面附「最近一次备战」时补上这场打完的血量
    assert r._after_text() == "之后打完 1-4:负,小队生命 73"


def test_pending_battle_is_written_before_the_next_battle(tmp_path):
    """结算页没认出来:下一场战斗开始前(或问模型前)照样写进经历,只是胜负没读到。"""
    r = runner(tmp_path)
    r._battle_pending = ("1-2", "进度 0% → 最后读到 100%", 82)
    r._flush_battle()
    assert r.ledger.history == ["[1-2 战斗] 胜负没读到;进度 0% → 最后读到 100%"]
    assert r._battle_pending is None
    r._flush_battle()
    assert len(r.ledger.history) == 1
