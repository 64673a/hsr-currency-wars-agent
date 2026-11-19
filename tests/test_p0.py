"""2026-11-16 优化方案 P0(设计文档第 20.10 节)的程序缺陷修复。"""

from pathlib import Path

from cwagent.brain.ledger import RunLedger, node_types
from cwagent.brain.screen_agent import Episode, ScreenAgent
from cwagent.config import ModelEndpoint, ModelProfile, ProviderConfig
from cwagent.memory import MemoryStore

ROOT = Path(__file__).resolve().parents[1]

# 2026-11-16 实机位面详情读到的说明行(runs/20261116-020449_gpt-6.1-sol)
PLANE_1 = [
    "1-1 奖励节点 难度较低的节点，拥有无限的行动值，敌人会随机掉落战利品晶矿。",
    "1-2 奖励节点 难度较低的节点，拥有无限的行动值，敌人会随机掉落战利品晶矿。",
    "1-3 投资策略 可提供随机【投资策略】三选一。选择完毕后将继续备战【战斗节点】。 回",
    "1-4 战斗节点 具有挑战的战斗节点，你将面对不同竞争对手阵营中的普通、精英敌人。 狂化状态",
    "1-5 补给阶段 提供若干组角色和装备的补给包，可根据当前阵容情况自由选择补给。",
    "1-6 战斗节点 具有挑战的战斗节点，你将面对不同竞争对手阵营中的普通、精英敌人。",
    "1-7 遭遇节点 可选难度的挑战节点，完成战斗挑战后可获得额外奖励。",
    "1-8 幕后茶歇 特殊的奖励节点，你将前往【幕后茶歇】，可与奇遇伙伴共度时光。 奇遇伙伴-周天鸽",
    "1-9 首领节点 当前位面的首领挑战，请小心应对。 主角光环 进入本节点时，可为你的角色选择主角光环。",
]


class NoIndex:
    def link(self, texts):
        return []


def agent(memories):
    ep = ModelEndpoint.of(ProviderConfig(name="p", format="openai", base_url="https://x/v1", api_key="k"),
                          ModelProfile(name="m", provider="p", model="m"))
    return ScreenAgent(ep, NoIndex(), memories, "规则")


def test_node_types_from_plane_detail():
    kinds = node_types(PLANE_1)
    assert kinds["1-1"] == {"reward"}
    assert kinds["1-3"] == {"strategy", "battle"}      # 投资策略选完还要打一场
    assert kinds["1-5"] == {"supply"}
    assert kinds["1-7"] == {"encounter"}
    assert kinds["1-8"] == {"tea_break"}               # 说明里的「奖励节点」不算
    assert kinds["1-9"] == {"boss"}
    ledger = RunLedger()
    ledger.node_kinds.update(kinds)
    assert ledger.kinds_near("1-8") == {"tea_break", "boss"}
    assert ledger.kinds_near("1-9") == {"boss"}         # 位面最后一个节点没有下一个
    assert ledger.kinds_near(None) == set()


def test_node_types_skip_leading_marker():
    """2026-11-19 那局位面 3 的详情在 3-7 前面多读出一个「■」,3-7 没被认成首领节点。"""
    kinds = node_types(["■ 3-7 首领节点 当前位面的首领挑战，请小心应对。", "3-6 幕后茶歇 特殊的奖励节点"])
    assert kinds == {"3-7": {"boss"}, "3-6": {"tea_break"}}


def test_public_memories_are_in_the_catalog(tmp_path):
    """经验 2026-11-18 起由模型自己查:公共经验(已采纳的)都在系统提示词的目录里,原文要 lookup 才给。"""
    memories = MemoryStore(ROOT / "knowledge" / "memories.yaml", tmp_path / "m.sqlite").public()
    brain = agent(memories)
    for m in memories:
        if m.status == "approved":
            assert f"[{m.kind}] {m.title}" in brain.system
    assert "{memories}" not in brain.system and "{rules}" not in brain.system


# ---------------------------------------------------------------- 出战人数未满、买经验后的上场上限

from PIL import Image  # noqa: E402

from cwagent.brain.screen_agent import Action  # noqa: E402
from cwagent.capture import Frame  # noqa: E402
from cwagent.orchestrator.prepare import PrepareExecutor, raise_cap  # noqa: E402
from cwagent.vision.ocr import TextBox  # noqa: E402


def box(text, x=0.5, y=0.5):
    return TextBox(text, x, y, 0.05, 0.03, 0.99)


class Game:
    """只认几下操作的假游戏:点出战(人数没满时)弹确认框;按 F 加经验到下一级。"""

    def __init__(self, *, seats_full: bool, level: int = 5):
        self.seats_full = seats_full
        self.level = level
        self.dialog = False
        self.started = False
        self.clicks: list[tuple[float, float]] = []

    def click(self, x, y):
        self.clicks.append((round(x, 3), round(y, 3)))
        if (round(x, 3), round(y, 3)) == (0.946, 0.694):
            self.dialog = not self.seats_full
            self.started = self.seats_full
        elif self.dialog and (round(x, 3), round(y, 3)) == (0.43, 0.572):    # 取消
            self.dialog = False
        elif self.dialog and (round(x, 3), round(y, 3)) == (0.57, 0.572):    # 确认
            self.dialog, self.started = False, True

    def key(self, k):
        if k == "f":
            self.level += 1

    def grab(self):
        return Frame(image=Image.new("RGB", (192, 108)), timestamp=0)

    def ocr(self, image, *a, **k):
        out = [box(f"Lv.{self.level}", 0.15, 0.84), box("商店", 0.845, 0.909)]
        if self.dialog:
            out += [box("可出战角色人数未达上限，是否确认出战？", 0.498, 0.473), box("取消", 0.43, 0.572),
                    box("确认", 0.57, 0.572)]
        return out


def executor(game):
    ex = PrepareExecutor(game, game.ocr, raw=lambda a, b: "", sleep=lambda s: None)
    ex.node = "1-4"
    return ex


def test_understaffed_ready_is_cancelled_then_respected():
    """第一次弹「人数未达上限」就取消、告诉模型;模型看过以后再出战,照它的决定确认。都不勾「本局不再提示」。"""
    game = Game(seats_full=False)
    ex = executor(game)
    ex.deployed = "4/6"
    r = ex.run([Action(type="ready")], [box("商店", 0.845, 0.909)], None)
    assert not r.ready and r.observe and "人数未达上限" in r.results[-1] and "上场 4/6" in r.results[-1]
    assert not game.started and not game.dialog
    r = ex.run([Action(type="ready")], [box("商店", 0.845, 0.909)], None)
    assert r.ready and game.started
    assert all("本局不再提示" not in str(c) for c in game.clicks)


def test_ready_with_full_seats_is_unchanged():
    game = Game(seats_full=True)
    r = executor(game).run([Action(type="ready")], [box("商店", 0.845, 0.909)], None)
    assert r.ready and game.started and r.results == ["出战"]


def test_buy_exp_raises_deploy_cap():
    """买经验升级后上场上限跟着加,不再用升级前的数拦上场。"""
    game = Game(seats_full=True, level=5)
    ex = executor(game)
    ex.deployed = "5/5"
    r = ex.run([Action(type="buy_exp", times=1)], [box("商店", 0.845, 0.909)], None)
    assert ex.deployed == "5/6" and "升到 6 级" in r.results[0]
    assert raise_cap("5/5", 2) == "5/7"


def test_buy_exp_unknown_level_marks_seats_stale():
    game = Game(seats_full=True, level=5)
    ex = executor(game)
    ex.deployed = "5/5"
    game.ocr = lambda image, *a, **k: []          # 等级读不出来
    ex.ocr = game.ocr
    ex.run([Action(type="buy_exp", times=1)], [], None)
    assert ex.deployed is None                      # 拿不准就不拦上场


# ---------------------------------------------------------------- 首领节点程序打开「选择主角」;人数未满的反射

from tests.test_loop import FakeBrain, FakeOcr, Sim, no_sleep  # noqa: E402,F401  (no_sleep 是自动生效的 fixture)
from cwagent.orchestrator.loop import Runner, Trace  # noqa: E402

BOSS_PREP = [("备战阶段", 0.245, 0.043), ("1-9", 0.245, 0.072), ("前台区域", 0.5, 0.29), ("商店", 0.845, 0.909),
             ("选择主角", 0.946, 0.62), ("出战", 0.946, 0.694)]


def runner_for(sim, tmp_path):
    r = Runner(sim, FakeBrain({}), Trace(tmp_path / "run"), ocr=FakeOcr(sim), log=lambda m: None, settle=False)
    r._surveyed, r._companion_done = "1", True          # 收集节点、读伙伴都做过了
    r._open_crystals = lambda image: None                # 没有晶矿
    return r


def test_boss_node_opens_spotlight_once(tmp_path):
    pages = {"prep": BOSS_PREP, "spot": [("选择一个角色获得主角光环", 0.5, 0.1)]}
    sim = Sim(pages, "prep", lambda s, a: "spot" if a == ("click", 0.946, 0.62) else None)
    r = runner_for(sim, tmp_path)
    r._node = "1-9"
    frame = sim.grab()
    assert r.macros(frame.image, FakeOcr(sim)(frame.image), "1-9").startswith("打开主角光环选择")
    assert sim.state == "spot"
    sim.state = "prep"                                    # 模型从光环界面返回了备战
    frame = sim.grab()
    assert r.macros(frame.image, FakeOcr(sim)(frame.image), "1-9") is None   # 同一节点不再自动打开


def test_boss_preparation_waits_for_controls_before_opening_crystals(tmp_path):
    prep = [("2-7", x, y) if text == "1-9" else (text, x, y) for text, x, y in BOSS_PREP]
    pages = {"loading": prep[:-2], "prep": prep, "spot": [("选择一个角色获得主角光环", 0.5, 0.1)]}
    sim = Sim(pages, "loading", lambda _, a: "spot" if a == ("click", 0.946, 0.62) else None)
    r = runner_for(sim, tmp_path)
    r._surveyed, r._node = "2", "2-7"
    r.ledger.node_kinds["2-7"] = {"boss"}
    initial = sim.grab()
    remaining = iter(["loading", "loading", "prep"])
    grab = sim.grab

    def advance():
        sim.state = next(remaining, sim.state)
        return grab()

    def collect(image):
        assert sim.state == "prep" and not sim.actions
        assert image.getpixel((0, 0))[0] == sim.names.index("prep")
        return "划开晶矿(5 个)"

    sim.grab = advance
    r._open_crystals = collect
    assert r.macros(initial.image, r.ocr(initial.image), "2-7") == "划开晶矿(5 个)"
    r._open_crystals = lambda _: None
    assert r.macros(initial.image, r.ocr(initial.image), "2-7").startswith("打开主角光环选择")
    assert sim.state == "spot"


def test_deploy_confirm_reflex_cancels_first(tmp_path):
    pages = {"dlg": [("可出战角色人数未达上限，是否确认出战？", 0.498, 0.473), ("取消", 0.43, 0.572),
                     ("确认", 0.57, 0.572), ("本局不再提示", 0.45, 0.53)]}
    sim = Sim(pages, "dlg", lambda s, a: None)
    r = runner_for(sim, tmp_path)
    r._node = "1-4"
    boxes = FakeOcr(sim)(sim.grab().image)
    first = r.reflex("deploy_confirm", None, boxes, {}, set())
    assert [a.text for a in first] == ["取消"] and "人数未达上限" in r._notice
    second = r.reflex("deploy_confirm", None, boxes, {}, set())
    assert [a.text for a in second] == ["确认"]


# ---------------------------------------------------------------- 界面错判

from cwagent.orchestrator.screens import classify  # noqa: E402

# 2026-11-16 实机投资环境界面(gpt-6.1-sol 那局第 7 步):「轮岗」的说明里有「备战阶段开始时」
ENV_WITH_ROTATION = [("投资环境", 0.5, 0.091), ("攻略", 0.885, 0.092), ("经济过热", 0.229, 0.363), ("轮岗", 0.499, 0.363),
                     ("二手市场", 0.771, 0.363), ("备战阶段开始时，使一个随机费用的刷新概", 0.5, 0.4),
                     ("率翻倍，每个备战阶段重新随机。", 0.476, 0.425), ("剩余次数：1", 0.402, 0.911), ("确认", 0.564, 0.91)]
# 2026-11-14 实机奖励节点的战斗结算(deepseek 那局 1-1),当时被认成了备战
REWARD_BATTLE_END = [("挑战成功", 0.498, 0.212), ("1-1奖励", 0.501, 0.263), ("挑战进度", 0.5, 0.372),
                     ("点击空白加速", 0.5, 0.832)]


def test_environment_choice_not_taken_for_prepare():
    assert classify([box(t, x, y) for t, x, y in ENV_WITH_ROTATION]) == "environment_choice"
    assert classify([box(t, x, y) for t, x, y in REWARD_BATTLE_END]) == "battle_end"
    assert classify([box(t, x, y) for t, x, y in BOSS_PREP]) == "prepare"


# ---------------------------------------------------------------- 逐节点报表

import json  # noqa: E402

from cwagent.replay import report  # noqa: E402


def test_node_report(tmp_path):
    board = [{"zone": "front", "index": 1, "name": "银狼LV.999", "stars": 1},
             {"zone": "back", "index": 0, "name": "三月七", "stars": 2},
             {"zone": "bench", "index": 0, "name": "景元", "stars": 1}]
    steps = [
        {"step": 1, "screen": "prepare", "node": "1-3", "state": {"hud": {"hp": 84, "gold": 18, "level": 4,
                                                                         "deployed": "2/4"}, "board": board},
         "decision": {"usage": {"input_tokens": 1000}}, "actions": ["出战"]},
        {"step": 2, "screen": "battle", "node": "1-3", "battle_summary": "进度 0% → 33%;阵亡 1 次(第 9 秒);用时 90 秒"},
        {"step": 3, "screen": "node_result", "node": None, "texts": ["(0.499,0.212) 挑战结束", "(0.384,0.465) 小队生命值75"]},
        {"step": 4, "screen": "strategy_choice", "node": None, "decision": {"usage": {"input_tokens": 500}},
         "actions": ["点击「爆晶矿」", "点击「确认」"]},
        {"step": 5, "screen": "prepare", "node": "1-4", "state": {"hud": {"hp": 75, "gold": 25, "level": 4}}},
    ]
    (tmp_path / "steps.jsonl").write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in steps), encoding="utf-8")
    (tmp_path / "meta.json").write_text(json.dumps({"profile": "m", "difficulty": "A8-1", "result": "stopped"}),
                                        encoding="utf-8")
    rows = report.node_rows(report._steps(tmp_path))
    r13 = rows[0]
    assert (r13.node, r13.hp_in, r13.gold_out, r13.front, r13.stars2, r13.on_field) == ("1-3", 84, 18, 1, 1, 2)
    assert (r13.progress, r13.deaths, r13.result, r13.hp_after, r13.tokens) == ("0%→33%", 1, "负", 75, 1500)
    assert rows[1].choices == ["投资策略:点击「爆晶矿」、点击「确认」"]
    text = report.render(tmp_path)
    assert "| 1-3 | 84 | 18 | 4 | 2/4 | 1 | 1/2 | 18 | 0%→33% | 1 | 负 | 75 | 2 | 1500 |" in text


def test_node_report_reads_result_screens(tmp_path):
    """2026-11-19 那局:最终首领的结算画面(「前往结算」,分类成 other)没记胜负;打赢了进度只读到 94%;
    奖励节点十几秒打完、没看到战斗。"""
    hud = {"hud": {"hp": 100, "gold": 30, "level": 8}}
    steps = [
        {"step": 1, "screen": "prepare", "node": "2-6", "state": hud,
         "plane_nodes": "位面 2(从左到右):\n- 2-6 奖励节点 难度较低的节点\n- 2-7 首领节点 当前位面的首领挑战"},
        {"step": 2, "screen": "node_result", "node": "2-6", "texts": ["(0.5,0.2) 挑战成功", "(0.4,0.4) 小队生命值100"]},
        {"step": 3, "screen": "prepare", "node": "2-7", "state": hud},
        {"step": 4, "screen": "battle", "node": "2-7", "battle_summary": "进度 0% → 最后读到 94%;没认出阵亡横幅;用时 268 秒"},
        {"step": 5, "screen": "node_result", "node": "2-7", "texts": ["(0.5,0.2) 挑战成功", "(0.4,0.4) 小队生命值100"]},
        {"step": 6, "screen": "prepare", "node": "3-7", "state": hud},
        {"step": 7, "screen": "battle", "node": "3-7", "battle_summary": "进度 0% → 最后读到 61%;用时 461 秒"},
        {"step": 8, "screen": "other", "node": None,
         "texts": ["(0.5,0.2) 挑战成功", "(0.5,0.3) 3-7", "(0.4,0.6) 挑战进度", "(0.6,0.6) 100", "(0.5,0.8) 前往结算"]},
        {"step": 9, "screen": "settlement", "node": None,
         "texts": ["(0.5,0.2) 挑战成功", "(0.5,0.4) 小队生命值❤100", "(0.5,0.8) 下一步"]},
    ]
    (tmp_path / "steps.jsonl").write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in steps), encoding="utf-8")
    (tmp_path / "meta.json").write_text(json.dumps({"profile": "m", "result": "settlement"}), encoding="utf-8")
    rows = {r.node: r for r in report.node_rows(report._steps(tmp_path))}
    assert (rows["2-6"].progress, rows["2-6"].result) == ("0%→100%", "胜")     # 奖励节点不会输
    assert (rows["2-7"].progress, rows["2-7"].result) == ("0%→100%", "胜")
    assert (rows["3-7"].progress, rows["3-7"].result, rows["3-7"].hp_after) == ("0%→100%", "胜", 100)
    text = report.render(tmp_path)
    assert "结果 通关(settlement)" in text and "战斗 3 场,胜 3 场" in text


# ---------------------------------------------------------------- 攻略卡片(作者原文不截断,前中期带前后台和羁绊人数)

from cwagent.knowledge.guides import Guide, card  # noqa: E402


def test_guide_card_shows_whole_plan():
    idea = "1-1拿dot奇美拉，点两下人口。\n1-3需要搜牌开3dot。" + "过渡" * 80 + "末尾的打法"
    g = Guide(id="x", title="【7持续伤害】测试", carries=["海瑟音"],
              final={"front": [{"name": "海瑟音", "star": 2, "equipment": ["蓄能帆"], "equipment2": ["绝对热量"],
                                "spotlights": ["刀具收藏家"]}], "back": []},
              early={"海瑟音", "黑天鹅"}, middle=set(), traits=["持续伤害"], environments=[], tempo=["速升9级"],
              equip_order={"compose": ["蓄能帆", "永动机"]}, idea=idea, augments=["定期福利", "成长基金"], use_count=1,
              hard=True, boards={"early": {"front": [{"name": "海瑟音", "star": 1}], "back": [{"name": "黑天鹅"}]}},
              stage_traits={"early": [{"name": "持续伤害", "count": 3}], "final": [{"name": "持续伤害", "count": 7}]},
              augment_groups={"first": ["定期福利"], "second": ["成长基金"]})
    text = card(g)
    assert "末尾的打法" in text                                  # 原来只给前 160 字
    assert "前期阵容:前台 海瑟音;后台 黑天鹅;羁绊 3持续伤害" in text
    assert "海瑟音 2星[蓄能帆;次选 绝对热量;光环 刀具收藏家]" in text and "羁绊 7持续伤害" in text
    assert "第一组 定期福利;第二组 成长基金" in text and "合成顺序 蓄能帆 → 永动机" in text
    short = card(g, full=False)
    assert "作者思路" not in short and "核心羁绊:7持续伤害" in short


# ---------------------------------------------------------------- 局面要点(P1)

from cwagent.orchestrator import situation  # noqa: E402
from cwagent.vision.board import Slot  # noqa: E402
from cwagent.vision.hud import Hud  # noqa: E402


def gpt_1_7_board():
    """2026-11-16 gpt-6.1-sol 那局 1-7 的阵容:前台只有银狼,备战席两张瓦尔特。"""
    def s(zone, i, name, stars=1):
        return Slot(zone, i, 0.5, 0.5, name=name, score=0.9, stars=stars)
    return ([Slot("front", 0, 0.4, 0.37), s("front", 1, "银狼LV.999"), Slot("front", 2, 0.5, 0.37),
             Slot("front", 3, 0.6, 0.37)]
            + [s("back", 0, "三月七"), s("back", 1, "白露"), s("back", 2, "景元"), s("back", 3, "开拓者")]
            + [s("bench", 0, "那刻夏"), s("bench", 1, "瓦尔特"), s("bench", 4, "瓦尔特"), s("bench", 5, "景元")])


def test_situation_facts():
    facts = situation.RoleFacts(traits_of={"景元": ["云上五骁", "召唤"], "白露": ["云上五骁", "治疗"],
                                           "三月七": ["开拓银轨"], "开拓者": ["开拓银轨", "星核猎手"],
                                           "银狼LV.999": ["星核猎手"]},
                                layers={"云上五骁": [2, 3, 4, 5], "开拓银轨": [2, 4, 6], "星核猎手": [2, 3, 5],
                                        "召唤": [2, 4], "治疗": [2, 4]})
    hud = Hud(node="1-7", hp=56, gold=56, level=5, exp="4/20", deployed="5/5")
    shop = [(1, "瓦尔特", 1), (2, "万敌", 2), (3, None, None), (4, "景元", 3), (5, "白露", 2)]
    text = situation.render(board=gpt_1_7_board(), hud=hud, shop=shop, two_star={4}, facts=facts)
    assert "下个节点利息 5" in text and "超过 50 的 6 金币不产生利息" in text
    assert "升下一级还差 16 经验(买 4 次,16 金币)" in text
    assert "前台 1/4、后台 4;上场 5/5" in text
    assert "瓦尔特 1星×2(再 1 张 1 星升 2 星)" in text and "景元 1星×2(再 1 张 1 星升 2 星)" in text
    assert "商店 1 号 瓦尔特:买了就升 2 星" in text
    assert "商店 4 号 景元" not in text                    # 那是二星卡,场上没有两张 2 星景元
    assert "云上五骁 2 人(档位 2*,3,4,5),下一档差 1 人" in text
    assert "星核猎手 2 人(档位 2*,3,5)" in text
    # 千星商会按 3 星人数、少女乐队要人数恰好等于某档(官方说明),不按人头算
    special = situation.RoleFacts(traits_of={"真珠": ["千星商会"], "爻光": ["千星商会"], "景元": ["少女乐队"],
                                             "白露": ["少女乐队"]},
                                  layers={"千星商会": [1, 2, 3], "少女乐队": [1, 4]},
                                  star_req={"千星商会": 3}, exact={"少女乐队"})
    lines = situation.traits(gpt_1_7_board()[:4] + [Slot("back", 0, 0.5, 0.5, name="真珠", score=0.9, stars=1),
                                                    Slot("back", 1, 0.5, 0.5, name="爻光", score=0.9, stars=3),
                                                    Slot("back", 2, 0.5, 0.5, name="景元", score=0.9, stars=1),
                                                    Slot("back", 3, 0.5, 0.5, name="白露", score=0.9, stars=1)], special)
    assert "千星商会 3 星 1 人(场上 2 人)(档位 1*,2,3),下一档差 1 人" in lines
    assert "少女乐队 2 人(档位 1,4),人数恰好等于某档才激活" in lines
    assert situation.at_ready(gpt_1_7_board(), hud) == "出战时前台 1/4,场上 5 人其中 2 星及以上 0 个,剩 56 金币(其中 6 不产生利息)"


def test_aha_banner_is_waited_out(tmp_path):
    """「阿哈帮大忙了」一两秒后自己消失(维护者 2026-11-16),程序直接等,不叫模型。"""
    pages = {"aha": [("阿哈帮大忙了", 0.5, 0.4), ("「贪饕」侵蚀 污染等级3", 0.5, 0.5)]}
    sim = Sim(pages, "aha", lambda s, a: None)
    r = runner_for(sim, tmp_path)
    acts = r.reflex("other", None, FakeOcr(sim)(sim.grab().image), {}, set())
    assert [a.type for a in acts] == ["wait"]


def test_node_report_lists_more_choices(tmp_path):
    """骇入、巡海游侠任命、道具里拿的东西、没见过的界面上点的文字也记进「选择」;只是翻页的点击不记(2026-11-19 报告 R6)。"""
    steps = [
        {"step": 1, "screen": "hack", "node": "1-1", "decision": {"turn": 1}, "actions": ["点击「数据拷贝仪LV.1」", "点击「确认选择」"]},
        {"step": 2, "screen": "prepare", "node": "1-1", "decision": {"turn": 1},
         "actions": ["从备战席6号的道具里拿「量产型装甲」:拿到 量产型装甲(来自简易武装箱)", "出战"]},
        {"step": 3, "screen": "ranger", "node": "1-1", "decision": {"turn": 1}, "actions": ["点击「银狼LV.999」", "点击「确认选择」"]},
        {"step": 4, "screen": "other", "node": "1-1", "decision": {"turn": 1}, "actions": ["点击「精雕细琢慢慢画。」"]},
        {"step": 5, "screen": "other", "node": "1-1", "decision": {"turn": 1}, "actions": ["点击 (0.600,0.850)"]},
        {"step": 6, "screen": "other", "node": None, "decision": {"turn": 1}, "actions": ["点击「前往结算」"]},
    ]
    (tmp_path / "steps.jsonl").write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in steps), encoding="utf-8")
    [row] = report.node_rows(report._steps(tmp_path))
    assert row.choices == ["骇入效果:点击「数据拷贝仪LV.1」、点击「确认选择」", "道具:拿到 量产型装甲(来自简易武装箱)",
                           "巡海游侠任命:点击「银狼LV.999」、点击「确认选择」", "其他界面:点击「精雕细琢慢慢画。」"]


def test_node_report_external_agent_has_no_token_count(tmp_path):
    """外部 agent 的 token 用量程序看不到,不写成 0(2026-11-19 报告 R1)。"""
    steps = [{"step": 1, "screen": "prepare", "node": "1-1", "state": {"hud": {"hp": 80}}, "decision": {"turn": 1}}]
    (tmp_path / "steps.jsonl").write_text(json.dumps(steps[0], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "meta.json").write_text(json.dumps({"profile": "外部 agent"}, ensure_ascii=False), encoding="utf-8")
    text = report.render(tmp_path)
    assert "token 用量程序看不到" in text and "输入 0 token" not in text and text.rstrip().endswith("| 1 | - |")
