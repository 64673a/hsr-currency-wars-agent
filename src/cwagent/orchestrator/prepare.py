"""备战阶段的语义动作:买、卖、移动、刷新、买经验、出战。设计见 docs/TECH_DESIGN.md 第 8.3 节。

模型只说「买商店第 2 格」「备战席 1 号 → 前台 3 号」,坐标由程序按棋盘布局换算(商店展开时棋盘位置不同),
执行后尽量确认效果,把结果交回模型。刷新商店后停下,让模型看了新商店再决定。
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ..brain.screen_agent import Action, action_text, slot_text
from ..device import UserInterrupt
from ..vision.board import SHOP_X, Slot, slots
from ..vision.boxes import CLOSE_POINT, read_panel
from ..vision.equipment import EquipmentReader, cancel_point, find_compose_confirm
from ..vision.ocr import Ocr, TextBox, find
from ..vision.shop import read_shop, shop_state

SHOP_BUTTON = (0.845, 0.909)      # 右下「商店」/「收起」(同一个位置,1920x1080 实测)
READY_BUTTON = (0.946, 0.694)     # 「出战」,商店收起时
SHOP_CARD_Y = 0.15
GAP = 0.5
ITEM_PREFIX = "道具"      # 备战席里带「开启」的武装箱等道具(loop.perceive 标出来的),不是角色


@dataclass
class PrepareResult:
    results: list[str] = field(default_factory=list)
    marks: list[dict] = field(default_factory=list)
    ready: bool = False        # 已经点了出战,备战阶段结束
    observe: bool = False      # 刷新了商店等:停下来让模型看新界面
    occupied: dict[tuple[str, int], str] | None = None   # 执行完以后各格子里是谁(认不出阵容时为 None)
    equipped: list[tuple[str, str]] = field(default_factory=list)   # (角色, 装备):这次给谁装了什么
    seen: dict[int, tuple[str, list[str]]] = field(default_factory=dict)   # 备战席几号:(道具名, 选项)
    taken: list[int] = field(default_factory=list)                         # 已经开掉的道具格子

    def lineup(self) -> str:
        if self.occupied is None:
            return ""
        zones = {"front": "前台", "back": "后台"}
        return ";".join(f"{label} " + "、".join(n for (z, _), n in sorted(self.occupied.items()) if z == zone)
                        for zone, label in zones.items())


def full(deployed: str | None) -> bool:
    """「已上场/上限」两个数相等。读不出时当作没满(不拦)。"""
    m = re.fullmatch(r"(\d+)/(\d+)", deployed or "")
    return bool(m) and int(m.group(1)) >= int(m.group(2))


def shift(deployed: str, delta: int) -> str:
    m = re.fullmatch(r"(\d+)/(\d+)", deployed)
    return f"{max(0, int(m.group(1)) + delta)}/{m.group(2)}" if m else deployed


class PrepareExecutor:
    def __init__(self, device, ocr: Ocr, *, raw: Callable[[Action, list[TextBox]], str],
                 sleep: Callable[[float], None] | None = None, equipment: EquipmentReader | None = None):
        self.device = device
        self.ocr = ocr
        self.equipment = equipment
        self.raw = raw            # 坐标类操作(click / drag / key ……)交回 Runner 执行
        self.sleep = sleep or (lambda s: time.sleep(s))
        self.shop = False         # 商店是否展开:看得出来时以画面为准,被遮住时沿用上次的判断
        self.frame = None         # 最近一次截图
        self.counts: dict[str, int] | None = None   # 前台、后台的格子数(Runner 识别阵容时更新)
        self.deployed: str | None = None            # 「已上场/上限」,如 5/6(Runner 读 HUD 时更新)

    def _look(self) -> list[TextBox]:
        self.frame = self.device.grab()
        return self.ocr(self.frame.image)

    def shop_now(self, boxes: list[TextBox]) -> bool:
        state = shop_state(boxes)
        if state is not None:
            self.shop = state
        return self.shop

    def _click(self, out: PrepareResult, x: float, y: float) -> None:
        self.device.click(x, y)
        out.marks.append({"type": "click", "x": x, "y": y})

    def set_shop(self, out: PrepareResult, boxes: list[TextBox], want_open: bool) -> list[TextBox]:
        """打开或收起商店,等动画结束(展开时等卡片名字连续两次读到一样)。点了没反应再点一次。"""
        if shop_state(boxes) is want_open:
            self.shop = want_open
            return boxes
        for _ in range(2):
            self._click(out, *SHOP_BUTTON)
            last = None
            for _ in range(6):
                self.sleep(0.4)
                boxes = self._look()
                if shop_state(boxes) is not want_open:
                    continue
                cards = read_shop(boxes)
                if not want_open or cards == last:
                    self.shop = want_open
                    return boxes
                last = cards
        self.shop_now(boxes)
        return boxes

    def run(self, actions: list[Action], boxes: list[TextBox], board: list[Slot] | None) -> PrepareResult:
        out = PrepareResult()
        # 各格子里有没有角色,随移动、买卖更新;认不出阵容时为 None,不做检查
        occupied: dict[tuple[str, int], str] | None = None
        if board is not None:
            occupied = {(s.zone, s.index): (s.name or "认不出的角色") for s in board if not s.empty}
        self.shop_now(boxes)
        pending = [a.model_copy() for a in actions]
        while pending:
            a = pending.pop(0)
            text = action_text(a)
            try:
                if a.type == "buy":
                    boxes = self.set_shop(out, boxes, True)
                    if not self.shop:
                        out.results.append(f"{text}:商店没有打开")
                        out.observe = True
                        break
                    name = read_shop(boxes)[a.slot - 1][1]
                    self._click(out, SHOP_X[a.slot - 1], SHOP_CARD_Y)
                    self.sleep(GAP)
                    boxes = self._look()
                    still = read_shop(boxes)[a.slot - 1][1]
                    if name and still == name:
                        out.results.append(f"{text}({name}):没买成,可能金币不够或备战席满了")
                        continue
                    if name is None:
                        out.results.append(f"{text}:这一格没认出角色(可能已经买走了)")
                        continue
                    if occupied is not None:
                        free = [i for i in range(9) if ("bench", i) not in occupied]
                        if free:
                            occupied[("bench", free[0])] = name or "新买的角色"
                    out.results.append(f"{text}" + (f"({name})" if name else ""))
                elif a.type in ("move", "sell", "open", "take"):
                    src = (a.zone, a.index - 1)
                    if occupied is not None and src not in occupied:
                        out.results.append(f"{text}:{slot_text(a.zone, a.index)}是空的")
                        continue
                    is_item = occupied is not None and occupied[src].startswith(ITEM_PREFIX)
                    if a.type in ("move", "sell") and is_item:
                        out.results.append(f"{text}:{slot_text(a.zone, a.index)}是道具,不是角色,不能上场或出售")
                        continue
                    if a.type in ("open", "take") and occupied is not None and not is_item:
                        out.results.append(f"{text}:{slot_text(a.zone, a.index)}不是可开启的道具")
                        continue
                    layout = {(s.zone, s.index): s for s in slots(self.shop_now(boxes), self.counts)}
                    if src not in layout:
                        out.results.append(f"{text}:没有{slot_text(a.zone, a.index)}这个格子")
                        continue
                    if a.type == "open":
                        seen = self.peek(out, layout[src].x, layout[src].y, a.index)
                        out.results.append(f"{text}:" + (f"{seen[0]},可选 {'、'.join(seen[1])}(没选,道具留着)"
                                                         if seen else "没有打开选择面板"))
                        continue
                    if a.type == "take":
                        got = self.take(out, layout[src].x, layout[src].y, a.text or "")
                        out.results.append(f"{text}:{got}")
                        if got.startswith("拿到"):
                            out.taken.append(a.index)
                            if occupied is not None:
                                occupied.pop(src, None)
                        out.observe = not got.startswith("拿到")
                        if out.observe:
                            break
                        continue
                    if a.type == "move":
                        dst = (a.to_zone, a.to_index - 1)
                        if dst not in layout:
                            out.results.append(f"{text}:没有{slot_text(a.to_zone, a.to_index)}这个格子")
                            continue
                        up = a.zone == "bench" and a.to_zone != "bench"
                        if up and occupied is not None and dst not in occupied and full(self.deployed):
                            out.results.append(f"{text}:上场已满({self.deployed}),先把场上的角色移回备战席或卖掉,"
                                               "或者升级")
                            continue
                        p, q = layout[src], layout[dst]
                        self.device.drag(p.x, p.y, q.x, q.y, duration=0.6)
                        if occupied is not None and self.deployed and (dst not in occupied) != (a.to_zone == "bench"):
                            self.deployed = shift(self.deployed, 1 if up else -1 if a.to_zone == "bench" else 0)
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
                        if a.zone != "bench" and self.deployed:
                            self.deployed = shift(self.deployed, -1)
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
                    self.sleep(0.8)   # 升级有动画,紧接着点商店会点不上(2026-11-14 实机)
                    out.results.append(text)
                elif a.type in ("compose", "equip"):
                    done = self._equipment(a, text, out, occupied, boxes)
                    if done is None:
                        break
                    _shift(pending, done)
                elif a.type == "ready":
                    boxes = self.set_shop(out, boxes, False)
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

    # ---------------------------------------------------------------- 道具(武装箱等)

    def _open(self, out: PrepareResult, x: float, y: float):
        """点开道具,返回选择面板。点得太快有时弹不出来(M7A 记的游戏 bug),就按住一会儿再点一次。"""
        self._click(out, x, y)
        self.sleep(1.0)
        panel = read_panel(self._look())
        if panel is None:
            self.device.drag(x, y, x, y, duration=0.25)
            out.marks.append({"type": "click", "x": x, "y": y})
            self.sleep(1.5)
            panel = read_panel(self._look())
        return panel

    def peek(self, out: PrepareResult, x: float, y: float, index: int) -> tuple[str, list[str]] | None:
        """点开道具看选项,不选,点空白处收起(道具留着)。返回(道具名, 选项)。"""
        panel = self._open(out, x, y)
        if panel is None:
            return None
        title, options = panel
        self._click(out, *CLOSE_POINT)
        self.sleep(0.6)
        seen = (title, [o.text for o in options])
        out.seen[index] = seen
        return seen

    def take(self, out: PrepareResult, x: float, y: float, name: str) -> str:
        """点开道具,选名字为 name 的选项。返回结果说明(成功时以「拿到」开头)。"""
        panel = self._open(out, x, y)
        if panel is None:
            return "没有打开选择面板"
        title, options = panel
        pick = next((o for o in options if o.text == name), None) or next((o for o in options if name and name in o.text),
                                                                          None)
        if pick is None:
            self._click(out, *CLOSE_POINT)
            self.sleep(0.6)
            return f"{title}里没有「{name}」,可选 {'、'.join(o.text for o in options)}(没选,道具留着)"
        self._click(out, pick.x, pick.y - 0.08)   # 选项卡片(名字上方的图标处)
        self.sleep(1.0)
        boxes = self._look()
        if read_panel(boxes) is not None:          # 还要确认
            ok = find(boxes, "^(确认|确定|选择)$")
            if ok is not None:
                self._click(out, ok.x, ok.y)
                self.sleep(1.0)
                boxes = self._look()
        if read_panel(boxes) is not None:
            return f"点了「{name}」但选择面板还在,没拿成"
        return f"拿到 {name}(来自{title})"

    # ---------------------------------------------------------------- 装备

    def _equipment(self, a: Action, text: str, out: PrepareResult, occupied, boxes) -> int | None:
        """合成或装备。成功返回装备栏里被用掉的编号(后面的装备会往前顺移);失败时记下原因、返回 None 停下来。"""
        if self.equipment is None:
            out.results.append(f"{text}:没有装备识别(知识库还没同步)")
            return None
        if a.item == 0 or (a.type == "compose" and a.onto == 0):
            out.results.append(f"{action_text(a).split(' ')[0]}:这件装备已经在前面的操作里用掉了")
            return None
        before = self.equipment.read(self.device.grab().image)
        item = next((i for i in before if i.index == a.item), None)
        if item is None:
            out.results.append(f"{text}:装备栏没有第 {a.item} 件(现在有 {len(before)} 件)")
            out.observe = True
            return None
        if a.type == "compose":
            target = next((i for i in before if i.index == a.onto), None)
            if target is None or a.onto == a.item:
                out.results.append(f"{text}:装备栏没有第 {a.onto} 件")
                out.observe = True
                return None
            self.device.drag(item.x, item.y, target.x, target.y, duration=0.6)
            out.marks.append({"type": "drag", "x": item.x, "y": item.y, "x2": target.x, "y2": target.y})
            self.sleep(1.2)
            ok = find_compose_confirm(self.device.grab().image)
            if ok is None:
                out.results.append(f"{text}({item.name or '?'}+{target.name or '?'}):没有出现合成确认,这两件可能不能合成")
                out.observe = True
                return None
            # 确认框有弹出动画,动画中点击会落空(2026-11-14 实机);点了还在就再点,最后取消,不能把它留在屏幕上
            for _ in range(3):
                self.sleep(0.4)
                ok = find_compose_confirm(self.device.grab().image) or ok
                self._click(out, *ok)
                self.sleep(1.0)
                if find_compose_confirm(self.device.grab().image) is None:
                    break
            else:
                self._click(out, *cancel_point(ok))
                self.sleep(0.8)
                out.results.append(f"{text}:合成确认点不下去,已取消")
                out.observe = True
                return None
            after = self.equipment.read(self.device.grab().image)
            if len(after) != len(before) - 1:
                out.results.append(f"{text}:点了确认,但装备数没有变化,可能没合成成功")
                out.observe = True
                return None
            keep = min(a.item, a.onto)
            product = next((i.name for i in after if i.index == keep), None) or "认不出的装备"
            out.results.append(f"{text}({item.name}+{target.name}):合成了 {product},现在是 {keep} 号")
            return max(a.item, a.onto)
        # 装备给角色
        src = (a.zone, a.index - 1)
        if occupied is not None and src not in occupied:
            out.results.append(f"{text}:{slot_text(a.zone, a.index)}没有角色")
            return None
        layout = {(s.zone, s.index): s for s in slots(self.shop_now(boxes), self.counts)}
        if src not in layout:
            out.results.append(f"{text}:没有{slot_text(a.zone, a.index)}这个格子")
            return None
        dst = layout[src]
        self.device.drag(item.x, item.y, dst.x, dst.y, duration=0.6)
        out.marks.append({"type": "drag", "x": item.x, "y": item.y, "x2": dst.x, "y2": dst.y})
        self.sleep(1.0)
        after = self.equipment.read(self.device.grab().image)
        if len(after) != len(before) - 1:
            out.results.append(f"{text}({item.name}):装备栏没有变化,可能没装上(角色的装备可能已经满了)")
            out.observe = True
            return None
        who = occupied.get(src, "") if occupied is not None else ""
        out.results.append(f"{text}:{item.name} 给了 {who or slot_text(a.zone, a.index)}")
        if who and item.name:
            out.equipped.append((who, item.name))
        return a.item


def _shift(pending: list[Action], removed: int) -> None:
    """装备栏第 removed 件用掉后,后面的往前顺移:把还没执行的动作里引用的编号跟着改。"""
    for a in pending:
        for f in ("item", "onto"):
            v = getattr(a, f)
            if not v:
                continue
            if v == removed:
                setattr(a, f, 0)    # 指向已经用掉的那件
            elif v > removed:
                setattr(a, f, v - 1)
