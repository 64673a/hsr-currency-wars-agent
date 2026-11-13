"""主循环:截图 → 识别界面 → 规则或模型决定 → 执行 → 记录。设计见 docs/TECH_DESIGN.md 第 10 节。"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from ..brain.screen_agent import Action, Decision, ScreenAgent, action_text, to_jsonable
from ..vision.ocr import Ocr, TextBox
from .screens import classify, companion_name, similar, text_signature

ACTION_GAP = 0.7  # 每个操作之后等画面响应


@dataclass
class RunOptions:
    max_steps: int = 600
    max_minutes: float = 120
    approve: Callable[[Decision], bool] | None = None   # 逐步确认(返回 False 则跳过本次操作)
    stop: threading.Event = field(default_factory=threading.Event)
    on_step: Callable[[dict], None] | None = None       # 每一步的记录,给网页实时推送


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

    def write(self, record: dict) -> None:
        self._f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._f.flush()

    def close(self) -> None:
        self._f.close()


class Runner:
    def __init__(self, device, brain: ScreenAgent, trace: Trace, *, ocr: Ocr | None = None,
                 on_companion: Callable[[str], None] | None = None, log: Callable[[str], None] = print):
        self.device = device
        self.brain = brain
        self.trace = trace
        self.ocr = ocr or Ocr()
        self.on_companion = on_companion
        self.log = log
        self.run_notes = ""
        self.history: list[str] = []
        self._last_sig: set[str] = set()
        self._waits = 0

    # ---------------------------------------------------------------- 执行

    def execute(self, actions: list[Action], boxes: list[TextBox]) -> list[str]:
        results = []
        for a in actions:
            try:
                if a.type == "click_text":
                    matches = [b for b in boxes if b.text == a.text] or [b for b in boxes if a.text and a.text in b.text]
                    if len(matches) <= a.nth:
                        results.append(f"{action_text(a)}:界面上没找到这段文字")
                        continue
                    b = matches[a.nth]
                    self.device.click(b.x, b.y)
                elif a.type == "click":
                    self.device.click(a.x, a.y)
                elif a.type == "drag":
                    self.device.drag(a.x, a.y, a.x2, a.y2, duration=0.8)
                elif a.type == "key":
                    self.device.key(a.key)
                elif a.type == "wait":
                    time.sleep(min(a.seconds or 3, 60))
                results.append(action_text(a))
                time.sleep(ACTION_GAP)
            except Exception as e:  # noqa: BLE001 - 单个操作失败记录下来,交给下一步重新判断
                results.append(f"{action_text(a)}:失败({e})")
        return results

    # ---------------------------------------------------------------- 一步

    def step(self, n: int) -> str:
        frame = self.device.grab()
        boxes = self.ocr(frame.image)
        screen = classify(boxes)
        record: dict = {"step": n, "ts": time.strftime("%H:%M:%S"), "screen": screen,
                        "shot": self.trace.shot(n, frame.image), "texts": [str(b) for b in boxes]}
        sig = text_signature(boxes)
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
        elif screen == "other" and self._waits and similar(sig, self._last_sig) > 0.6 and self._waits < 30:
            actions = [Action(type="wait", seconds=4, reason="画面与上一步相似,继续等待")]
            self._waits += 1
        else:
            decision = self.brain.decide(boxes, screen=screen, run_notes=self.run_notes, history=self.history)
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
        results = self.execute(actions, boxes) if actions else []
        record["actions"] = results
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
            screen = self.step(n)
            if opts.on_step:
                opts.on_step({"step": n, "screen": screen})
            if screen in ("settlement", "pause"):
                return screen
        return "max_steps"
