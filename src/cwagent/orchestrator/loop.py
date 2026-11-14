"""主循环:截图 → 识别界面 → 规则或模型决定 → 执行 → 记录。设计见 docs/TECH_DESIGN.md 第 10 节。"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from ..device import UserInterrupt
from ..brain.screen_agent import Action, Decision, ScreenAgent, action_text, to_jsonable
from ..vision.battle import has_marker, read_action_value, read_progress, summarize
from ..vision.board import Portraits, format_board, shop_is_open
from ..vision.hud import read_hud
from ..vision.ocr import Ocr, TextBox, find
from .macros import open_crystals, survey_plane
from .screens import (
    DIFFICULTY_HINT,
    PREPARE_LAYOUT,
    SPOTLIGHT_LAYOUT,
    classify,
    companion_name,
    node_of,
    similar,
    text_signature,
)

ACTION_GAP = 0.7  # 每个操作之后等画面响应


@dataclass
class RunOptions:
    max_steps: int = 600
    max_minutes: float = 120
    approve: Callable[[Decision], bool] | None = None   # 逐步确认(返回 False 则跳过本次操作)
    stop: threading.Event = field(default_factory=threading.Event)
    on_step: Callable[[dict], None] | None = None       # 每一步的记录,给网页实时推送


def _plain(o):
    """numpy 的数字(OCR 坐标等)转成普通数字再写 JSON。"""
    return o.item() if hasattr(o, "item") else str(o)


class Trace:
    def __init__(self, root: Path):
        self.root = root
        (root / "shots").mkdir(parents=True, exist_ok=True)
        self._f = (root / "steps.jsonl").open("a", encoding="utf-8")

    def shot(self, step: int, image: Image.Image) -> str:
        name = f"shots/{step:04d}.jpg"
        img = image.convert("RGB")
        img.thumbnail((960, 540))
        img.save(self.root / name, quality=72)
        return name

    def meta(self, **fields) -> None:
        """合并写入 meta.json(模型、难度、开始与结束时间、结果)。"""
        path = self.root / "meta.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        data.update(fields)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def write(self, record: dict) -> None:
        self._f.write(json.dumps(record, ensure_ascii=False, default=_plain) + "\n")
        self._f.flush()

    def close(self) -> None:
        self._f.close()


class Runner:
    def __init__(self, device, brain: ScreenAgent, trace: Trace, *, ocr: Ocr | None = None,
                 on_companion: Callable[[str], None] | None = None, log: Callable[[str], None] = print,
                 difficulty: str = "A8-1", portraits: Portraits | None = None):
        self.device = device
        self.difficulty = difficulty
        self.brain = brain
        self.trace = trace
        self.ocr = ocr or Ocr()
        self.portraits = portraits
        self.on_companion = on_companion
        self.log = log
        self.run_notes = ""
        self.history: list[str] = []
        self._last_sig: set[str] = set()
        self._waits = 0
        self.in_run = False  # 本次运行是否已经开局(用来区分上一局遗留的结算界面)
        self._same = 0       # 连续几步在同一界面、画面几乎没变且做的不是等待
        self._marks: list[dict] = []  # 本步实际点击 / 拖拽的位置,给网页在截图上标出来
        self._curve: list[tuple[int | None, int | None]] = []  # 当前这场战斗的(剩余行动值, 进度)采样
        self._curve_node: str | None = None
        self.last_battle = ""                                  # 上一场战斗的摘要,给下一次决策
        self.plane_nodes = ""                                  # 本位面的节点序列(进入新位面时程序收集)
        self._surveyed: str | None = None                      # 已经收集过节点的位面
        self._crystal_tries: dict[str, int] = {}               # 每个节点开晶矿的次数
        self._crystal_ok = True                                # 划开晶矿没效果两次后就不再尝试

    # ---------------------------------------------------------------- 执行

    def execute(self, actions: list[Action], boxes: list[TextBox]) -> list[str]:
        results = []
        self._marks = []
        for a in actions:
            try:
                if a.type == "click_text":
                    matches = [b for b in boxes if b.text == a.text] or [b for b in boxes if a.text and a.text in b.text]
                    if len(matches) <= a.nth:
                        results.append(f"{action_text(a)}:界面上没找到这段文字")
                        continue
                    b = matches[a.nth]
                    self.device.click(b.x, b.y)
                    self._marks.append({"type": "click", "x": float(b.x), "y": float(b.y)})
                elif a.type == "click":
                    self.device.click(a.x, a.y)
                    self._marks.append({"type": "click", "x": a.x, "y": a.y})
                elif a.type == "drag":
                    self.device.drag(a.x, a.y, a.x2, a.y2, duration=0.8)
                    self._marks.append({"type": "drag", "x": a.x, "y": a.y, "x2": a.x2, "y2": a.y2})
                elif a.type == "scroll":
                    x, y = (a.x if a.x is not None else 0.5), (a.y if a.y is not None else 0.5)
                    self.device.scroll(x, y, a.notches or -3)
                    self._marks.append({"type": "scroll", "x": x, "y": y})
                elif a.type == "key":
                    self.device.key(a.key)
                elif a.type == "wait":
                    time.sleep(min(a.seconds or 3, 60))
                results.append(action_text(a))
                time.sleep(ACTION_GAP)
            except UserInterrupt:
                raise
            except Exception as e:  # noqa: BLE001 - 单个操作失败记录下来,交给下一步重新判断
                results.append(f"{action_text(a)}:失败({e})")
        return results

    # ---------------------------------------------------------------- 局面识别

    def perceive(self, image, boxes: list[TextBox], node: str | None) -> tuple[str, dict, list[str]]:
        """备战界面:读数字和阵容。返回(给模型的文字, 记进记录的结构, 认出的角色名)。"""
        hud = read_hud(image, boxes, self.ocr, node=node)
        state: dict = {"hud": hud.as_dict()}
        lines = [hud.text()]
        names: list[str] = []
        if self.portraits is not None:
            shop = shop_is_open(boxes)
            if shop:
                learned = self.portraits.learn_from_shop(image, boxes)
                if learned:
                    self.log(f"从商店记下了立绘:{'、'.join(learned)}")
            board = self.portraits.read(image, shop_open=shop)
            state["board"] = [{"zone": b.zone, "index": b.index, "x": round(b.x, 3), "y": round(b.y, 3),
                               "name": b.name, "stars": b.stars, "score": round(b.score, 2)}
                              for b in board if not b.empty]
            lines.append("阵容(「认不出」表示有角色但没认出是谁,可以点开看详情):\n" + format_board(board))
            names = sorted({b.name for b in board if b.name})
        return "\n".join(lines), state, names

    # ---------------------------------------------------------------- 固定流程

    def macros(self, image, boxes: list[TextBox], node: str | None) -> str | None:
        """备战界面、商店收起时先做的固定流程。做了什么就返回说明(这一步不再问模型),什么都没做返回 None。"""
        if not node or shop_is_open(boxes):
            return None
        plane = node.split("-")[0]
        if plane != self._surveyed:
            self._surveyed = plane
            lines = survey_plane(self.device, self.ocr, self.log)
            if lines:
                self.plane_nodes = f"位面 {plane}(从左到右):\n" + "\n".join(f"- {t}" for t in lines)
                self.log(f"位面 {plane} 的节点:{' | '.join(lines)[:300]}")
            return "收集本位面的节点序列"
        tries = self._crystal_tries.get(node, 0)
        if self._crystal_ok and tries < 2:
            opened = open_crystals(self.device, image, self.log)
            if opened is None:
                return None
            self._crystal_tries[node] = tries + 1
            if not opened and sum(self._crystal_tries.values()) >= 2:
                self._crystal_ok = False
                self.log("划开晶矿没有效果,本局不再尝试")
            return "划开晶矿" if opened else "尝试划开晶矿(没变化)"
        return None

    # ---------------------------------------------------------------- 一步

    def step(self, n: int) -> str:
        frame = self.device.grab()
        boxes = self.ocr(frame.image)
        screen = classify(boxes)
        # 战斗中文字有时只认出零星几个:左侧行动值条目能读出数字就算战斗(备战界面的羁绊图标也是金色六边形,只认图标会误判)
        if screen == "other" and has_marker(frame.image) and read_action_value(frame.image, self.ocr) is not None:
            screen = "battle"
        record: dict = {"step": n, "ts": time.strftime("%H:%M:%S"), "screen": screen, "node": node_of(boxes),
                        "shot": self.trace.shot(n, frame.image), "texts": [str(b) for b in boxes]}
        sig = text_signature(boxes)
        if screen in ("run_intro", "environment_choice", "prepare", "battle"):
            self.in_run = True
        if self._curve and screen in ("prepare", "supply", "spotlight_choice", "settlement", "environment_choice"):
            self.last_battle = f"{self._curve_node or ''} " + summarize(self._curve)
            record["battle_summary"] = self.last_battle
            self.history.append(f"[战斗] {self.last_battle}")
            self.log(f"[{n}] 上一场战斗:{self.last_battle}")
            self._curve, self._curve_node = [], None
        actions: list[Action] = []
        decision: Decision | None = None

        if screen == "tap_continue":
            actions = [Action(type="click", x=0.5, y=0.62, reason="点击空白处继续")]
        elif screen == "run_intro":
            name = companion_name(boxes)
            if name:
                self.run_notes = (self.run_notes + f"\n奇遇伙伴:{name}").strip()
                if self.on_companion:
                    self.on_companion(name)
            actions = [Action(type="click_text", text="下一步", reason="开局介绍")]
        elif screen == "home":
            actions = [Action(type="click_text", text="开始「货币战争」")]
        elif screen == "mode_select":
            actions = [Action(type="click_text", text="标准博弈"), Action(type="click_text", text="进入标准博弈")]
        elif screen == "settlement":
            record["note"] = "对局结束"
            if find(boxes, "^下一步$"):
                actions = [Action(type="click_text", text="下一步", reason="离开结算界面")]
        elif screen == "battle":
            av, progress = read_action_value(frame.image, self.ocr), read_progress(boxes)
            self._curve.append((av, progress))
            self._curve_node = record["node"] or self._curve_node
            record["battle"] = {"av": av, "progress": progress}
            actions = [Action(type="wait", seconds=4, reason="战斗进行中")]
        elif screen == "other" and self._waits and similar(sig, self._last_sig) > 0.6 and self._waits < 30:
            actions = [Action(type="wait", seconds=4, reason="画面与上一步相似,继续等待")]
            self._waits += 1
        else:
            if screen == "prepare":
                try:
                    did = self.macros(frame.image, boxes, record["node"])
                except UserInterrupt:
                    raise
                except Exception as e:  # noqa: BLE001 - 固定流程出错就交给模型
                    did = None
                    self.log(f"[{n}] 固定流程出错:{type(e).__name__}: {e}")
                if did:
                    record["actions"] = [did]
                    if did.startswith("收集") and self.plane_nodes:
                        record["plane_nodes"] = self.plane_nodes
                    self.history.append(f"[{screen}] {did}")
                    self._last_sig = sig
                    self.trace.write(record)
                    return screen
            hints = PREPARE_LAYOUT if screen == "prepare" else ""
            if self.plane_nodes and screen in ("prepare", "supply"):
                hints += f"\n\n## 本位面的节点(程序在位面开始时收集)\n{self.plane_nodes}"
            if self.last_battle and screen in ("prepare", "supply", "spotlight_choice"):
                hints += f"\n\n## 上一场战斗(程序记录的行动值和进度)\n{self.last_battle}"
            names: list[str] = []
            if screen == "prepare":
                try:
                    seen, record["state"], names = self.perceive(frame.image, boxes, record["node"])
                    hints += "\n\n## 程序识别的局面\n" + seen
                except Exception as e:  # noqa: BLE001 - 识别出错不影响模型照常决策
                    self.log(f"[{n}] 局面识别出错:{type(e).__name__}: {e}")
            if screen == "difficulty":
                hints = DIFFICULTY_HINT.format(target=self.difficulty)
            elif screen == "spotlight_choice":
                hints = SPOTLIGHT_LAYOUT
            if self._same >= 3:
                hints += (f"\n\n注意:最近 {self._same} 步都在这个界面、画面几乎没变,前面的操作没有起作用。"
                          "先读界面中间的提示文字找原因,换一种做法,不要重复同样的操作。")
            decision = self.brain.decide(boxes, screen=screen, run_notes=self.run_notes, history=self.history,
                                         hints=hints, names=names)
            record["decision"] = to_jsonable(decision)
            if decision.error:
                self.log(f"[{n}] 决策失败:{decision.error}")
                actions = [Action(type="wait", seconds=3, reason="决策失败,稍后重试")]
            else:
                actions = decision.actions
                if decision.run_notes:
                    self.run_notes = decision.run_notes[:800]
                self.log(f"[{n}] {screen} | {decision.analysis[:120]}")
            self._waits = 1 if actions and all(a.type == "wait" for a in actions) else 0

        if any(a.type == "pause" for a in actions):
            record["actions"] = [action_text(a) for a in actions]
            self.trace.write(record)
            return "pause"
        waiting = bool(actions) and all(a.type == "wait" for a in actions)
        self._same = self._same + 1 if (not waiting and similar(sig, self._last_sig) > 0.85) else 0
        results = self.execute(actions, boxes) if actions else []
        record["actions"] = results
        if self._marks:
            record["marks"] = self._marks
        self.history.append(f"[{screen}] " + ";".join(results))
        self._last_sig = sig
        self.trace.write(record)
        return screen

    def run(self, opts: RunOptions) -> str:
        started = time.monotonic()
        for n in range(1, opts.max_steps + 1):
            if opts.stop.is_set():
                return "stopped"
            if time.monotonic() - started > opts.max_minutes * 60:
                return "timeout"
            try:
                screen = self.step(n)
            except UserInterrupt as e:
                self.log(f"[{n}] {e}")
                self.trace.write({"step": n, "error": str(e)})
                return "interrupted"
            except Exception as e:  # noqa: BLE001 - 单步出错不终止整局,记录后稍等重试
                self.log(f"[{n}] 出错:{type(e).__name__}: {e}")
                self.trace.write({"step": n, "error": f"{type(e).__name__}: {e}"})
                time.sleep(3)
                continue
            if opts.on_step:
                opts.on_step({"step": n, "screen": screen})
            if screen == "pause" or (screen == "settlement" and self.in_run):
                return screen
        return "max_steps"
