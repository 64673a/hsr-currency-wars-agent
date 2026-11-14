from pathlib import Path

import pytest
from PIL import Image

from cwagent.brain.screen_agent import Action, Decision
from cwagent.capture import Frame
from cwagent.orchestrator.loop import Runner, RunOptions, Trace
from cwagent.vision.board import SHOP_X
from cwagent.vision.ocr import TextBox


class Sim:
    """假游戏:按状态返回截图(颜色编码状态),操作按规则切换状态;FakeOcr 按截图颜色返回这一状态的文字。"""

    def __init__(self, pages: dict[str, list], start: str, rules):
        self.pages, self.state, self.rules = pages, start, rules
        self.names = list(pages)
        self.actions: list[tuple] = []

    def grab(self):
        i = self.names.index(self.state)
        return Frame(image=Image.new("RGB", (192, 108), (i, i, i)), timestamp=0.0)

    def _do(self, action):
        self.actions.append(action)
        self.state = self.rules(self.state, action) or self.state

    def click(self, x, y, *, button="left"):
        self._do(("click", round(x, 3), round(y, 3)))

    def drag(self, x1, y1, x2, y2, *, duration=0.4):
        self._do(("drag", round(x1, 3), round(y1, 3), round(x2, 3), round(y2, 3)))

    def key(self, key):
        self._do(("key", key))

    def scroll(self, x, y, notches):
        self._do(("scroll", notches))

    def move(self, x, y):
        pass


class FakeOcr:
    def __init__(self, sim: Sim):
        self.sim = sim

    def __call__(self, image, region=None):
        page = self.sim.pages[self.sim.names[image.convert("RGB").getpixel((0, 0))[0]]]
        return [TextBox(text=t, x=x, y=y, w=0.05, h=0.02, score=0.9) for t, x, y in page]


class FakeBrain:
    """按界面给出预设的操作,并像 ScreenAgent 一样维护回合状态。"""

    def __init__(self, plans: dict[str, list[list[Action]]]):
        self.plans = plans
        self.calls: list[tuple[str, str, int]] = []   # (界面, opening / observation, 回合 id)

    def opening(self, ep, boxes, *, ledger, hints="", names=None):
        self.calls.append((ep.screen, "opening", ep.id))
        self.ledger = ledger
        return "open", []

    def observation(self, ep, results, boxes, *, screen, hints="", names=None):
        self.calls.append((screen, "observation", ep.id))
        self.results = results
        return "obs"

    def decide(self, ep, text, recalled=None):
        ep.turns += 1
        ep.pending = ["call"]
        actions = self.plans[ep.screen].pop(0)
        ep.waiting = all(a.type == "wait" for a in actions)
        return Decision("想好了", actions, "主 C 走那刻夏" if ep.screen == "environment_choice" else None, [],
                        prompt=text, response=None, episode=ep.id, turn=ep.turns, role=ep.role)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    import cwagent.orchestrator.loop as loop

    monkeypatch.setattr(loop, "ACTION_GAP", 0)
    monkeypatch.setattr(loop.time, "sleep", lambda s: None)


def test_runner_reflexes_and_episode(tmp_path):
    pages = {
        "intro": [("本场对局首领", 0.3, 0.18), ("本局奇遇", 0.63, 0.58), ("菇菇兔", 0.65, 0.635), ("下一步", 0.73, 0.91)],
        "tap": [("点击空白处继续", 0.5, 0.89)],
        "env": [("投资环境", 0.5, 0.09), ("追击概念股", 0.77, 0.36), ("确认", 0.56, 0.91)],
        "end": [("对局评价", 0.5, 0.3)],
    }

    def rules(state, a):
        return {("intro", ("click", 0.73, 0.91)): "tap", ("tap", ("click", 0.5, 0.62)): "env",
                ("env", ("click", 0.56, 0.91)): "end"}.get((state, a))

    sim = Sim(pages, "intro", rules)
    brain = FakeBrain({"environment_choice": [[Action(type="click_text", text="追击概念股"),
                                               Action(type="click_text", text="确认")]]})
    met = []
    runner = Runner(sim, brain, Trace(tmp_path / "run"), ocr=FakeOcr(sim), on_companion=met.append,
                    log=lambda m: None, settle=False)
    assert runner.run(RunOptions(max_steps=10)) == "settlement"
    assert met == ["菇菇兔"] and brain.calls == [("environment_choice", "opening", 1)]
    assert sim.actions == [("click", 0.73, 0.91), ("click", 0.5, 0.62), ("click", 0.77, 0.36), ("click", 0.56, 0.91)]
    led = runner.ledger
    assert led.companion == "菇菇兔" and "本场对局首领" in led.intro
    assert led.direction == "主 C 走那刻夏"
    assert led.events == ["投资环境:点击「追击概念股」;点击「确认」"]   # 回合在结算界面结束,留一行记录
    assert "菇菇兔" in brain.ledger                                    # 开场时模型看到了本局档案
    lines = (tmp_path / "run" / "steps.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4 and (tmp_path / "run" / "shots" / "0001.jpg").exists()


def test_blank_and_popup_screens_skip_model(tmp_path):
    """过场画面直接等;模型操作后弹出的界面接着原来的回合(只交执行结果和新界面)。"""
    pages = {
        "loading": [("UID:103175008", 0.05, 0.97)],
        "strategy": [("请选择投资策略", 0.5, 0.1), ("团队力量•金", 0.5, 0.4), ("确认", 0.5, 0.9)],
        "popup": [("提示", 0.5, 0.3), ("确定选择这个投资策略吗", 0.5, 0.45), ("确定", 0.6, 0.6)],
        "result": [("挑战成功", 0.5, 0.2), ("获得金币总览", 0.5, 0.4), ("继续挑战", 0.8, 0.9)],
    }
    step = {"n": 0}

    def rules(state, a):
        return {("strategy", ("click", 0.5, 0.9)): "popup", ("popup", ("click", 0.6, 0.6)): "result"}.get((state, a))

    sim = Sim(pages, "loading", rules)
    real_grab = sim.grab

    def grab():
        step["n"] += 1
        if sim.state == "loading" and step["n"] > 1:
            sim.state = "strategy"
        return real_grab()

    sim.grab = grab
    brain = FakeBrain({"strategy_choice": [[Action(type="click_text", text="团队力量•金"),
                                            Action(type="click_text", text="确认")],
                                           [Action(type="click_text", text="确定")]]})
    runner = Runner(sim, brain, Trace(tmp_path / "run"), ocr=FakeOcr(sim), log=lambda m: None, settle=False)
    runner.run(RunOptions(max_steps=5))
    assert brain.calls == [("strategy_choice", "opening", 1), ("other", "observation", 1)]
    assert brain.results == ["点击「团队力量•金」", "点击「确认」"]
    assert sim.actions[-1] == ("click", 0.8, 0.9)                     # 结算界面:规则点「继续挑战」
    assert runner.episode is None
    assert runner.ledger.events == ["投资策略:点击「团队力量•金」;点击「确认」;点击「确定」"]


def test_prepare_semantic_actions(tmp_path):
    shop = [("备战阶段", 0.12, 0.04), ("1-3", 0.12, 0.07), ("收起", 0.845, 0.909),
            ("银狼", 0.22, 0.28), ("3", 0.30, 0.28), ("黄泉", 0.36, 0.28), ("5", 0.44, 0.28)]
    pages = {
        "closed": [("备战阶段", 0.12, 0.04), ("1-3", 0.12, 0.07), ("商店", 0.845, 0.909)],
        "shop": shop,
        "bought": [b for b in shop if b[0] not in ("黄泉", "5")],
        "closed2": [("备战阶段", 0.12, 0.04), ("1-3", 0.12, 0.07), ("商店", 0.845, 0.909)],
        "battle": [("敌方行动中", 0.5, 0.1)],
    }

    def rules(state, a):
        if a == ("click", 0.845, 0.909) and state == "closed":
            return "shop"
        if a == ("click", SHOP_X[1], 0.15) and state == "shop":
            return "bought"
        if a == ("click", 0.845, 0.909) and state in ("shop", "bought"):    # 「收起」和「商店」在同一个位置
            return "closed2"
        if a == ("click", 0.946, 0.694) and state == "closed2":
            return "battle"
        return None

    sim = Sim(pages, "closed", rules)
    brain = FakeBrain({"prepare": [[Action(type="buy", slot=2), Action(type="move", zone="bench", index=1,
                                                                      to_zone="front", to_index=1),
                                    Action(type="ready")]]})
    runner = Runner(sim, brain, Trace(tmp_path / "run"), ocr=FakeOcr(sim), log=lambda m: None, settle=False)
    runner._surveyed, runner._crystal_ok = "1", False     # 跳过收集节点、开晶矿
    runner.run(RunOptions(max_steps=3))
    assert brain.calls == [("prepare", "opening", 1)]
    kinds = [a[0] for a in sim.actions]
    assert kinds == ["click", "click", "drag", "click", "click"]      # 开商店、买、拖上场、收起商店、出战
    assert sim.actions[1] == ("click", SHOP_X[1], 0.15)
    assert runner.episode is None
    assert runner.ledger.nodes["1-3"].actions == "买商店第 2 格(黄泉);备战席1号 → 前台1号;出战"


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


def test_tick_diff_signed():
    # device.windows 在非 Windows 上导入 ctypes.windll 会失败,只取纯函数测试
    src = (Path(__file__).resolve().parents[1] / "src/cwagent/device/windows.py").read_text(encoding="utf-8")
    start = src.index("def tick_diff")
    ns: dict = {}
    exec(src[start:src.index("\n\n\n", start)], ns)
    tick_diff = ns["tick_diff"]
    assert tick_diff(1000, 1016) == -16                 # 系统记录的输入略早于 agent 记录的时间:不算有人操作
    assert tick_diff(5000, 1000) == 4000
    assert tick_diff(10, 0xFFFFFFF0) == 26               # 回绕


def test_user_interrupt_stops_run(tmp_path):
    from cwagent.device import UserInterrupt

    sim = Sim({"tap": [("点击空白处继续", 0.5, 0.89)]}, "tap", lambda s, a: None)

    def boom(x, y, *, button="left"):
        raise UserInterrupt("检测到有人在动鼠标")

    sim.click = boom
    runner = Runner(sim, FakeBrain({}), Trace(tmp_path / "run"), ocr=FakeOcr(sim), log=lambda m: None, settle=False)
    assert runner.run(RunOptions(max_steps=5)) == "interrupted"
