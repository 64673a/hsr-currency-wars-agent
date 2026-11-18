"""备战阶段的语义动作:买、卖、移动、刷新、买经验、出战。设计见 docs/TECH_DESIGN.md 第 8.3 节。

模型只说「买商店第 2 格」「备战席 1 号 → 前台 3 号」,坐标由程序按棋盘布局换算(商店展开时棋盘位置不同),
执行后尽量确认效果,把结果交回模型。刷新商店后停下,让模型看了新商店再决定。
"""

from __future__ import annotations

import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from ..brain.screen_agent import Action, action_text, slot_text
from .. import paths
from ..device import UserInterrupt
from ..vision.board import SHOP_X, Slot, occupied_slots, slots
from ..vision.cards import FIXED_UNITS
from ..vision.boxes import CLOSE_POINT, read_panel
from ..vision.equipment import (
    EquipmentReader,
    Item,
    cancel_point,
    find_compose_confirm,
    format_items,
)
from ..vision.hud import read_level
from ..vision.ocr import Ocr, TextBox, find
from ..vision.shop import format_shop, read_shop, shop_state

SHOP_BUTTON = (0.845, 0.909)      # 右下「商店」/「收起」(同一个位置,1920x1080 实测)
BOARD_BLANK = (0.500, 0.550)      # 棋盘中间空白:点这里也能关掉商店(trail-cli 的 SHOP_CLOSE_POINT),按钮点不动时用它
READY_BUTTON = (0.946, 0.694)     # 「出战」,商店收起时
REFRESH_REGION = (0.78, 0.40, 0.12, 0.10)          # 商店右侧的「刷新」按钮(2026-11-16 实机约 0.842, 0.451)
REFRESH_PRICE_REGION = (0.80, 0.455, 0.10, 0.075)  # 「刷新」下面那个价格(约 0.852, 0.492)
NO_MONEY = r"金币不足|不足|余额不足"
SHOP_CARD_Y = 0.15
GAP = 0.5
ITEM_PREFIX = "道具"      # 备战席里带「开启」的武装箱等道具(loop.perceive 标出来的),不是角色
REFUSED = "不可调整|无法上阵|无法移动"   # 拖动被游戏拒绝时界面中间的提示(2026-11-15 实机:「当前情况下不可调整」)
BEHIND_SHOP = {"选择主角", "出战"}   # 右下的按钮,商店展开时被挡住(2026-11-16 外部 agent 实测:点了没反应)
UNDERSTAFFED = "人数未达上限"    # 出战时的「可出战角色人数未达上限,是否确认出战?」(实机文字在 0.498, 0.473)
UNDERSTAFFED_CANCEL = (0.43, 0.572)     # 读不到「取消」时的位置(trail-cli 的 BATTLE_TEAM_COUNT_CANCEL_POINT)
UNDERSTAFFED_CONFIRM = (0.57, 0.572)    # 读不到「确认」时的位置(和取消左右对称,待实机核对)


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
    sold: list[str] = field(default_factory=list)                          # 卖掉的角色名(清掉他的装备记录)
    ready_image: object = None   # 同一轮先做了别的操作再出战时,点出战前的画面(出战时的金币要按它读)

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


def raise_cap(deployed: str, delta: int) -> str:
    """「已上场/上限」的上限加 delta(升级)。"""
    m = re.fullmatch(r"(\d+)/(\d+)", deployed)
    return f"{m.group(1)}/{int(m.group(2)) + delta}" if m else deployed


class PrepareExecutor:
    def __init__(self, device, ocr: Ocr, *, raw: Callable[[Action, list[TextBox]], str],
                 sleep: Callable[[float], None] | None = None, equipment: EquipmentReader | None = None,
                 before_ready: Callable[[], str | None] | None = None, capture: Callable | None = None):
        self.device = device
        self.ocr = ocr
        self.equipment = equipment
        self.raw = raw            # 坐标类操作(click / drag / key ……)交回 Runner 执行
        self.before_ready = before_ready   # 点出战前(商店已收起)先做的事;返回说明时不出战,让模型先看新局面
        self.sleep = sleep or (lambda s: time.sleep(s))
        self.capture = capture or (lambda: self.device.grab())
        self.shop = False         # 商店是否展开:看得出来时以画面为准,被遮住时沿用上次的判断
        self.frame = None         # 最近一次截图
        self.counts: dict[str, int] | None = None   # 前台、后台的格子数(Runner 识别阵容时更新)
        self.deployed: str | None = None            # 「已上场/上限」,如 5/6(Runner 读 HUD 时更新)
        self.node: str | None = None                # 当前节点(Runner 每步更新)
        self.understaffed: set[str] = set()         # 已经因为「人数未达上限」拦过一次出战的节点

    def _look(self) -> list[TextBox]:
        self.frame = self.capture()
        return self.ocr(self.frame.image)

    def shop_now(self, boxes: list[TextBox]) -> bool:
        state = shop_state(boxes)
        if state is not None:
            self.shop = state
        return self.shop

    def _click(self, out: PrepareResult, x: float, y: float) -> None:
        self.device.click(x, y)
        out.marks.append({"type": "click", "x": x, "y": y})

    def _level(self) -> int | None:
        """左下「购买经验」上的等级,商店展开时也读得到(读不出返回 None)。"""
        try:
            return read_level(self.capture().image, self.ocr)
        except UserInterrupt:
            raise
        except Exception:  # noqa: BLE001 - 读等级只是为了跟上上场上限,失败就当拿不准
            return None

    def _after_level_up(self, before: int | None, after: int | None) -> str:
        """买经验后跟上上场上限(上限 = 等级 + 财富宝钻):升了几级上限就加几。读不出等级时上场人数拿不准,
        标成过期(None),之后不用它拦上场,改成上场后核对——原来缓存一直是升级前的数,模型的上场被程序自己拦下
        (2026-11-16 实机,模型在笔记里记下了这个问题)。"""
        if before is None or after is None:
            self.deployed = None
            return ""
        if after > before:
            if self.deployed:
                self.deployed = raise_cap(self.deployed, after - before)
            return f":升到 {after} 级" + (f",上场 {self.deployed}" if self.deployed else "")
        return f":现在 {after} 级"

    def answer_understaffed(self, out: PrepareResult, boxes: list[TextBox]) -> str | None:
        """点出战后弹出「可出战角色人数未达上限,是否确认出战?」时怎么答(设计文档第 9 节,参考 trail-cli):
        这个节点第一次弹就点「取消」,返回给模型的说明;模型看过以后又发出战(比如备战席确实没人可上),
        就照它的决定点「确认」,返回 None。两种情况都不勾「本局不再提示」——原来勾上后,之后少上人游戏就不再提醒。
        没弹这个框返回 None。"""
        if find(boxes, UNDERSTAFFED) is None:
            return None
        node = self.node or ""
        if node in self.understaffed:
            ok = find(boxes, "^确认$")
            self._click(out, *((float(ok.x), float(ok.y)) if ok is not None else UNDERSTAFFED_CONFIRM))
            self.sleep(1.0)
            return None
        self.understaffed.add(node)
        cancel = find(boxes, "^取消$")
        self._click(out, *((float(cancel.x), float(cancel.y)) if cancel is not None else UNDERSTAFFED_CANCEL))
        self.sleep(0.8)
        seats = f"(上场 {self.deployed})" if self.deployed else ""
        return (f"没有出战:游戏提示「可出战角色人数未达上限」{seats},已点取消。还有空着的上场名额,"
                "先把角色放上场再出战;确实没有角色可上就再发一次 ready,会照常出战")

    def set_shop(self, out: PrepareResult, boxes: list[TextBox], want_open: bool) -> list[TextBox]:
        """打开或收起商店,等动画结束(展开时等卡片名字连续两次读到一样)。

        点哪里:打开点右下按钮;关闭先点右下「收起」,还关不掉就点棋盘中间空白——trail-cli 关商店就是点
        棋盘中间 (0.5, 0.55),不依赖按钮位置。每步都重新读界面确认,不靠上一轮的猜测。
        """
        if shop_state(boxes) is want_open:
            self.shop = want_open
            return boxes
        points = (SHOP_BUTTON,) if want_open else (SHOP_BUTTON, BOARD_BLANK)
        for point in points:
            self._click(out, *point)
            last = None
            for _ in range(5):
                self.sleep(0.4)
                boxes = self._look()
                state = shop_state(boxes)
                if state is None:
                    continue                     # 一时看不出开合(动画中),再看一眼
                if state is not want_open:
                    break                        # 点出了反效果(比如收起时点到了商店),别在同一个点上反复等
                cards = read_shop(boxes)
                if not want_open or cards == last:
                    self.shop = want_open
                    return boxes
                last = cards
        self.shop_now(boxes)
        return boxes

    def _refresh(self, out: PrepareResult, boxes: list[TextBox], text: str) -> str:
        """点商店右侧的「刷新」按钮,再核对 5 张卡真的换了。

        原来按 D 键(2026-11-16 实机两局按了都没反应:金币和卡片跟刷新前一样,模型因此以为刷新不可用,
        整局没再搜过牌)。M7A 的做法是直接点「刷新」;点完再读一遍商店,没变就把界面提示(金币不足等)
        和刷新价一起报回去,不让模型以为自己刷新过了。
        """
        before = read_shop(boxes)
        button = find(boxes, "^刷新$", region=REFRESH_REGION)
        if button is not None:
            self._click(out, float(button.x), float(button.y))
        else:
            self.device.key("d")          # 认不出按钮时退回快捷键
        self.sleep(1.0)
        boxes = self._look()
        after = read_shop(boxes)
        if after != before:
            return f"{text}(停下来看新商店)"
        hint = find(boxes, NO_MONEY)
        price = find(boxes, r"^\d{1,2}$", region=REFRESH_PRICE_REGION)
        why = (f"界面提示「{hint.text}」" if hint is not None
               else f"刷新价 {price.text} 金币,可能不够" if price is not None else "可能金币不足")
        return (f"刷新没有生效:金币和商店都没变(还是 {format_shop(after)})——{why}。"
                "不要反复点刷新,先看现有卡片怎么用")

    def _drag_slot(self, out: PrepareResult, src: tuple[str, int], dst: tuple[str, int],
                   boxes: list[TextBox]) -> tuple[str | None, list[TextBox]]:
        """把 src 格的角色拖到 dst 格,再看一眼:被游戏拒绝(界面中间的提示)或目标格还是空的就算没成功。
        商店开着和收着时棋盘位置不同,而商店会被别的操作带着开合(实机见过买完卡后商店收起),所以每次按最新画面换算坐标,
        没成功再试一次。返回(失败原因或 None, 最新的界面文字)。"""
        for _ in range(2):
            layout = {(s.zone, s.index): s for s in slots(self.shop_now(boxes), self.counts)}
            p, q = layout[src], layout[dst]
            self.device.drag(p.x, p.y, q.x, q.y, duration=0.6)
            out.marks.append({"type": "drag", "x": p.x, "y": p.y, "x2": q.x, "y2": q.y})
            self.sleep(GAP)
            boxes = self._look()
            refused = find(boxes, REFUSED)
            if refused is not None:
                return f"界面提示「{refused.text}」", boxes
            filled = occupied_slots(self.frame.image, self.shop_now(boxes), self.counts)
            if filled is None or dst in filled:
                return None, boxes
        return f"拖完以后{slot_text(dst[0], dst[1] + 1)}还是空的", boxes

    def run(self, actions: list[Action], boxes: list[TextBox], board: list[Slot] | None, *,
            final: bool = False) -> PrepareResult:
        """final:本备战阶段最后一次决策。出战要单独一轮(维护者要求:模型以为操作都成功了就出战,实际上场情况常常
        和它想的不一样);只有最后一次决策里,前面的操作都成功时才接着出战。"""
        out = PrepareResult()
        others = any(x.type not in ("wait", "ready") for x in actions)
        failed = False
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
                    if occupied is not None and occupied[src] in FIXED_UNITS and a.type in ("move", "sell"):
                        out.results.append(f"{text}:{slot_text(a.zone, a.index)}是{occupied[src]},固定在后台,不能拖动、不能出售")
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
                        if occupied is not None and occupied.get(dst) in FIXED_UNITS:
                            out.results.append(f"{text}:{slot_text(a.to_zone, a.to_index)}是{occupied[dst]},不能换位,换一个格子")
                            continue
                        up = a.zone == "bench" and a.to_zone != "bench"
                        if up and occupied is not None and dst not in occupied and full(self.deployed):
                            out.results.append(f"{text}:上场已满({self.deployed}),先把场上的角色移回备战席或卖掉,"
                                               "或者升级")
                            continue
                        problem, boxes = self._drag_slot(out, src, dst, boxes)
                        if problem:
                            out.results.append(f"{text}:没成功({problem}),后面的操作先不做")
                            out.observe = failed = True
                            break
                        # 上场人数只在挪到空格时变:备战席 → 场上 +1,场上 → 备战席 -1;换位、场上挪场上、
                        # 备战席挪备战席都不变(原来的条件把下场写反了:2026-11-18 那局火花下场后还算 3/3,挡住了补人)
                        down = a.zone != "bench" and a.to_zone == "bench"
                        if occupied is not None and self.deployed and dst not in occupied and (up or down):
                            self.deployed = shift(self.deployed, 1 if up else -1)
                        if occupied is not None:
                            moving, there = occupied.pop(src), occupied.pop(dst, None)
                            occupied[dst] = moving
                            if there:
                                occupied[src] = there
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
                            gone = occupied.pop(src, None)
                            if gone:
                                out.sold.append(gone)
                        if a.zone != "bench" and self.deployed:
                            self.deployed = shift(self.deployed, -1)
                        out.results.append(text)
                elif a.type == "refresh":
                    out.results.append(self._refresh(out, boxes, text))
                    out.observe = True
                    break
                elif a.type == "buy_exp":
                    before = self._level()
                    for _ in range(a.times or 1):
                        self.device.key("f")
                        self.sleep(0.3)
                    self.sleep(0.8)   # 升级有动画,紧接着点商店会点不上(2026-11-14 实机)
                    out.results.append(text + self._after_level_up(before, self._level()))
                elif a.type in ("compose", "equip"):
                    done = self._equipment(a, text, out, occupied, boxes)
                    if done is None:
                        failed = True
                        break
                    if a.type == "compose" and any(x.type in ("compose", "equip") for x in pending):
                        # 合成后装备栏会重新排序(2026-11-15 实机:和平手枪+折叠小刀合成后,原来 3 号的生命之花排到了 1 号),
                        # 之前给的编号不一定还对
                        out.results.append("合成后装备栏的顺序变了,后面的合成 / 装备先不做:按上面的新装备栏重新给编号")
                        out.observe = True
                        break
                    _shift(pending, done)
                elif a.type == "ready":
                    if others and (not final or failed):
                        out.results.append("出战:这一轮没有出战。出战要单独一轮:先看上面这些操作之后的实际局面(上场人数、"
                                           "站位、装备),确认无误再只发 ready")
                        out.observe = True
                        break
                    boxes = self.set_shop(out, boxes, False)
                    note = self.before_ready() if self.before_ready else None
                    if note:
                        out.results.append(note)
                        out.observe = True
                        break
                    if others and self.frame is not None:
                        out.ready_image = self.frame.image
                    self._click(out, *READY_BUTTON)
                    self.sleep(1.0)
                    blocked = self.answer_understaffed(out, self._look())
                    if blocked:
                        out.results.append(f"{text}:{blocked}")
                        out.observe = True
                        break
                    out.results.append(text)
                    out.ready = True
                    break
                else:
                    if a.type == "click_text" and a.text in BEHIND_SHOP:
                        boxes = self.set_shop(out, boxes, False)   # 商店开着时点不到这些按钮
                    out.results.append(self.raw(a, boxes))
                    if a.type != "wait":
                        boxes = self._look()
            except UserInterrupt:
                raise
            except Exception as e:
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
        before = self.equipment.read(self.capture().image)
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
            shot = self.capture().image
            ok = find_compose_confirm(shot)
            if ok is None:
                # 留一张现场图:是拖拽没触发、还是确认框换了样式,只能靠它判断(2026-11-16 两次合成都没确认框)
                try:
                    debug = paths.data_dir() / "debug"
                    debug.mkdir(parents=True, exist_ok=True)
                    shot.save(debug / f"compose-{time.strftime('%H%M%S')}.png")
                except Exception:  # noqa: BLE001 - 存图失败不影响结果
                    pass
                out.results.append(f"{text}({item.name or '?'}+{target.name or '?'}):没有出现合成确认,"
                                   "这两件可能不能合成(截图已存 data/debug,可用 cwagent 排查)")
                out.observe = True
                return None
            # 确认框有弹出动画,动画中点击会落空(2026-11-14 实机);点了还在就再点,最后取消,不能把它留在屏幕上
            for _ in range(3):
                self.sleep(0.4)
                ok = find_compose_confirm(self.capture().image) or ok
                self._click(out, *ok)
                self.sleep(1.0)
                if find_compose_confirm(self.capture().image) is None:
                    break
            else:
                self._click(out, *cancel_point(ok))
                self.sleep(0.8)
                out.results.append(f"{text}:合成确认点不下去,已取消")
                out.observe = True
                return None
            after = self.equipment.read(self.capture().image)
            if len(after) != len(before) - 1:
                out.results.append(f"{text}:点了确认,但装备数没有变化,可能没合成成功")
                out.observe = True
                return None
            rest = Counter(i.name for i in before if i.index not in (a.item, a.onto))
            new = [i for i in after if (Counter(x.name for x in after) - rest)[i.name] > 0]
            product = f"{new[0].name or '认不出的装备'}(现在是 {new[0].index} 号)" if len(new) == 1 else "认不出的装备"
            out.results.append(f"{text}({item.name}+{target.name}):合成了 {product};装备栏现在是 {format_items(after)}")
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
        after = self.equipment.read(self.capture().image)
        if len(after) != len(before) - 1:
            out.results.append(f"{text}({item.name}):装备栏没有变化,可能没装上(角色的装备可能已经满了)")
            out.observe = True
            return None
        if _lost(before, after) not in (None, Counter({item.name: 1})):
            lost = "、".join(n for n in _lost(before, after).elements()) or "?"
            out.results.append(f"{text}:装备栏少掉的不是 {item.name}(少了 {lost}),可能装错了;"
                               f"装备栏现在是 {format_items(after)}")
            out.observe = True
            return None
        who = occupied.get(src, "") if occupied is not None else ""
        out.results.append(f"{text}:{item.name} 给了 {who or slot_text(a.zone, a.index)}")
        if who and item.name:
            out.equipped.append((who, item.name))
        return a.item


def _lost(before: list[Item], after: list[Item]) -> Counter | None:
    """装备栏少了哪些装备(按名字);有认不出的格子时没法比,返回 None。"""
    if any(i.name is None for i in before + after):
        return None
    return Counter(i.name for i in before) - Counter(i.name for i in after)


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
