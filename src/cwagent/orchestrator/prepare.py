"""备战阶段的语义动作:买、卖、移动、刷新、买经验、出战。设计见 docs/TECH_DESIGN.md 第 8.3 节。

模型只说「买商店第 2 格」「备战席 1 号 → 前台 3 号」,坐标由程序按棋盘布局换算(商店展开时棋盘位置不同),
执行后尽量确认效果,把结果交回模型。刷新商店后停下,让模型看了新商店再决定。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ..brain.screen_agent import Action, action_text, slot_text
from ..device import UserInterrupt
from ..vision.board import SHOP_X, Slot, shop_is_open, slots
from ..vision.ocr import Ocr, TextBox, find
from ..vision.shop import read_shop

SHOP_BUTTON = (0.845, 0.909)      # 右下「商店」,识别不到文字时用(1920x1080 实测)
READY_BUTTON = (0.946, 0.694)     # 「出战」,商店收起时
SHOP_CARD_Y = 0.15
GAP = 0.5


@dataclass
class PrepareResult:
    results: list[str] = field(default_factory=list)
    marks: list[dict] = field(default_factory=list)
    ready: bool = False        # 已经点了出战,备战阶段结束
    observe: bool = False      # 刷新了商店等:停下来让模型看新界面
    occupied: dict[tuple[str, int], str] | None = None   # 执行完以后各格子里是谁(认不出阵容时为 None)

    def lineup(self) -> str:
        if self.occupied is None:
            return ""
        zones = {"front": "前台", "back": "后台"}
        return ";".join(f"{label} " + "、".join(n for (z, _), n in sorted(self.occupied.items()) if z == zone)
                        for zone, label in zones.items())


class PrepareExecutor:
    def __init__(self, device, ocr: Ocr, *, raw: Callable[[Action, list[TextBox]], str],
                 sleep: Callable[[float], None] | None = None):
        self.device = device
        self.ocr = ocr
        self.raw = raw            # 坐标类操作(click / drag / key ……)交回 Runner 执行
        self.sleep = sleep or (lambda s: time.sleep(s))

    def _look(self) -> list[TextBox]:
        return self.ocr(self.device.grab().image)

    def _click(self, out: PrepareResult, x: float, y: float) -> None:
        self.device.click(x, y)
        out.marks.append({"type": "click", "x": x, "y": y})

    def _set_shop(self, out: PrepareResult, boxes: list[TextBox], want_open: bool) -> list[TextBox]:
        if shop_is_open(boxes) == want_open:
            return boxes
        b = find(boxes, "^商店$" if want_open else "^收起$")
        if b is not None:
            self._click(out, b.x, b.y)
        elif want_open:
            self._click(out, *SHOP_BUTTON)
        else:
            return boxes
        self.sleep(0.8)
        return self._look()

    def run(self, actions: list[Action], boxes: list[TextBox], board: list[Slot] | None) -> PrepareResult:
        out = PrepareResult()
        # 各格子里有没有角色,随移动、买卖更新;认不出阵容时为 None,不做检查
        occupied: dict[tuple[str, int], str] | None = None
        if board is not None:
            occupied = {(s.zone, s.index): (s.name or "认不出的角色") for s in board if not s.empty}
        for a in actions:
            text = action_text(a)
            try:
                if a.type == "buy":
                    boxes = self._set_shop(out, boxes, True)
                    cards = read_shop(boxes)
                    name = cards[a.slot - 1][1]
                    if name is None and any(n for _, n, _ in cards):   # 别的格子认得出,这格没字:已经买走了
                        out.results.append(f"{text}:这一格是空的")
                        continue
                    self._click(out, SHOP_X[a.slot - 1], SHOP_CARD_Y)
                    self.sleep(GAP)
                    boxes = self._look()
                    still = read_shop(boxes)[a.slot - 1][1]
                    if name and still == name:
                        out.results.append(f"{text}({name}):没买成,可能金币不够或备战席满了")
                        continue
                    if occupied is not None:
                        free = [i for i in range(9) if ("bench", i) not in occupied]
                        if free:
                            occupied[("bench", free[0])] = name or "新买的角色"
                    out.results.append(f"{text}" + (f"({name})" if name else ""))
                elif a.type in ("move", "sell"):
                    src = (a.zone, a.index - 1)
                    if occupied is not None and src not in occupied:
                        out.results.append(f"{text}:{slot_text(a.zone, a.index)}是空的")
                        continue
                    layout = {(s.zone, s.index): s for s in slots(shop_is_open(boxes))}
                    if src not in layout:
                        out.results.append(f"{text}:没有{slot_text(a.zone, a.index)}这个格子")
                        continue
                    if a.type == "move":
                        dst = (a.to_zone, a.to_index - 1)
                        if dst not in layout:
                            out.results.append(f"{text}:没有{slot_text(a.to_zone, a.to_index)}这个格子")
                            continue
                        p, q = layout[src], layout[dst]
                        self.device.drag(p.x, p.y, q.x, q.y, duration=0.6)
                        out.marks.append({"type": "drag", "x": p.x, "y": p.y, "x2": q.x, "y2": q.y})
                        if occupied is not None:
                            moving, there = occupied.pop(src), occupied.pop(dst, None)
                            occupied[dst] = moving
                            if there:
                                occupied[src] = there
                        self.sleep(GAP)
                        out.results.append(text)
                    else:
                        p = layout[src]
                        self._click(out, p.x, p.y)
                        self.sleep(0.7)
                        boxes = self._look()
                        sell = find(boxes, "^出售")
                        if sell is None:
                            out.results.append(f"{text}:点开角色后没找到「出售」按钮")
                            out.observe = True
                            break
                        self._click(out, sell.x, sell.y)
                        self.sleep(GAP)
                        boxes = self._look()
                        confirm = find(boxes, "^确认$")
                        if confirm is not None:
                            self._click(out, confirm.x, confirm.y)
                            self.sleep(GAP)
                            boxes = self._look()
                        if occupied is not None:
                            occupied.pop(src, None)
                        out.results.append(text)
                elif a.type == "refresh":
                    self.device.key("d")
                    self.sleep(0.8)
                    out.results.append(f"{text}(停下来看新商店)")
                    out.observe = True
                    break
                elif a.type == "buy_exp":
                    for _ in range(a.times or 1):
                        self.device.key("f")
                        self.sleep(0.3)
                    out.results.append(text)
                elif a.type == "ready":
                    boxes = self._set_shop(out, boxes, False)
                    self._click(out, *READY_BUTTON)
                    self.sleep(1.0)
                    out.results.append(text)
                    out.ready = True
                    break
                else:
                    out.results.append(self.raw(a, boxes))
                    if a.type != "wait":
                        boxes = self._look()
            except UserInterrupt:
                raise
            except Exception as e:  # noqa: BLE001 - 单个动作出错:记下来,剩下的交给模型重新判断
                out.results.append(f"{text}:失败({type(e).__name__}: {e})")
                out.observe = True
                break
        out.occupied = occupied
        return out
