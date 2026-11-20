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

from ..brain.screen_agent import ROLL_MAX, ROLL_REFRESHES, Action, action_text, slot_text
from .. import paths
from ..device import UserInterrupt
from ..vision.board import SHOP_X, Slot, occupied_slots, slot_changed, slots
from ..vision.cards import FIXED_UNITS
from ..vision.boxes import CLOSE_POINT, read_panel
from ..vision.equipment import (
    EquipmentReader,
    Item,
    cancel_point,
    find_compose_confirm,
    format_items,
    privilege_panel,
)
from ..vision.hud import read_gold, read_level
from ..vision.motion import frame_diff
from ..vision.ocr import Ocr, TextBox, find
from ..vision.shop import format_shop, read_prices, read_shop, reread_names, shop_state, two_star

SHOP_BUTTON = (0.845, 0.909)      # 右下「商店」/「收起」(同一个位置,1920x1080 实测)
BOARD_BLANK = (0.500, 0.550)      # 棋盘中间空白:点这里也能关掉商店(trail-cli 的 SHOP_CLOSE_POINT),按钮点不动时用它
READY_BUTTON = (0.946, 0.694)     # 「出战」,商店收起时
REFRESH_REGION = (0.78, 0.40, 0.12, 0.10)          # 商店右侧的「刷新」按钮(2026-11-16 实机约 0.842, 0.451)
REFRESH_PRICE_REGION = (0.80, 0.455, 0.10, 0.075)  # 「刷新」下面那个价格(约 0.852, 0.492)
NO_MONEY = r"金币不足|不足|余额不足"
SHOP_CARD_Y = 0.15
# 出战和别的操作一起发时的提醒:只在当轮执行结果里给,不记进本局经历(下一轮单独出战后经历里有「出战」)
READY_DEFERRED = "出战:这一轮没有出战。"
# 程序执行后能核对结果的操作:同一批里只有这些、而且都成功了,ready 就接着出战(报告 P1-6,维护者 2026-11-19 同意)
CHECKED = {"buy", "sell", "move", "buy_exp", "compose", "equip", "take"}
ITEM_PREFIX = "道具"      # 备战席里带「开启」的武装箱等道具(loop.perceive 标出来的),不是角色
REFUSED = "不可调整|无法上阵|无法移动"   # 拖动被游戏拒绝时界面中间的提示(2026-11-15 实机:「当前情况下不可调整」)
BEHIND_SHOP = {"选择主角", "出战"}   # 右下的按钮,商店展开时被挡住(2026-11-16 外部 agent 实测:点了没反应)
UNDERSTAFFED = "人数未达上限"    # 出战时的「可出战角色人数未达上限,是否确认出战?」(实机文字在 0.498, 0.473)
UNDERSTAFFED_CANCEL = (0.43, 0.572)     # 读不到「取消」时的位置(trail-cli 的 BATTLE_TEAM_COUNT_CANCEL_POINT)
UNDERSTAFFED_CONFIRM = (0.57, 0.572)    # 读不到「确认」时的位置(和取消左右对称,待实机核对)
# 操作后看回执(2026-11-20,参考 M7A 的 ACTION_POLL):每隔 POLL 秒截一张看预期的变化出现没有,出现就走;
# 原来是固定睡 0.5~1.2 秒、再等画面静止(至少两张截图隔 0.3 秒)、再整屏识别,一个上场要 2 秒多、卖一个 4 秒多。
# 回执一直不来时最多看这么多眼,之后照原来的方式核对和报告
POLL = 0.15
SHOP_TRIES = 12
BUY_TRIES = 8
MOVE_TRIES = 12
SELL_TRIES = 8
EQUIP_TRIES = 12
REFRESH_TRIES = 12
READY_TRIES = 8
STILL = 3.0      # 两眼之间画面平均差低于这个算静止(和等画面静止的标准一样)
PREP_BANNER = (0.0, 0.0, 0.45, 0.12)   # 左上角「备战阶段」
# 拖完把指针停到客户区左上角(M7A v2026.11.20 的 POINTER_REST,1920x1080 下 12 像素处,离屏幕角很远,不会触发急停):
# 指针停在卡上卡面会抬起,换位后核对目标格、读卡都会受影响
REST = (0.006, 0.011)


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
    timings: list[list] = field(default_factory=list)   # 每个操作的用时 [类型, 秒]

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
                 before_ready: Callable[[], str | None] | None = None, capture: Callable | None = None,
                 open_crystals: Callable[[], str] | None = None):
        self.device = device
        self.ocr = ocr
        self.equipment = equipment
        self.raw = raw            # 坐标类操作(click / drag / key ……)交回 Runner 执行
        self.before_ready = before_ready   # 点出战前(商店已收起)先做的事;返回说明时不出战,让模型先看新局面
        self.open_crystals = open_crystals
        self.sleep = sleep or (lambda s: time.sleep(s))
        self.capture = capture or (lambda: self.device.grab())
        self.shop = False         # 商店是否展开:看得出来时以画面为准,被遮住时沿用上次的判断
        self.frame = None         # 最近一次截图
        self.counts: dict[str, int] | None = None   # 前台、后台的格子数(Runner 识别阵容时更新)
        self.deployed: str | None = None            # 「已上场/上限」,如 5/6(Runner 读 HUD 时更新)
        self.node: str | None = None                # 当前节点(Runner 每步更新)
        self.understaffed: set[str] = set()         # 已经因为「人数未达上限」拦过一次出战的节点
        self.roster: Callable[[], dict[str, set[int]]] | None = None   # 角色名 → 费用(Runner 给;roll 认牌、算二星)

    def _look(self) -> list[TextBox]:
        """等画面静止后整屏识别:结果说不准的操作(模型按坐标点的、等待)之后用。"""
        self.frame = self.capture()
        return self.ocr(self.frame.image)

    def _park(self) -> None:
        move = getattr(self.device, "move", None)
        if move is not None:
            move(*REST)

    def _grab(self):
        """直接截一张,不等画面静止(看回执时靠多看几眼,而不是先等动画放完)。"""
        self.frame = self.device.grab()
        return self.frame

    def _glance(self) -> list[TextBox]:
        """直接截一张并整屏识别。"""
        return self.ocr(self._grab().image)

    def _until(self, done: Callable[[list[TextBox] | None], bool], *, tries: int,
               read: bool = True) -> tuple[list[TextBox] | None, bool]:
        """操作之后每隔 POLL 秒看一眼,done 为真就返回(这一眼的界面文字, True);看满 tries 眼还没等到,
        返回(最后一眼, False),由调用方照原来的方式核对。read=False 时不做文字识别(只看像素,
        done 收到 None,用 self.frame 判断),一眼只要几毫秒。"""
        boxes = None
        for _ in range(tries):
            self.sleep(POLL)
            if read:
                boxes = self._glance()
            else:
                self._grab()
            if done(boxes):
                return boxes, True
        return boxes, False

    def shop_now(self, boxes: list[TextBox] | None) -> bool:
        if boxes is None:
            return self.shop          # 只看了像素、没识别文字:沿用上次的判断
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
            return read_level(self._grab().image, self.ocr)
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

    def _wait_ready(self) -> list[TextBox]:
        """点了出战以后:弹出「人数未达上限」,或者左上角的「备战阶段」连续两眼都不见了(进了战斗或别的界面)就返回。
        原来固定等 1 秒再等画面静止;弹窗出来时背景变暗,只看一眼「备战阶段」没了不算数。"""
        gone = 0

        def done(boxes) -> bool:
            nonlocal gone
            if find(boxes, UNDERSTAFFED) is not None:
                return True
            gone = gone + 1 if find(boxes, "备战阶段", region=PREP_BANNER) is None else 0
            return gone >= 2

        boxes, _ = self._until(done, tries=READY_TRIES)
        return boxes

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
            last, prev = None, None
            for i in range(SHOP_TRIES):
                self.sleep(POLL)
                boxes = self._glance()
                image, prev = prev, self.frame.image
                state = shop_state(boxes)
                if state is None:
                    continue                     # 一时看不出开合(动画中),再看一眼
                if state is not want_open:
                    if i >= 4:
                        break                    # 点出了反效果(比如收起时点到了商店),别在同一个点上反复等
                    continue                     # 刚点完画面还没动,前几眼看到的还是原样
                cards = read_shop(boxes)
                # 展开要等卡片读稳:连续两眼一样;五格全空(买空了或卡片还没滑进来)时还要画面静止
                if not want_open or (cards == last and (any(n for _, n, _ in cards)
                                                         or image is not None and frame_diff(image, prev) < STILL)):
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
        gold = self._gold()
        button = find(boxes, "^刷新$", region=REFRESH_REGION)
        if button is not None:
            self._click(out, float(button.x), float(button.y))
        else:
            self.device.key("d")          # 认不出按钮时退回快捷键
        boxes = self._wait_refresh(before, gold)
        after = read_shop(boxes)
        if after != before:
            return f"{text}(停下来看新商店)"
        hint = find(boxes, NO_MONEY)
        price = find(boxes, r"^\d{1,2}$", region=REFRESH_PRICE_REGION)
        why = (f"界面提示「{hint.text}」" if hint is not None
               else f"刷新价 {price.text} 金币,可能不够" if price is not None else "可能金币不足")
        return (f"刷新没有生效:金币和商店都没变(还是 {format_shop(after)})——{why}。"
                "不要反复点刷新,先看现有卡片怎么用")

    def _wait_refresh(self, before: list, gold: int | None) -> list[TextBox]:
        """点了刷新以后看到新的五张卡读稳(连续两眼一样)为止;商店被收起、提示金币不足也停。
        五张碰巧和刷新前一样时看金币有没有扣。原来固定等 1 秒再等画面静止。"""
        last = None

        def done(boxes) -> bool:
            nonlocal last
            if shop_state(boxes) is False or find(boxes, NO_MONEY) is not None:
                return True
            cards, prev = read_shop(boxes), last
            last = cards
            now = self._gold()
            changed = cards != before or (gold is not None and now is not None and now < gold)
            return changed and cards == prev and any(n for _, n, _ in cards)

        boxes, _ = self._until(done, tries=REFRESH_TRIES)
        return boxes

    def _buy_slot(self, out: PrepareResult, slot: int, boxes: list[TextBox]) -> tuple[str | None, list[TextBox]]:
        """点商店第 slot 格买卡并核对,返回(没买成的原因或 None, 最新的界面文字)。boxes 要是眼前这一帧(金币按它读)。

        商店还开着就看这一格的名字变没变;点完商店被收起时名字读不到,改看金币有没有扣——2026-11-19 那局
        1-6、1-7、2-2 三次点卡后商店收起、金币没变,原来按「名字读不到」算成买到了,模型下一轮看金币才发现。
        收起了又没扣钱,就重新打开商店,这一格还是同一张卡时再点一次。"""
        name = read_shop(boxes)[slot - 1][1]
        for attempt in range(2):
            gold = self._gold()
            self._click(out, SHOP_X[slot - 1], SHOP_CARD_Y)

            def done(b) -> bool:
                # 回执:这一格换了(买走了)、商店被收起、金币变了,或者界面提示买不了
                if shop_state(b) is False or find(b, NO_MONEY) is not None or find(b, "备战席已满") is not None:
                    return True
                if name:
                    return read_shop(b)[slot - 1][1] != name
                now = self._gold()
                return gold is not None and now is not None and now != gold

            boxes, _ = self._until(done, tries=BUY_TRIES)
            now = self._gold()
            paid = None if gold is None or now is None else now < gold
            if shop_state(boxes) is not False:
                if name:
                    changed = read_shop(boxes)[slot - 1][1] != name
                    return (None if changed else "买不进,可能金币不够或备战席满了"), boxes
                if paid is False:
                    return "这一格没认出角色,点了以后金币没变,没买到", boxes
                return None, boxes                     # 认不出名字时按金币核对,金币也读不出就不下结论
            if paid:
                return None, boxes                     # 买到了,只是商店跟着收起了
            if attempt:
                break
            boxes = self.set_shop(out, boxes, True)
            if not self.shop or read_shop(boxes)[slot - 1][1] != name:
                break
        return "点卡以后商店被收起了,金币没扣,没买到", boxes

    # ---------------------------------------------------------------- D 牌

    def _gold(self) -> int | None:
        try:
            return read_gold(self.frame.image, self.ocr) if self.frame is not None else None
        except Exception:  # noqa: BLE001 - 读不出金币就按花掉的算
            return None

    def _shop_cards(self, boxes: list[TextBox]) -> list[tuple[int, str | None, int | None]]:
        """商店五张卡(名字、价格):名字认不出时重读,价格漏检时按金币图标补读,和交给模型的商店一样。"""
        cards = read_shop(boxes)
        known = (set(self.roster()) | {"开拓者"}) if self.roster else set()
        if self.frame is not None and known and any(n and n not in known for _, n, _ in cards):
            cards = reread_names(self.frame.image, cards, self.ocr, known)
        if self.frame is not None and any(n and p is None for _, n, p in cards):
            prices = read_prices(self.frame.image, self.ocr)
            cards = [(i, n, p if p is not None else (prices.get(i) if n else None)) for i, n, p in cards]
        return cards

    def _roll(self, out: PrepareResult, a: Action, boxes: list[TextBox],
              board: list[Slot] | None) -> tuple[str, list[TextBox]]:
        """D 牌(维护者 2026-11-18 要求:极端时要刷十几二十次,一次刷新一轮决策不合理):反复「买下商店里的目标 → 刷新」,
        目标达成、到刷新上限、再花钱就低于保底金币、买不进、刷新没生效都会停。场上 + 备战席已有的按一星的量算
        (二星 3 张、三星 9 张),商店里按价格判断出的二星卡算 3 张。"""
        text = action_text(a)
        boxes = self.set_shop(out, boxes, True)
        if not self.shop:
            return f"{text}:商店没有打开", boxes
        boxes = self._glance()               # 商店本来就开着时没有重新截图,金币要按眼前这一帧读
        costs = self.roster() if self.roster else {}
        have: Counter = Counter()
        for slot in board or []:
            if slot.name:
                have[slot.name] += 3 ** (max(slot.stars, 1) - 1)
        targets = {t.name: t for t in a.targets or []}
        bought: Counter = Counter()

        def done(name: str) -> bool:
            t = targets[name]
            return bought[name] >= t.count if t.count else have[name] >= 3 ** (t.stars - 1)

        def finished() -> bool:
            states = [done(n) for n in targets]
            return any(states) if a.until == "any" else all(states)

        keep = a.keep_gold or 0
        limit = min(a.max_refresh or ROLL_REFRESHES, ROLL_MAX)
        gold = self._gold()
        refreshes = spent = 0
        reason = ""
        while not reason:
            for slot, name, price in self._shop_cards(boxes):
                if finished():
                    break
                if name not in targets or done(name):
                    continue
                if gold is not None and price is not None and gold - price < keep:
                    reason = f"买{name}要 {price} 金,买了就低于保底 {keep} 金"
                    break
                why, boxes = self._buy_slot(out, slot, boxes)
                if why:
                    reason = f"{name}{why}"
                    break
                bought[name] += 1
                have[name] += 3 if two_star(name, price, costs) else 1
                spent += price or 0
                now = self._gold()
                gold = now if now is not None else (gold - (price or 0) if gold is not None else None)
            if reason:
                break
            if finished():
                reason = "目标达成"
                break
            if refreshes >= limit:
                reason = f"刷新到了 {limit} 次的上限"
                break
            free = find(boxes, "免费刷新", region=REFRESH_REGION) is not None
            price_box = find(boxes, r"^\d{1,2}$", region=REFRESH_PRICE_REGION)
            cost = 0 if free else int(price_box.text) if price_box is not None else 2
            if gold is not None and gold - cost < keep:
                reason = f"再刷新(要 {cost} 金)就低于保底 {keep} 金"
                break
            before = read_shop(boxes)
            button = find(boxes, "刷新$", region=REFRESH_REGION)
            if button is not None:
                self._click(out, float(button.x), float(button.y))
            else:
                self.device.key("d")
            boxes = self._wait_refresh(before, gold)
            if shop_state(boxes) is False:
                reason = "商店被收起了(界面可能变了)"
                break
            now = self._gold()
            paid = now is not None and gold is not None and now < gold   # 五张碰巧一样时看金币有没有扣
            if read_shop(boxes) == before and not paid:
                hint = find(boxes, NO_MONEY)
                reason = "刷新没有生效" + (f"(界面提示「{hint.text}」)" if hint is not None else "(可能金币不够)")
                break
            refreshes += 1
            spent += cost
            gold = now if now is not None else (gold - cost if gold is not None else None)
        got = "、".join(f"{n}×{c}" for n, c in bought.items()) or "没买到目标"
        status = "、".join(f"{n} 买了 {bought[n]}/{t.count} 张" if t.count else f"{n} 现有一星的量 {have[n]} 张"
                          for n, t in targets.items())
        return (f"{text}:刷新 {refreshes} 次,花了 {spent} 金,买到 {got};停下:{reason};"
                f"现在金币 {gold if gold is not None else '读不出'};{status}"), boxes

    def _drag_slot(self, out: PrepareResult, src: tuple[str, int], dst: tuple[str, int],
                   boxes: list[TextBox]) -> tuple[str | None, list[TextBox]]:
        """把 src 格的角色拖到 dst 格,再看一眼:被游戏拒绝(界面中间的提示)或目标格还是空的就算没成功。
        商店开着和收着时棋盘位置不同,而商店会被别的操作带着开合(实机见过买完卡后商店收起),所以每次按最新画面换算坐标,
        没成功再试一次。返回(失败原因或 None, 最新的界面文字)。"""
        for _ in range(2):
            shop = self.shop_now(boxes)
            layout = {(s.zone, s.index): s for s in slots(shop, self.counts)}
            p, q = layout[src], layout[dst]
            before = self._grab().image
            was = occupied_slots(before, shop, self.counts)
            self.device.drag(p.x, p.y, q.x, q.y, duration=0.6)
            self._park()
            out.marks.append({"type": "drag", "x": p.x, "y": p.y, "x2": q.x, "y2": q.y})

            def landed(_) -> bool:
                # 回执只看像素(几毫秒一眼):目标格原来空着、现在有卡,或者原来有卡、现在卡面换了(换位)
                filled = occupied_slots(self.frame.image, shop, self.counts)
                if filled is None or was is None:
                    return False
                return dst in filled and (dst not in was or bool(slot_changed(before, self.frame.image, dst, shop,
                                                                                self.counts)))

            _, ok = self._until(landed, tries=MOVE_TRIES, read=False)
            if ok:
                return None, None          # 界面文字这一步没读,后面要用时再看
            boxes = self._glance()
            refused = find(boxes, REFUSED)
            if refused is not None:
                return f"界面提示「{refused.text}」", boxes
            filled = occupied_slots(self.frame.image, self.shop_now(boxes), self.counts)
            if filled is None or dst in filled:
                return None, boxes
        return f"拖完以后{slot_text(dst[0], dst[1] + 1)}还是空的", boxes

    def run(self, actions: list[Action], boxes: list[TextBox], board: list[Slot] | None, *,
            final: bool = False) -> PrepareResult:
        """final:本备战阶段最后一次决策。

        ready 和别的操作一起发时:前面全是程序能核对的操作(CHECKED)而且都成功了,就接着出战;有操作没成功,
        或者有点击、拖拽这类程序核对不了的操作,就先不出战,把实际局面交给模型(最后一次决策例外,前面都成功就出战)。
        原来一律「出战单独一轮」(2026-11-16 起:上场被游戏拒绝时程序照样报成功,模型按想象中的阵容出战),
        现在上场、买卖、装备都有核对,2026-11-19 那局单独出战的 21 轮大多可以省掉。"""
        out = PrepareResult()
        others = any(x.type not in ("wait", "ready") for x in actions)
        unchecked = any(x.type not in CHECKED | {"wait", "ready"} for x in actions)
        failed = False
        # 各格子里有没有角色,随移动、买卖更新;认不出阵容时为 None,不做检查
        occupied: dict[tuple[str, int], str] | None = None
        if board is not None:
            occupied = {(s.zone, s.index): (s.name or "认不出的角色") for s in board if not s.empty}
        self.shop_now(boxes)
        given = boxes             # 模型决策时的那一帧;执行中还拿着它的话,说明画面已经旧了
        pending = [a.model_copy() for a in actions]
        while pending:
            a = pending.pop(0)
            text = action_text(a)
            started = time.monotonic()
            try:
                if a.type == "buy":
                    if boxes is given or boxes is None:
                        boxes = self._glance()   # 模型决策要十几秒,这期间商店可能已经收起,按眼前的画面买
                    boxes = self.set_shop(out, boxes, True)
                    if not self.shop:
                        out.results.append(f"{text}:商店没有打开")
                        out.observe = True
                        break
                    name = read_shop(boxes)[a.slot - 1][1]
                    why, boxes = self._buy_slot(out, a.slot, boxes)
                    if why:
                        out.results.append(f"{text}" + (f"({name})" if name else "") + f":{why}")
                        failed = True
                        continue
                    if name is None:
                        out.results.append(f"{text}:这一格没认出角色(可能已经买走了)")
                        failed = True
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
                        failed = True
                        continue
                    if occupied is not None and occupied[src] in FIXED_UNITS and a.type in ("move", "sell"):
                        out.results.append(f"{text}:{slot_text(a.zone, a.index)}是{occupied[src]},固定在后台,不能拖动、不能出售")
                        failed = True
                        continue
                    is_item = occupied is not None and occupied[src].startswith(ITEM_PREFIX)
                    if a.type in ("move", "sell") and is_item:
                        out.results.append(f"{text}:{slot_text(a.zone, a.index)}是道具,不是角色,不能上场或出售")
                        failed = True
                        continue
                    if a.type in ("open", "take") and occupied is not None and not is_item:
                        out.results.append(f"{text}:{slot_text(a.zone, a.index)}不是可开启的道具")
                        failed = True
                        continue
                    layout = {(s.zone, s.index): s for s in slots(self.shop_now(boxes), self.counts)}
                    if src not in layout:
                        out.results.append(f"{text}:没有{slot_text(a.zone, a.index)}这个格子")
                        failed = True
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
                            failed = True
                            continue
                        if occupied is not None and occupied.get(dst) in FIXED_UNITS:
                            out.results.append(f"{text}:{slot_text(a.to_zone, a.to_index)}是{occupied[dst]},不能换位,换一个格子")
                            failed = True
                            continue
                        up = a.zone == "bench" and a.to_zone != "bench"
                        if up and occupied is not None and dst not in occupied and full(self.deployed):
                            out.results.append(f"{text}:上场已满({self.deployed}),先把场上的角色移回备战席或卖掉,"
                                               "或者升级")
                            failed = True
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
                        shop = self.shop_now(boxes)
                        self._click(out, p.x, p.y)
                        boxes, _ = self._until(lambda b: find(b, "^出售") is not None, tries=SELL_TRIES)
                        sell = find(boxes, "^出售")
                        if sell is None:
                            out.results.append(f"{text}:点开角色后没找到「出售」按钮")
                            out.observe = True
                            break
                        self._click(out, sell.x, sell.y)

                        def sold(b, src=src, shop=shop) -> bool:
                            # 回执:弹出确认,或者出售按钮没了、这一格空了
                            if find(b, "^确认$") is not None:
                                return True
                            if find(b, "^出售") is not None:
                                return False
                            filled = occupied_slots(self.frame.image, shop, self.counts)
                            return filled is None or src not in filled

                        boxes, _ = self._until(sold, tries=SELL_TRIES)
                        confirm = find(boxes, "^确认$")
                        if confirm is not None:
                            self._click(out, confirm.x, confirm.y)
                            boxes, _ = self._until(lambda b: find(b, "^确认$") is None, tries=SELL_TRIES)
                        if occupied is not None:
                            gone = occupied.pop(src, None)
                            if gone:
                                out.sold.append(gone)
                        if a.zone != "bench" and self.deployed:
                            self.deployed = shift(self.deployed, -1)
                        out.results.append(text)
                elif a.type == "refresh":
                    # 先确认商店开着:2026-11-18 那局梦境交织的弹窗把商店收了,程序还当开着,点的不是刷新
                    boxes = self.set_shop(out, boxes if boxes is not None else self._glance(), True)
                    if not self.shop:
                        out.results.append(f"{text}:商店没有打开")
                        out.observe = True
                        break
                    out.results.append(self._refresh(out, boxes, text))
                    out.observe = True
                    break
                elif a.type == "roll":
                    result, boxes = self._roll(out, a, boxes if boxes is not None else self._glance(), board)
                    out.results.append(result)
                    out.observe = True      # 买进来的牌和新商店交给模型看,这一轮后面的操作不做
                    break
                elif a.type == "open_crystals":
                    out.results.append(self.open_crystals() if self.open_crystals else "现在开不了晶矿")
                    out.observe = True
                    break
                elif a.type == "buy_exp":
                    before = self._level()
                    for _ in range(a.times or 1):
                        self.device.key("f")
                        self.sleep(0.3)
                    self.sleep(0.8)   # 升级有动画,紧接着点商店会点不上(2026-11-14 实机)
                    out.results.append(text + self._after_level_up(before, self._level()))
                    boxes = None      # 升级后界面变了(上场上限、商店价格),后面要用时重新看
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
                    if others and (failed or (unchecked and not final)):
                        why = ("前面有操作没成功" if failed else
                               "这一批里有程序核对不了的操作(点击、拖拽等)")
                        out.results.append(READY_DEFERRED + f"{why}:先看操作之后的实际局面(上场人数、站位、装备),"
                                           "确认无误再发 ready")
                        out.observe = True
                        break
                    boxes = self.set_shop(out, boxes if boxes is not None else self._glance(), False)
                    note = self.before_ready() if self.before_ready else None
                    if note:
                        out.results.append(note)
                        out.observe = True
                        break
                    if others and self.frame is not None:
                        out.ready_image = self.frame.image
                    self._click(out, *READY_BUTTON)
                    blocked = self.answer_understaffed(out, self._wait_ready())
                    if blocked:
                        out.results.append(f"{text}:{blocked}")
                        out.observe = True
                        break
                    out.results.append(text)
                    out.ready = True
                    break
                else:
                    if boxes is None:
                        boxes = self._glance()
                    if a.type == "click_text" and a.text in BEHIND_SHOP:
                        boxes = self.set_shop(out, boxes, False)   # 商店开着时点不到这些按钮
                    out.results.append(self.raw(a, boxes))
                    if a.type != "wait":
                        boxes = self._look()
                        if privilege_panel(boxes):
                            out.observe = True
                            break
            except UserInterrupt:
                raise
            except Exception as e:
                out.results.append(f"{text}:失败({type(e).__name__}: {e})")
                out.observe = True
                break
            finally:
                out.timings.append([a.type, round(time.monotonic() - started, 2)])
        out.occupied = occupied
        return out

    # ---------------------------------------------------------------- 道具(武装箱等)

    def _open(self, out: PrepareResult, x: float, y: float):
        """点开道具,返回选择面板。点得太快有时弹不出来(M7A 记的游戏 bug),就按住一会儿再点一次。"""
        self._click(out, x, y)
        boxes, _ = self._until(lambda b: read_panel(b) is not None, tries=8)
        panel = read_panel(boxes)
        if panel is None:
            self.device.drag(x, y, x, y, duration=0.25)
            out.marks.append({"type": "click", "x": x, "y": y})
            boxes, _ = self._until(lambda b: read_panel(b) is not None, tries=12)
            panel = read_panel(boxes)
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
        self.sleep(0.5)                            # 选中有动画,确认按钮要等选中生效才点得动
        boxes = self._glance()
        if read_panel(boxes) is not None:          # 还要确认
            ok = find(boxes, "^(确认|确定|选择)$")
            if ok is not None:
                self._click(out, ok.x, ok.y)
                boxes, _ = self._until(lambda b: read_panel(b) is None, tries=10)
        if read_panel(boxes) is not None:
            return f"点了「{name}」但选择面板还在,没拿成"
        return f"拿到 {name}(来自{title})"

    # ---------------------------------------------------------------- 装备

    def _settled_bag(self) -> list[Item]:
        """装备栏读稳了再交出去:连续两眼读到一样的才算(装上、合成后装备栏会重新排一次)。
        原来固定等 1 秒、再等整屏静止后读一次。"""
        last = None
        items: list[Item] = []
        for _ in range(EQUIP_TRIES):
            items = self.equipment.read(self._grab().image)
            names = [(i.index, i.name) for i in items]
            if names == last:
                return items
            last = names
            self.sleep(POLL)
        return items

    def _confirm_compose(self, out: PrepareResult, ok, text: str) -> bool:
        """点合成确认框的 ✓。确认框有弹出动画,动画中点击会落空(2026-11-14 实机);点了还在就再点,
        最后取消,不能把它留在屏幕上。点不下去返回 False(结果里记下原因)。"""
        for _ in range(3):
            self.sleep(0.4)
            ok = find_compose_confirm(self._grab().image) or ok
            self._click(out, *ok)
            _, gone = self._until(lambda _: find_compose_confirm(self.frame.image) is None, tries=8, read=False)
            if gone:
                return True
        self._click(out, *cancel_point(ok))
        self.sleep(0.8)
        out.results.append(f"{text}:合成确认点不下去,已取消")
        out.observe = True
        return False

    def _equipment(self, a: Action, text: str, out: PrepareResult, occupied, boxes) -> int | None:
        """合成或装备。成功返回装备栏里被用掉的编号(后面的装备会往前顺移);失败时记下原因、返回 None 停下来。"""
        if self.equipment is None:
            out.results.append(f"{text}:没有装备识别(知识库还没同步)")
            return None
        if a.item == 0 or (a.type == "compose" and a.onto == 0):
            out.results.append(f"{action_text(a).split(' ')[0]}:这件装备已经在前面的操作里用掉了")
            return None
        before = self.equipment.read(self._grab().image)
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
            self._park()
            out.marks.append({"type": "drag", "x": item.x, "y": item.y, "x2": target.x, "y2": target.y})
            self._until(lambda _: find_compose_confirm(self.frame.image) is not None, tries=EQUIP_TRIES, read=False)
            shot = self.frame.image
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
            if not self._confirm_compose(out, ok, text):
                return None
            after = self._settled_bag()
            if len(after) != len(before) - 1:
                out.results.append(f"{text}:点了确认,但装备栏从 {len(before)} 件变成 {len(after)} 件(合成应该少 1 件),"
                                   f"可能没合成成功,以截图为准;装备栏现在是 {format_items(after, coords=True)}")
                out.observe = True
                return None
            rest = Counter(i.name for i in before if i.index not in (a.item, a.onto))
            new = [i for i in after if (Counter(x.name for x in after) - rest)[i.name] > 0]
            product = f"{new[0].name or '认不出的装备'}(现在是 {new[0].index} 号)" if len(new) == 1 else "认不出的装备"
            out.results.append(f"{text}({item.name or '认不出'}+{target.name or '认不出'}):合成了 {product};"
                               f"装备栏现在是 {format_items(after)}")
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
        self._park()
        out.marks.append({"type": "drag", "x": item.x, "y": item.y, "x2": dst.x, "y2": dst.y})
        # 回执:装备栏少了一件,或者弹出合成确认。
        # 角色身上已有一件简易装备时,再装一件简易装备会合成,也弹「A + B = 成品」要点 ✓(维护者 2026-11-18)
        self._until(lambda _: find_compose_confirm(self.frame.image) is not None
                    or len(self.equipment.read(self.frame.image)) != len(before), tries=EQUIP_TRIES, read=False)
        merged = find_compose_confirm(self.frame.image)
        if merged is not None and not self._confirm_compose(out, merged, text):
            return None
        after = self._settled_bag()
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
        out.results.append(f"{text}:{item.name} 给了 {who or slot_text(a.zone, a.index)}"
                           + (",和身上的简易装备合成了(点了确认)" if merged is not None else ""))
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
