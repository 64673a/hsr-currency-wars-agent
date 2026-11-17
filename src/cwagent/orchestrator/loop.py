"""主循环:截图(等画面稳定)→ 识别界面 → 规则或模型决定 → 执行 → 记录。设计见 docs/TECH_DESIGN.md 第 10 节。

- 过场、加载、固定界面由规则处理,不调用模型;
- 需要判断的界面按「回合」组织(一个备战阶段或一个选择界面):回合内多轮对话,模型看得到自己之前的判断和
  执行结果;回合结束后对话丢弃,在本局经历里留一行(程序记录 + 模型写的回合小结),之后每个回合都能看到
  整局的经历(brain/ledger.py)。
"""

from __future__ import annotations

import itertools
import hashlib
import json
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
from PIL import Image

from ..brain.ledger import MAX_LINE, RunLedger, compact_node, node_types
from ..knowledge.affixes import AffixBook
from ..knowledge.equipment import EquipmentBook
from ..knowledge.guides import Guide, GuideAdvisor, card
from ..brain.screen_agent import Action, Decision, Episode, ScreenAgent, action_text, slot_text, to_jsonable
from .. import paths
from ..device import UserInterrupt
from ..vision.battle import has_marker, read_action_value
from ..vision.board import Portraits, Slot, back_hint, detail_name, format_board
from ..vision.buttons import lit
from ..vision.hud import read_crop, read_gold, read_hud
from ..vision.ocr import Aliased, Ocr, TextBox, find, has
from ..vision.items import catalog as item_catalog
from ..vision.items import describe as describe_items
from ..vision.items import read_items
from ..vision.cards import FIXED_UNITS
from ..vision.supply import describe as describe_supply
from ..vision.supply import read_supply
from ..vision.supply import required as supply_required
from ..vision.shop import format_shop, read_prices, read_shop, trial_slots, two_star
from ..vision.annotate import annotate, legend, save_image
from ..llm.base import ImageInput
from . import difficulty as difficulty_select
from .entry import EntryNavigator
from .battle_watch import BattleWatcher
from .macros import companion_of_bubble, find_orbs, open_crystals, read_companions, survey_plane
from ..vision.boxes import item_slots
from ..vision.equipment import cancel_point, find_compose_confirm
from . import situation
from .prepare import PrepareExecutor, PrepareResult
from .screens import (
    CLOSE_CORNER,
    DIFFICULTY_HINT,
    GREY_CONFIRM,
    OVERLAY_HINTS,
    SCREEN_NAMES,
    SPOTLIGHT_LAYOUT,
    TEA_BREAK_HINT,
    classify,
    companion_banner,
    companion_name,
    investment_cards,
    investment_hint,
    paired_investment,
    PAIRED_INVEST_X,
    PAIRED_INVEST_TITLE_Y,
    trait_shop,
    intro_text,
    meaningful,
    node_of,
    role_of,
    similar,
    text_signature,
)

ACTION_GAP = 0.7      # 每个操作之后等画面响应
STABLE_GAP = 0.6      # 判断画面是否稳定:隔这么久再截一张比较
STABLE_TRIES = 30
STABLE_CONFIRMATIONS = 3
STABLE_DIFF = 3.0     # 缩略灰度图逐像素平均差(0~255)低于这个值算稳定
OTHER_TURNS = 4       # 备战以外的回合最多问几轮,超过就重新开一个回合(清掉对话)
STUCK_WARN = 3        # 同一节点第几次开备战回合时提醒模型前面没推进
STUCK_PAUSE = 5       # 同一节点开到第几次备战回合就暂停,请人处理
DIFFICULTY_LLM_STEPS = 8   # 难度交给模型后最多调几步
ENV_GUIDES = 2             # 投资环境界面每个环境列几篇攻略
READY_SECONDS = 8          # 关键操作前等界面静下来最多等几秒
ENV_EFFECT = 3.0           # 刚进投资环境选择时的特效时长(秒)
INVEST_WAITS = 8           # 投资界面最多等几次(每次 0.5 秒)三张卡都翻出来
DETAIL_PER_NODE = 3        # 每个节点最多点开几张认不出的卡看详情
RAIL_STEPS = 6             # 开拓商店界面停留这么多步还没关,程序直接关掉
# 一两秒后自己消失的过场(维护者 2026-11-16:「阿哈帮大忙了」相当于过场动画),认出就等,不叫模型
TRANSIENT = "阿哈帮大忙了|啊哈帮大忙了"
SPOTLIGHT_BUTTON = "^选择主角$"   # 首领节点备战界面右下的按钮(实机:商店开着时点不到)
SPOTLIGHT_INFO_CLOSE = (0.970, 0.060)   # 「主角光环与奇遇详情」右上角的 X(实机关掉过;M7A 也点这里)
CHOICE_SCREENS = {"supply", "strategy_choice", "environment_choice", "encounter_choice", "spotlight_choice",
                  "ranger", "dance", "garden", "hack", "tea_break", "clara_mod", "mentor", "equipment_reward"}
CRYSTAL_TRIES = 2          # 同一节点开晶矿连续几次没开成就放过这个节点
CENTER = (0.25, 0.12, 0.5, 0.55)                       # 棋盘中间:弹窗、奇遇伙伴动画出现的地方
# 棋盘上本来就有的文字,以及一直挂着、不挡操作的提示
# 「打开商店」「有商品可购买」是棋盘左上角羁绊专属商店(开拓银轨的开拓商店等)的入口,一直挂着(2026-11-15 实机:
# 当成弹窗后每个备战回合都等满 8 秒、跳过了开晶矿和收集节点)
BOARD_TEXT = re.compile(r"^(前台区域|后台区域|备战阶段|试用|打开商店|有商品可购买|\d+(/\d+)*)$|备战席已满")
# 出现这些界面说明之前的回合已经结束
CLOSES = {"battle", "battle_end", "node_result", "settlement", "run_intro", "home", "mode_select", "mode_resume", "previous_run"}


@dataclass
class RunOptions:
    max_steps: int = 600
    max_minutes: float = 120
    approve: Callable[[Decision], bool] | None = None   # 逐步确认(返回 False 则跳过本次操作)
    stop: threading.Event = field(default_factory=threading.Event)
    on_step: Callable[[dict], None] | None = None       # 每一步的记录,给网页实时推送


ITEM_BAR = (0.87, 0.08, 0.22)   # 右上角道具栏:x 下限、y 范围(实机 960 宽截图里两个道具在 x≈0.905、0.95;商店第 5 张卡到 x≈0.885)


GEAR_ITEMS = {350101, 350102, 350103}   # 拆装扳手、精密拆装扳手、冶金炉:用了会取下或重掷角色身上的装备
ZONE_NAMES = {"front": "前台", "back": "后台", "bench": "备战席"}
SURVEY_WAITS = 3   # 新位面第一个备战回合界面静不下来时,最多再等几轮才交给模型
FREE_LOOKS = 3   # 每个回合最多几轮只看不操作(zoom、inspect)不占决策次数
GRACE_TURNS = 1  # 决策次数用完时,最后一轮被程序打断(合成后装备栏重排、操作没成功),再给几轮看结果


def _companion_book() -> list[dict]:
    try:
        from ..knowledge.companions import CompanionBook

        return CompanionBook().all()
    except Exception:  # noqa: BLE001 - 档案读不出就只靠句式认名字
        return []


def _tail(text: str, room: int) -> str:
    """超长时留后面 room 个字,前面用「…」代替。"""
    return text if len(text) <= room else "…" + text[-(room - 1):]


def _plain(o):
    """numpy 的数字(OCR 坐标等)转成普通数字再写 JSON。"""
    return o.item() if hasattr(o, "item") else str(o)


def overlay_texts(boxes: list[TextBox]) -> list[str]:
    """棋盘中间不该有的文字(弹窗、奇遇伙伴动画等):两个以上汉字、又不是区域标题。"""
    x, y, w, h = CENTER
    return [b.text for b in boxes if x <= b.x <= x + w and y <= b.y <= y + h and not BOARD_TEXT.match(b.text)
            and re.search(r"[\u4e00-\u9fff]{2,}", b.text)]


def _is_item(s: Slot, boxes: list[TextBox]) -> bool:
    """备战席里带「开启」的是武装箱一类的道具(维护者告知),头像比对会把它认成某个角色。"""
    return s.zone == "bench" and any(b.text == "开启" and abs(b.x - s.x) < 0.035 and abs(b.y - s.y) < 0.065
                                     for b in boxes)


def frame_diff(a: Image.Image, b: Image.Image) -> float:
    small = [np.asarray(im.convert("L").resize((96, 54)), dtype=np.int16) for im in (a, b)]
    return float(np.abs(small[0] - small[1]).mean())


def frame_motion(a: Image.Image, b: Image.Image) -> float:
    """取九宫格中变化最大的一块,局部动画不会被整屏平均值稀释。"""
    small = [np.asarray(im.convert("L").resize((96, 54)), dtype=np.int16) for im in (a, b)]
    delta = np.abs(small[0] - small[1])
    return float(delta.reshape(3, 18, 3, 32).mean(axis=(1, 3)).max())


class Trace:
    def __init__(self, root: Path):
        self.root = root
        (root / "shots").mkdir(parents=True, exist_ok=True)
        self._f = (root / "steps.jsonl").open("a", encoding="utf-8")
        self._image_paths: dict[str, str] = {}

    def shot(self, step: int, image: Image.Image) -> str:
        name = f"shots/{step:04d}.jpg"
        img = image.convert("RGB")
        img.thumbnail((960, 540))
        img.save(self.root / name, quality=72)
        return name

    def request(self, step: int, system: str, messages) -> str:
        """保存实际请求,图片按内容引用,回放保留消息角色和工具结果。"""
        serialized = []
        for message in messages:
            item = message.model_dump(exclude={"images", "snapshot"})
            item["images"] = []
            for image in message.images:
                digest = hashlib.sha256(image.data.encode("ascii")).hexdigest()[:20]
                if digest not in self._image_paths:
                    self._image_paths[digest] = save_image(self.root, digest + ".jpg", image)
                item["images"].append({**image.model_dump(exclude={"data"}), "path": self._image_paths[digest]})
            serialized.append(item)
        path = self.root / "requests" / f"{step:04d}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"system": system, "messages": serialized}, ensure_ascii=False, default=_plain), encoding="utf-8")
        return path.relative_to(self.root).as_posix()

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
                 on_companion: Callable[[str, str], None] | None = None, log: Callable[[str], None] = print,
                 difficulty: str = "A8-1", portraits: Portraits | None = None, prepare_turns: int = 3,
                 settle: bool = True, equipment: EquipmentBook | None = None, guides: GuideAdvisor | None = None,
                 trailblazer: str = ""):
        self.device = device
        self.difficulty = difficulty
        self.brain = brain
        self.trace = trace
        # 开拓者在商店、补给、角色详情里显示的是玩家自己取的名字,识别出来统一换成「开拓者」
        self.ocr = Aliased(ocr or Ocr(), {trailblazer: "开拓者"}) if trailblazer.strip() else (ocr or Ocr())
        self.portraits = portraits
        self.on_companion = on_companion
        self.log = log
        self.prepare_turns = prepare_turns
        self.settle_frames = settle
        self.ledger = RunLedger(difficulty=difficulty)
        self.episode: Episode | None = None
        self._ids = itertools.count(1)
        self._results: list[str] = []     # 上一组操作的执行结果,下一轮交给模型
        self._vision_marks = {}
        self._zoom_region = None
        self.equipment = equipment
        self.equipment_reader = equipment.reader(paths.templates_dir() / "equipment") if equipment else None
        self.prepare = PrepareExecutor(device, self.ocr, raw=self._raw, equipment=self.equipment_reader,
                                       before_ready=self._crystals_before_ready, capture=self.settle)
        self.guides = guides
        self._guide: Guide | None = None                       # 当前参考的攻略
        self._guide_pinned = False                             # 模型自己选的攻略不再自动换
        self._candidates: list[Guide] = []                     # 上次给模型看的候选攻略(按编号)
        self._shown: tuple = ()                                # 本回合已经给模型看过的候选
        self._roles: list[str] = []                            # 最近一次认出的场上 + 备战席角色
        self._shop_names: list[str] = []                       # 最近一次读到的商店角色
        self._node: str | None = None
        self._board: list[Slot] | None = None
        self._last_screen = ""
        self._last_sig: set[str] = set()
        self._waits = 0
        self._quits = 0
        self.in_run = False  # 本次运行是否已经开局(用来区分上一局遗留的结算界面)
        self.entry = EntryNavigator()
        self._settlement_tries = 0
        self._same = 0       # 连续几步在同一界面、画面几乎没变且做的不是等待
        self._marks: list[dict] = []  # 本步实际点击 / 拖拽的位置,给网页在截图上标出来
        self._stop: threading.Event | None = None              # 网页上的停止(战斗监视里也要随时能停)
        self._surveyed: str | None = None                      # 已经收集过节点的位面
        self._crystal_fails: dict[str, int] = {}               # 每个节点开晶矿连续没开成的次数(到 CRYSTAL_TRIES 就放过这个节点)
        self._crystal_full: tuple[str, int] | None = None      # (节点, 剩几个):备战席满了,晶矿里的角色放不下
        self._ready_checked: str | None = None                 # 出战前已经检查过晶矿的节点
        self._opens: dict[str, int] = {}                       # 每个节点开过几次备战回合
        self._hud_seen: tuple[str | None, int | None] = (None, None)   # (节点, 生命值):商店收起时读到的
        self._details: dict[str, int] = {}                     # 每个节点点开看过几张认不出的卡
        self._costs: dict[str, set[int]] | None = None         # 角色名 → 费用(星神★阿哈有 4、5、7 费三个版本)
        self._rail: dict[str, int] = {}                        # 每个节点在开拓商店界面停了几步
        self._rail_launched: set[str] = set()                  # 已经免费启动过星穹列车的节点
        self._survey_tries: dict[str, int] = {}                # 每个位面试过几次收集节点
        self._companion_tries = 0                              # 读奇遇伙伴试了几次
        self._companion_done = False                           # 本局的奇遇伙伴已经读过(只读一次)
        self._env_waited = False                               # 投资环境选择的开场特效等过了
        self.affixes = AffixBook()                             # 敌人词条库(边点边存)
        self._affixes: list[tuple[str, str]] = []              # 本局读到的敌人词条
        self._difficulty_tries = 0                             # 程序自动选难度试了几次
        self._difficulty_llm = 0                               # 交给模型后在难度界面走了几步
        self._notice = ""                                      # 反射层处理掉的、下一个备战回合要告诉模型的事
        self._spotlight_opened: set[str] = set()               # 程序已经替模型打开过「选择主角」的节点
        self._mentor_until = 0.0                              # 从师光环后的过渡备战画面等待截止时间
        self._facts: situation.RoleFacts | None = None         # 角色 → 羁绊、羁绊档位(局面要点用)
        self._last_hud = None                                  # 最近一次读到的备战数字
        self._items = []                                       # 最近一次认出的道具栏(vision.items.Item)
        self._survey_waits: dict[str, int] = {}                # 节点 → 等界面静下来收集节点等了几轮
        self._macro_detail: dict = {}                          # 固定流程这一步读到的详情(词条、伙伴),写进记录
        self._trial: dict[str, bool] = {}                      # 这局见过的角色 → 是不是试用(卡面标签)
        self._ready_facts = ""                                 # 出战时的客观数字,战斗结束后写进本局经历
        self._checked_planes: set[str] = set()                 # 战略检查点:已经过了开头的位面
        self._checked_boss: set[str] = set()                   # 战略检查点:已经过了的首领节点
        self._losses = 0                                       # 连输几场(节点结算读到「挑战结束」)
        self._checked_losses = False                           # 这一串连输已经算过检查点

    # ---------------------------------------------------------------- 截图

    def settle(self):
        """等画面稳定再返回(过场动画、界面切换中途不识别)。一直在动的画面(战斗)最多等 STABLE_TRIES 次。"""
        frame = self.device.grab()
        if not self.settle_frames:
            return frame
        stable = 0
        for _ in range(STABLE_TRIES):
            if self._stop is not None and self._stop.is_set():
                raise UserInterrupt("已停止")
            time.sleep(STABLE_GAP)
            nxt = self.device.grab()
            stable = stable + 1 if frame_motion(frame.image, nxt.image) < STABLE_DIFF else 0
            if stable >= STABLE_CONFIRMATIONS:
                return nxt
            frame = nxt
        self.log("等待画面稳定达到上限,使用最后一帧")
        return frame

    # ---------------------------------------------------------------- 执行

    def _raw(self, a: Action, boxes: list[TextBox]) -> str:
        """执行一个坐标类操作,返回结果说明。"""
        if a.mark is not None:
            mark = self._vision_marks.get(a.mark)
            if mark is None:
                return f"{action_text(a)}:当前截图没有这个标号,没有执行"
            if a.type == "inspect":
                if a.mode == "click":
                    self.device.click(mark.x, mark.y)
                else:
                    self.device.move(mark.x, mark.y)
                time.sleep(1.0)
                return f"{action_text(a)}:{mark.label},说明见新截图和识别结果"
            a = a.model_copy(update={"x": mark.x, "y": mark.y})
        if a.type == "zoom":
            self._zoom_region = a.region
            return action_text(a)
        if a.type == "click_text":
            matches = [b for b in boxes if b.text == a.text] or [b for b in boxes if a.text and a.text in b.text]
            if len(matches) <= a.nth:
                return f"{action_text(a)}:界面上没找到这段文字"
            b = matches[a.nth]
            self._remember_choice(float(b.x), float(b.y), boxes)
            self.device.click(b.x, b.y)
            self._marks.append({"type": "click", "x": float(b.x), "y": float(b.y)})
        elif a.type == "click":
            chosen = self._remember_choice(a.x, a.y, boxes)
            self.device.click(a.x, a.y)
            self._marks.append({"type": "click", "x": a.x, "y": a.y})
            target = chosen or next((b.text for b in boxes if abs(b.x - a.x) <= max(b.w / 2, 0.012)
                           and abs(b.y - a.y) <= max(b.h / 2, 0.012)), None)
            if target:
                time.sleep(ACTION_GAP)
                return f"点击「{target}」"
        elif a.type == "drag":
            fixed = next((s for s in self._board or [] if s.name in FIXED_UNITS and any(
                abs(s.x - x) < 0.03 and abs(s.y - y) < 0.05 for x, y in ((a.x, a.y), (a.x2, a.y2)))), None)
            if fixed is not None:
                return f"{fixed.name}是后台固定单位,不能拖动或换位,没有执行"
            if self.episode is not None and self.episode.screen == "spotlight_choice" and self._board:
                if abs(a.x2 - 0.5) < 0.04 and abs(a.y2 - 0.407) < 0.06:
                    coords = {"front": ((0.417, 0.474, 0.528, 0.583), 0.574),
                              "back": ((0.370, 0.422, 0.474, 0.528, 0.583, 0.630), 0.700)}
                    for slot in self._board:
                        if slot.zone in coords:
                            xs, y = coords[slot.zone]
                            if slot.index < len(xs) and abs(a.x - xs[slot.index]) < 0.025 and abs(a.y - y) < 0.04:
                                self.episode.spotlight_role = slot.name or "角色未识别"
            self.device.drag(a.x, a.y, a.x2, a.y2, duration=0.8)
            self._marks.append({"type": "drag", "x": a.x, "y": a.y, "x2": a.x2, "y2": a.y2})
            if a.x >= ITEM_BAR[0] and ITEM_BAR[1] <= a.y <= ITEM_BAR[2]:
                time.sleep(ACTION_GAP)
                return self._used_item(a)
        elif a.type == "scroll":
            x, y = (a.x if a.x is not None else 0.5), (a.y if a.y is not None else 0.5)
            self.device.scroll(x, y, a.notches or -3)
            self._marks.append({"type": "scroll", "x": x, "y": y})
        elif a.type == "key":
            self.device.key(a.key)
        elif a.type == "wait":
            time.sleep(min(a.seconds or 3, 60))
        else:
            return f"{action_text(a)}:这里不能用"
        time.sleep(ACTION_GAP)
        return action_text(a)

    def _used_item(self, a: Action) -> str:
        """从右上角道具栏拖出了道具:说清拖的是什么、拖到了谁身上;会动装备的道具让程序记的穿戴作废。"""
        item = next((it for it in self._items if abs(it.x - a.x) < 0.02 and abs(it.y - a.y) < 0.035), None)
        target = None
        for slot in self._board or []:
            if slot.name and not slot.empty and abs(slot.x - a.x2) < 0.03 and abs(slot.y - a.y2) < 0.05:
                target = slot
                break
        what = f"道具「{item.name}」" if item else "道具栏里的道具(没认出是哪个)"
        onto = f"{ZONE_NAMES.get(target.zone, target.zone)}{target.index + 1}号 {target.name}" if target else \
            f"({a.x2:.3f},{a.y2:.3f})"
        text = f"把{what}拖到 {onto}"
        if item is None or item.id in GEAR_ITEMS:
            # 拆装扳手、冶金炉会取下或重掷装备:拖到谁身上就清掉谁的记录,看不出是谁就全部作废
            self.ledger.unequip(target.name if target and item is not None else None)
            text += ";程序记的这名角色的装备已清掉,以截图为准" if target and item is not None else \
                ";程序记的角色装备已全部清掉,以截图为准"
        return text + "(结果看新截图)"

    def _remember_choice(self, x: float, y: float, boxes) -> str | None:
        ep = self.episode
        kind = {"environment_choice": "portals", "strategy_choice": "augments", "spotlight_choice": "spotlights"}.get(ep.screen if ep else "")
        index = getattr(self.brain, "index", None)
        if not kind or index is None or not 0.18 < y < 0.82:
            return None
        under = [b for b in boxes if abs(b.x - x) <= max(b.w / 2, 0.012) and abs(b.y - y) <= max(b.h / 2, 0.012)]
        if any(re.search(r"刷新|确认|返回|放弃|跳过", b.text) for b in under):
            return None   # 卡片下面的「刷新」等按钮:不是选卡(Codex 2026-11-16:点刷新被记成选了「白银投资」)
        if paired_investment(boxes, ep.screen):
            if y >= 0.70:
                return None
            column = min(range(3), key=lambda i: abs(PAIRED_INVEST_X[i] - x))
            columns = (0,) if column == 0 else (1, 2)
            picked = []
            for i in columns:
                names = [b for b in boxes if PAIRED_INVEST_TITLE_Y[0] <= b.y <= PAIRED_INVEST_TITLE_Y[1]
                         and abs(b.x - PAIRED_INVEST_X[i]) < 0.10]
                entities = [e for b in names for e in index.by_name.get(b.text, []) if e.kind == kind]
                if entities:
                    picked.append(entities[0])
            if picked:
                name = "、".join(e.name for e in picked)
                text = "\n".join(f"{e.name}:{e.text}" for e in picked)
                if len(picked) < len(columns):
                    text += "\n右侧另一张策略名称没认出,以截图为准。"
                ep.chosen[kind] = (name, text)
                return name
            return None
        candidates = []
        for box in boxes:
            if not 0.18 < box.y < 0.65 or abs(box.x - x) > 0.16:
                continue
            for entity in index.by_name.get(box.text, []):
                if entity.kind == kind:
                    candidates.append((abs(box.x - x) + abs(box.y - y) * 0.1, entity))
        if candidates:
            entity = min(candidates, key=lambda p: p[0])[1]
            ep.chosen[kind] = (entity.name, entity.text)
            return entity.name
        return None

    def execute(self, actions: list[Action], boxes: list[TextBox]) -> list[str]:
        results = []
        for a in actions:
            try:
                results.append(self._raw(a, boxes))
            except UserInterrupt:
                raise
            except Exception as e:  # noqa: BLE001 - 单个操作失败记录下来,交给下一步重新判断
                results.append(f"{action_text(a)}:失败({e})")
        return results

    # ---------------------------------------------------------------- 局面识别

    def perceive(self, image, boxes: list[TextBox], node: str | None) -> tuple[str, dict, list[str]]:
        """备战界面:读数字、阵容和商店。返回(给模型的文字, 记进记录的结构, 认出的角色名)。"""
        hud = read_hud(image, boxes, self.ocr, node=node)
        # 商店展开时生命值和上场人数被挡住:用商店收起时读到的(上场人数在上下场、出售后由程序跟着改)
        seen_node, seen_hp = self._hud_seen
        if hud.hp is None and seen_node == node:
            hud.hp = seen_hp
        if hud.deployed:
            self.prepare.deployed = hud.deployed
        elif seen_node == node:
            hud.deployed = self.prepare.deployed
        state: dict = {"hud": hud.as_dict()}
        lines = [hud.text()]
        names: list[str] = []
        shop = self.prepare.shop_now(boxes)
        cards: list = []
        if shop:
            cards = read_shop(boxes)
            if any(name and price is None for _, name, price in cards):
                try:
                    prices = read_prices(image, self.ocr)
                except Exception as e:  # noqa: BLE001 - 价格读不出就照旧不给
                    prices = {}
                    self.log(f"读商店价格出错:{type(e).__name__}: {e}")
                cards = [(i, name, price if price is not None else (prices.get(i) if name else None))
                         for i, name, price in cards]
            self._shop_names = [name for _, name, _ in cards if name]
            trial = trial_slots(boxes)
            self._note_roster({name: i in trial for i, name, _ in cards if name})
            lines.append("商店(从左到右;「2星」是价格为费用 3 倍的二星卡;[试用] 是账号里没有、给的试用版):"
                         + format_shop(cards, self._role_costs(), trial))
        self._board = None
        if self.portraits is not None:
            if shop:
                learned = self.portraits.learn_from_shop(image, boxes)
                if learned:
                    self.log(f"从商店记下了立绘:{'、'.join(learned)}")
            board = self.portraits.read(image, shop_open=shop, back_hint=back_hint(hud.level, hud.deployed))
            self.prepare.counts = dict(self.portraits.counts)
            for s in board:
                if _is_item(s, boxes):
                    s.name, s.stars, s.score, s.halo, s.cost = "道具(可开启)", 0, max(s.score, 0.01), False, None
            self._board = board
            state["board"] = [{"zone": b.zone, "index": b.index, "x": round(b.x, 3), "y": round(b.y, 3),
                               "name": b.name, "stars": b.stars, "score": round(b.score, 2), "cost": b.cost,
                               "halo": b.halo, "mark": b.mark}
                              for b in board if not b.empty or b.mark]
            lines.append("阵容(格子编号从左到右、从 1 开始;「认不出」表示有角色但没认出是谁):\n" + format_board(board))
            names = sorted({b.name for b in board if b.name and not b.name.startswith("道具")})
            if ("杨叔" in names and "佩佩" not in names
                    and re.search(r"(后台|固定|占位).{0,20}佩佩|佩佩.{0,20}(后台|固定|占位)", self.ledger.direction)):
                lines.append("当前后台固定单位按头像识别为「杨叔」;笔记中把它称作佩佩的判断与当前识别不一致,请核对并修正笔记。")
            self._roles = names
        try:
            self._items = read_items(image, self.ocr)
        except Exception as e:  # noqa: BLE001 - 道具栏认不出就不给
            self._items = []
            self.log(f"读道具栏出错:{type(e).__name__}: {e}")
        if self._items:
            state["items"] = [{"slot": it.slot, "id": it.id, "name": it.name, "count": it.count, "score": it.score}
                              for it in self._items]
            lines.append(describe_items(self._items))
            for it in self._items:   # 每种道具第一次出现时附上官方说明(回合中途出现的直接写在这里)
                desc = (item_catalog().get(it.id) or {}).get("desc", "").replace("\n", "")
                if desc and self.ledger.add_entry(f"道具:{it.name}", f"- [道具] {it.name}:{desc}") \
                        and self.episode is not None:
                    lines.append(f"(新道具)【{it.name}】{desc}")
        if self.equipment is not None and self.equipment_reader is not None:
            items = self.equipment_reader.read(image)
            state["equipment"] = [{"index": i.index, "name": i.name, "score": round(i.score, 2)} for i in items]
            text, equip_names = self.equipment.describe(items, names, self.ledger.items)
            lines.append(text)
            names = names + equip_names
        try:
            costs = self._role_costs()
            two = {slot for slot, name, price in cards if name and two_star(name, price, costs)}
            facts = situation.render(board=self._board, hud=hud, shop=cards, two_star=two, facts=self._role_facts(),
                                     guide=self._guide, front_slots=(self.prepare.counts or {}).get("front", 4),
                                     trial=self._trial)
            if facts:
                lines.append(facts)
        except Exception as e:  # noqa: BLE001 - 局面要点算错不影响模型照常决策
            self.log(f"局面要点出错:{type(e).__name__}: {e}")
        self._last_hud = hud
        if node and not self.ledger.node(node).start:
            self.ledger.node(node).start = hud.text().replace(f"节点 {node},", "")
        return "\n".join(lines), state, names

    def _role_facts(self) -> situation.RoleFacts:
        if self._facts is None:
            self._facts = situation.RoleFacts.load(paths.knowledge_db())
        return self._facts

    def _role_costs(self) -> dict[str, set[int]]:
        """角色名 → 费用,从知识库读一次(判断商店里的二星卡:价格是费用的 3 倍)。"""
        if self._costs is None:
            self._costs = {}
            db = paths.knowledge_db()
            if db.exists():
                try:
                    with sqlite3.connect(db) as con:
                        for name, summary in con.execute("SELECT name, summary FROM roles"):
                            cost = json.loads(summary).get("cost")
                            if cost:
                                self._costs.setdefault(re.sub(r"[·•・]", "·", name), set()).add(int(cost))
                except (sqlite3.Error, ValueError) as e:
                    self.log(f"读角色费用出错:{e}")
        return self._costs

    def _remember_hud(self, image, boxes: list[TextBox]) -> None:
        """商店收起时读一次生命值和上场人数,商店展开后(被挡住)沿用。"""
        hud = read_hud(image, boxes, self.ocr, node=self._node)
        if hud.hp is not None:
            self._hud_seen = (self._node, hud.hp)
        if hud.deployed:
            self.prepare.deployed = hud.deployed
            self._hud_seen = (self._node, hud.hp if hud.hp is not None else self._hud_seen[1])

    def _identify_unknown(self, n: int) -> tuple | None:
        """头像认不出的角色卡:点开看详情面板里的名字(M7A 的做法),把卡面存成这个角色的头像模板,之后直接认。
        只在商店收起、界面静止时做,每个节点最多 DETAIL_PER_NODE 张。返回最新的(画面, 文字);没做事返回 None。"""
        node = self._node
        if self.portraits is None or not node or self._details.get(node, 0) >= DETAIL_PER_NODE:
            return None
        ready = self.ready_prepare()
        if ready is None:
            return None
        frame, boxes = ready
        board = self.portraits.read(frame.image, shop_open=False)
        unknown = [s for s in board if not s.empty and s.name is None and not s.mark and not _is_item(s, boxes)]
        for s in unknown[:DETAIL_PER_NODE - self._details.get(node, 0)]:
            self._details[node] = self._details.get(node, 0) + 1
            card = self.portraits.card_image(frame.image, s)
            where = slot_text(s.zone, s.index + 1)
            self.device.click(s.x, s.y)
            time.sleep(0.8)
            text = detail_name(self.ocr(self.device.grab().image))
            rid = self.portraits.role_of(text) if text else None
            if rid:
                self.portraits.learn_card(card, rid)
                self.log(f"[{n}] {where}认不出,点开详情是「{self.portraits.names[rid]}」,记下了这张卡面")
            else:
                self.log(f"[{n}] {where}认不出,点开详情也没读到名字" + (f"(读到「{text}」)" if text else ""))
            for _ in range(2):                                  # 关掉详情:点左上角最边上的空白(M7A)
                self.device.click(*CLOSE_CORNER)
                time.sleep(0.6)
                frame = self.device.grab()
                boxes = self.ocr(frame.image)
                if detail_name(boxes) is None:
                    break
            if classify(boxes) != "prepare":
                break
        return frame, boxes

    # ---------------------------------------------------------------- 攻略

    def _set_guide(self, g: Guide) -> None:
        self._guide = g
        self.ledger.guide = card(g)
        self.ledger.add(f"[{self._node or '开局'} 攻略] 参考攻略改为「{g.title}」(主 C {'、'.join(g.carries)})")

    def checkpoint(self) -> str:
        """现在是不是战略检查点(设计文档第 20.5 节),是就返回原因:每个位面的第一个备战回合、首领节点、连输两场以上。
        同一个原因只算一次。攻略候选只在检查点给,平时不再每回合附上、也不自动换方向。"""
        node = self._node or ""
        plane = node.split("-")[0] if node else ""
        if plane and plane not in self._checked_planes:
            self._checked_planes.add(plane)
            return f"位面 {plane} 开始"
        if "boss" in self.ledger.node_kinds.get(node, set()) and node not in self._checked_boss:
            self._checked_boss.add(node)
            return f"{node} 首领节点"
        if self._losses >= 2 and not self._checked_losses:
            self._checked_losses = True
            return f"连输 {self._losses} 场"
        return ""

    def guide_advice(self, opening: bool, record: dict) -> str:
        """攻略方向:还没定方向时按场上、备战席、商店和投资环境定下第一名;之后不自动换(设计文档第 20.5 节),
        只在战略检查点的回合开头列出别的候选和当前方向的匹配情况,由模型决定换不换(guide 字段)。"""
        if self.guides is None:
            return ""
        env = self.ledger.environment or None
        ranked = self.guides.rank(self._roles, self._shop_names, env)
        if not ranked:
            return ""
        if self._guide is None:
            best = ranked[0]
            self._set_guide(best.guide)
            self.log(f"初始方向:攻略「{best.guide.title}」({best.why()})")
        reason = self.checkpoint() if opening else ""
        if not reason:
            return ""
        current = self.guides.score(self._guide, set(self._roles), set(self._shop_names) - set(self._roles), env)
        shop = [m for m in self.guides.shop_carries(self._roles, self._shop_names, env)
                if m.guide.id != self._guide.id and not set(m.guide.carries) & set(self._guide.carries)]
        cands: list = []
        for m in ranked + shop[:2]:
            if m.guide.id != self._guide.id and all(c.guide.id != m.guide.id for c in cands):
                cands.append(m)
        cands = cands[:3]
        self._candidates = [m.guide for m in cands]
        record["guides"] = {"checkpoint": reason, "current": self._guide.title, "score": current.score,
                            "candidates": [{"title": m.guide.title, "score": m.score} for m in cands]}
        lines = [f"战略检查点({reason}):可以重新比较方向;要换就在 guide 填候选编号,并在 run_notes 改写笔记。",
                 f"当前方向「{self._guide.title}」:{current.why()}(匹配分 {current.score})"]
        for i, m in enumerate(cands, start=1):
            lines.append(f"候选 {i}(匹配分 {m.score}):{m.why()}\n{card(m.guide, full=False)}")
        return "\n".join(lines)

    def environment_advice(self, boxes: list[TextBox]) -> str:
        """投资环境界面:每个看得到的环境列出适合它的热门攻略,让模型把环境和开局方向一起考虑(trail-cli 的做法)。"""
        if self.guides is None:
            return ""
        envs = sorted(self.guides.environments, key=len, reverse=True)
        seen: list[str] = []
        for b in sorted(boxes, key=lambda b: b.x):
            env = next((e for e in envs if e in b.text), None)
            if env and env not in seen:
                seen.append(env)
        self._candidates, lines = [], []
        for env in seen:
            for g in sorted((g for g in self.guides.guides if env in g.environments),
                            key=lambda g: g.use_count, reverse=True)[:ENV_GUIDES]:
                self._candidates.append(g)
                lines.append(f"- {env}:候选 {len(self._candidates)}「{g.title}」主 C {'、'.join(g.carries)}"
                             + (f";节奏 {'、'.join(g.tempo)}" if g.tempo else ""))
        if not lines:
            return ""
        return ("各投资环境适合的热门攻略(可以把投资环境和开局方向一起考虑;想定下方向就在 guide 字段填候选编号,"
                "不填就由程序在第一个备战阶段按阵容挑):\n" + "\n".join(lines))

    def choose_guide(self, key: str, n: int) -> None:
        key = key.strip().removeprefix("候选").strip()
        g = (self._candidates[int(key) - 1] if key.isdigit() and 0 < int(key) <= len(self._candidates)
             else self.guides.find(key.strip("「」")) if self.guides else None)
        if g is None:
            self.log(f"[{n}] 找不到模型选的攻略:{key}")
            return
        if self._guide is None or g.id != self._guide.id:
            self.log(f"[{n}] 模型改用攻略「{g.title}」")
            self._set_guide(g)
        self._guide_pinned = True
        self._shown = ()

    def _peek_items(self, n: int, boxes: list[TextBox]) -> None:
        """备战席里没看过的道具(武装箱等)先点开看选项、不选:模型据此决定现在开还是留着凑配方。"""
        here = item_slots(boxes)
        if any(i not in self.ledger.items for i in here):
            ready = self.ready_prepare()
            if ready is None:
                return
            boxes = ready[1]
            here = item_slots(boxes)
        for index in list(self.ledger.items):
            if index not in here:
                self.ledger.items.pop(index)           # 已经开掉或挪走了
        if self.prepare.shop_now(boxes):
            return
        from ..vision.board import slots

        layout = {s.index: s for s in slots(False) if s.zone == "bench"}
        result = PrepareResult()
        for index in here:
            if index in self.ledger.items or index - 1 not in layout:
                continue
            seen = self.prepare.peek(result, layout[index - 1].x, layout[index - 1].y, index)
            if seen:
                self.log(f"[{n}] 备战席{index}号 {seen[0]}:{'、'.join(seen[1])}")
        self.ledger.items.update(result.seen)

    def _lineup(self) -> str:
        if not self._board:
            return ""
        zones = {"front": "前台", "back": "后台"}
        return ";".join(f"{label} " + "、".join(s.name or "?" for s in self._board if s.zone == z and not s.empty)
                        for z, label in zones.items())

    # ---------------------------------------------------------------- 固定流程

    def ready_prepare(self) -> tuple | None:
        """关键操作(收集节点、开晶矿、看道具)前确认界面状态(维护者建议):是备战界面、商店收起、棋盘中间没有弹窗、
        画面静止。打完一关回到备战界面时游戏会自动打开商店,奇遇伙伴也常在开局等时候弹出动画(如「团队规模提升」),
        这时的点击、手柄操作都会被吞掉。最多等 READY_SECONDS 秒;等不到返回 None,交给模型看着处理。"""
        deadline = time.monotonic() + READY_SECONDS
        prev, popup = None, []
        while time.monotonic() < deadline:
            frame = self.device.grab()
            boxes = self.ocr(frame.image)
            if classify(boxes) != "prepare":
                return None
            if self.prepare.shop_now(boxes):
                self.prepare.set_shop(PrepareResult(), boxes, False)
                prev = None
                continue
            confirm = find_compose_confirm(frame.image)
            if confirm is not None:
                self.device.click(*cancel_point(confirm))
                time.sleep(0.8)
                prev = None
                continue
            banner = companion_banner(boxes)          # 奇遇伙伴的横幅盖在备战界面上:先点掉再继续
            if banner is not None:
                self.device.click(*banner)
                time.sleep(0.8)
                prev = None
                continue
            popup = overlay_texts(boxes)
            if not popup and prev is not None and frame_diff(prev, frame.image) < STABLE_DIFF:
                return frame, boxes
            prev = frame.image
            time.sleep(0.6)
        self.log("界面一直没静下来" + (f"(棋盘上有:{'、'.join(popup[:4])})" if popup else "") + ",固定流程先跳过")
        return None

    def macros(self, image, boxes: list[TextBox], node: str | None) -> str | None:
        """备战回合开始先做的固定流程(先确保商店收起):新位面先收集节点序列,再开晶矿(M7A 的时机:
        每个备战回合一开始、买东西之前先开)。做了什么就返回说明(这一步不再问模型),什么都没做返回 None。"""
        if not node:
            return None
        plane = node.split("-")[0]
        full = self._crystal_full is not None and self._crystal_full[0] == node   # 等模型腾出备战席,出战前再开
        pending_companion = plane == "1" and not self._companion_done   # 奇遇伙伴整局只读一次,第一位面读
        spotlight = node not in self._spotlight_opened and find(boxes, SPOTLIGHT_BUTTON) is not None
        if (plane == self._surveyed and (full or self._crystal_fails.get(node, 0) >= CRYSTAL_TRIES)
                and not pending_companion and not spotlight):
            return None   # 没有要做的
        ready = self.ready_prepare()
        if ready is None:
            if plane != self._surveyed and self._survey_waits.get(node, 0) < SURVEY_WAITS:
                # 新位面还没收集节点和词条:先别叫模型(Codex 2026-11-16 那局 1-1 开局弹窗没静下来,
                # 固定流程被跳过,模型在 1-1 决策时没有节点序列和词条,1-2 才补上)
                self._survey_waits[node] = self._survey_waits.get(node, 0) + 1
                time.sleep(1.5)
                return f"等界面静下来再收集本位面的节点(第 {self._survey_waits[node]} 次)"
            return None
        image = ready[0].image
        if plane != self._surveyed:
            # 打不开位面详情(节点图标正在播动画时点击会被吞,2026-11-16 实机)就下个回合再试。
            # survey_plane 内部已经等了两轮、点了两次,所以这里给 2 次机会就够,别把一个位面拖太久
            tries = self._survey_tries[plane] = self._survey_tries.get(plane, 0) + 1
            if tries >= 2:
                self._surveyed = plane
            affixes: list[tuple[str, str]] = []
            lines = survey_plane(self.device, self.ocr, self.log, affixes=affixes, book=self.affixes,
                                 debug=self.trace.root / "debug")
            if lines is not None:
                self._surveyed = plane
            if lines:
                self.ledger.plane_nodes = f"位面 {plane}(从左到右):\n" + "\n".join(f"- {t}" for t in lines)
                self.ledger.node_kinds.update(node_types(lines))
                self.ledger.add_fact(f"位面 {plane} 的节点(从左到右):" + " | ".join(compact_node(t) for t in lines))
                self.log(f"位面 {plane} 的节点:{' | '.join(lines)[:300]}")
            if affixes:
                self._macro_detail["affixes"] = [{"name": n, "desc": d} for n, d in affixes]
                self._refresh_knowledge()
            if affixes and affixes != self._affixes:
                # 有的词条会左右全局方向(如「虚数熄火」不用虚数主 C、「能量逃逸」不用靠能量放大招的主 C,维护者举例)
                self._affixes = affixes
                text = ";".join(f"{n}({d})" if d else n for n, d in affixes)
                self.ledger.add_fact(f"位面 {plane} 的敌人词条:{text}")
                self.log(f"位面 {plane} 的敌人词条:{text[:300]}")
            return "收集本位面的节点序列"
        if pending_companion:
            # 第一位面收集完词条和节点,顺手点左上角读本局的奇遇伙伴(维护者 2026-11-16 指定的时机):
            # 详情页左下角列着本局所有伙伴,悬停读气泡。读不到下个节点再试一次,不影响备战。
            self._read_companions()
            return "读取本局奇遇伙伴"
        did = self._open_crystals(image)
        if did is None and spotlight:
            did = self._open_spotlight(node)
        return did

    def _open_spotlight(self, node: str) -> str | None:
        """首领节点开局先打开「选择主角」(设计文档第 20.5 节:必做的界面操作由程序包办)。原来交给模型自己找,
        外部 agent 花了两三轮才发现按钮被商店挡住。每个节点只开一次:模型从光环界面返回备战后要自己再点。"""
        self._spotlight_opened.add(node)
        boxes = self.ocr(self.device.grab().image)
        if self.prepare.shop_now(boxes):
            boxes = self.prepare.set_shop(PrepareResult(), boxes, False)
        button = find(boxes, SPOTLIGHT_BUTTON)
        if button is None:
            return None
        self.device.click(float(button.x), float(button.y))
        time.sleep(1.0)
        return "打开主角光环选择(首领节点要选完光环才能出战)"

    def _note_roster(self, seen: dict[str, bool]) -> None:
        """记下这局见到的角色是试用还是自己的(商店、补给卡面上的「试用」标签)。一次标过试用就算试用。"""
        for name, trial in seen.items():
            if name and (trial or name not in self._trial):
                self._trial[name] = trial or self._trial.get(name, False)

    def _refresh_knowledge(self) -> None:
        """对局中读到的伙伴、词条刷进知识库(网页知识库马上能看到);失败不影响对局。"""
        try:
            from ..knowledge.sync import refresh_local

            refresh_local()
        except Exception as e:  # noqa: BLE001
            self.log(f"刷新知识库里的伙伴、词条出错:{type(e).__name__}: {e}")

    def _read_companions(self) -> None:
        """点左上角读本局的奇遇伙伴(详情页左下角),记进本局经历。整局一次。"""
        if self._companion_done or self._companion_tries >= 2:
            return
        self._companion_tries += 1
        try:
            lines = read_companions(self.device, self.ocr, self.log, debug=self.trace.root / "debug")
        except Exception as e:  # noqa: BLE001 - 读伙伴出错不耽误备战
            self.log(f"读奇遇伙伴出错:{type(e).__name__}: {e}")
            lines = None
        if lines is None:
            self._companion_done = self._companion_tries >= 2   # 试两次还读不到就放过
            return
        self._companion_done = True
        if lines:
            text = " / ".join(lines)[:500]
            # 标签不写「开局」:这条是第一位面收集完怪物词条和节点之后点左上角读到的,是正式的记录;
            # 开局界面上那条只有名字(见 reflex 的 run_intro)
            self.ledger.add_fact(f"本局奇遇伙伴(详情页读到):{text}")
            self.log(f"本局奇遇伙伴:{text[:300]}")
            known = {self.ledger.companion} | {c.get("name", "") for c in _companion_book()}
            found = []
            for line in lines:      # 名字和效果一起交给档案(本机 companions.local.yaml)
                name, desc = companion_of_bubble(line, known)
                found.append({"name": name or "(没认出名字)", "text": line})
                if name and self.on_companion:
                    self.on_companion(name, desc)
            self._macro_detail["companions"] = found
            self._refresh_knowledge()

    def _open_crystals(self, image) -> str | None:
        """开晶矿,返回结果说明;没有晶矿返回 None。没开成只记在当前节点上,下个节点照常再开
        (原来累计失败 3 次就整局不开,一局里大半晶矿都没开)。"""
        node = self._node or ""
        if self._crystal_fails.get(node, 0) >= CRYSTAL_TRIES:
            return None
        result = open_crystals(self.device, image, self.log, ocr=self.ocr, debug=self.trace.root / "debug")
        if result is None:
            self._crystal_full = None
            return None
        if result.opened:
            self._crystal_fails.pop(node, None)
        elif not result.full:
            self._crystal_fails[node] = self._crystal_fails.get(node, 0) + 1
        self._crystal_full = (node, result.after) if result.full else None
        return result.text()

    def _crystals_before_ready(self) -> str | None:
        """点出战前(商店已收起)再看一次晶矿:回合中途拿到的、开头因为备战席满了没开成的。
        开到了东西就先不出战,让模型看看新局面;每个节点只拦一次,免得来回卡住。"""
        node = self._node
        if not node or self._ready_checked == node:
            return None
        self._ready_checked = node
        try:
            if not find_orbs(self.device.grab().image):
                self._crystal_full = None
                return None
            ready = self.ready_prepare()
            did = self._open_crystals(ready[0].image) if ready else None
        except UserInterrupt:
            raise
        except Exception as e:  # noqa: BLE001 - 开晶矿出错不耽误出战
            self.log(f"出战前开晶矿出错:{type(e).__name__}: {e}")
            return None
        if did is None or did.startswith("尝试"):
            return None
        return f"出战前{did}(先没出战,看看拿到了什么;没有要做的就再 ready)"

    def reflex(self, screen: str, image, boxes: list[TextBox], record: dict, sig: set[str]) -> list[Action] | None:
        """不需要模型的界面:返回要做的操作;需要模型判断时返回 None。"""
        if screen != "settlement":
            self._settlement_tries = 0
        if screen != "quit_dialog":
            self._quits = 0
        match screen:
            case "tap_continue":
                # 提示文字的位置随界面变:奇遇伙伴的横幅提示在 y≈0.655,原来固定的 0.62 点不到,横幅一直挂着,
                # 后面的固定流程就被跳过了(2026-11-16 实机)。按 M7A 的做法点这段文字本身。
                point = companion_banner(boxes)
                if point is not None:
                    return [Action(type="click", x=point[0], y=point[1], reason="跳过奇遇伙伴的介绍横幅")]
                hint = find(boxes, "点击空白处继续")
                if hint is not None:
                    return [Action(type="click", x=float(hint.x), y=float(hint.y), reason="点击空白处继续")]
                return [Action(type="click", x=0.5, y=0.62, reason="点击空白处继续")]
            case "battle_end":
                return [Action(type="click", x=0.5, y=0.83, reason="点击空白加速")]
            case "run_intro":
                name = companion_name(boxes)
                if name and not self.ledger.companion:
                    self.ledger.companion = name
                    self.ledger.add_fact(f"奇遇伙伴 {name}(开局界面,效果见之后读到的那条)")
                    if self.on_companion:
                        self.on_companion(name, "")          # 开局界面只有名字,效果等第一位面读完再补
                        self._refresh_knowledge()
                intro = intro_text(boxes)[:600]
                if intro and intro != self.ledger.intro:
                    self.ledger.intro = intro
                    self.ledger.add_fact("开局界面:" + intro.replace("\n", " "))
                return [Action(type="click_text", text="下一步", reason="开局介绍")]
            case "home":
                return [Action(type="click_text", text="开始「货币战争」")]
            case "difficulty":
                # 程序选最多试 2 次;再不行交给模型,模型也调了 DIFFICULTY_LLM_STEPS 步还没开局就暂停请人处理
                if self._difficulty_tries < 2:
                    self._difficulty_tries += 1
                    if difficulty_select.select(self.device, self.ocr, self.difficulty, self.log):
                        record["note"] = f"难度 {self.difficulty}"
                        return [Action(type="click_text", text="开始对局", reason=f"难度已调到 {self.difficulty}")]
                    self.log("难度没能自动选好" + (",再试一次" if self._difficulty_tries < 2 else ",交给模型"))
                    if self._difficulty_tries < 2:
                        return [Action(type="wait", seconds=0.5, reason="重试选难度")]
                self._difficulty_llm += 1
                if self._difficulty_llm > DIFFICULTY_LLM_STEPS:
                    return [Action(type="pause", reason=f"难度没能调到 {self.difficulty},请手动选好后点开始对局")]
                return None
            case "mode_select":
                return [Action(type="click_text", text="标准博弈"), Action(type="click_text", text="进入标准博弈")]
            case "mode_resume":
                if find(boxes, "^标准博弈$", region=(0.28, 0.10, 0.65, 0.12)) is not None:
                    return [Action(type="click_text", text="继续进度", reason="接回已保存的标准博弈")]
                return [Action(type="pause", reason="已有对局的模式未确认为标准博弈,保留进度")]
            case "settlement":
                record["note"] = "对局结束" if self.in_run else "处理启动前的旧局结算"
                if not self.in_run:
                    self._settlement_tries += 1
                    if self._settlement_tries > 4:
                        return [Action(type="pause", reason="旧局结算多次尝试仍未推进,保留当前页面")]
                if find(boxes, "^前往结算$"):
                    return [Action(type="click_text", text="前往结算", reason="整局失败")]
                if not self.in_run and find(boxes, "对局评价|对局总结") and find(boxes, "^下一步$") is None:
                    return [Action(type="click", x=0.5, y=0.832, reason="离开启动前的旧局结算页")]
                return [Action(type="click_text", text="下一步", reason="离开结算界面")] if find(boxes, "^下一步$") else []
            case "node_result":
                if find(boxes, "挑战成功"):
                    self._losses, self._checked_losses = 0, False
                elif find(boxes, "挑战结束") and sig != self._last_sig:
                    self._losses += 1
                return [Action(type="click_text", text="继续挑战", reason="节点结算")]
            case "deploy_confirm":
                # 出战时「可出战角色人数未达上限」(设计文档第 9 节):这个节点第一次弹就取消、告诉模型;模型看过以后
                # 又出战就照它的决定确认。不勾「本局不再提示」——原来勾上后,之后少上人游戏就不再提醒(最近 6 局每局都发生)
                node = self._node or ""
                if node in self.prepare.understaffed:
                    return [Action(type="click_text", text="确认", reason="看过人数未满的提示后仍决定出战")]
                self.prepare.understaffed.add(node)
                self._notice = ("上次点出战时游戏提示「可出战角色人数未达上限」,已点取消、没有出战。还有空着的上场名额,"
                                "先把角色放上场再出战;确实没有角色可上就再发一次 ready,会照常出战。")
                return [Action(type="click_text", text="取消", reason="人数未满,先交回模型")]
            case "previous_run":
                if find(boxes, "^返回货币战争$"):
                    return [Action(type="click_text", text="返回货币战争", reason="上一局的结算")]
                return [Action(type="click_text", text="下一页", reason="上一局的结算")]
            case "quit_dialog":
                # 误按 Esc 弹出的中断确认:两个按钮都会离开对局,再按一次 Esc 关掉(2026-11-14 记录里这样关掉过)
                self._quits += 1
                if self._quits > 3:
                    return [Action(type="pause", reason="中断确认框关不掉,请人处理")]
                return [Action(type="key", key="escape", reason="关掉中断确认框")]
            case "reconnect":
                ok = find(boxes, "^确认$")
                if ok is not None and lit(image, ok):
                    return [Action(type="click_text", text="确认", reason="断线后重新连接")]
                return [Action(type="wait", seconds=2, reason="等重连提示的确认按钮")]
            case "disconnected":
                return [Action(type="pause", reason="游戏连接中断,请人处理")]
            case "tap_close":
                b = find(boxes, "点击空白处关闭")
                return [Action(type="click", x=float(b.x), y=float(b.y), reason="点击空白处关闭")]
            case "reward_limit" | "empty_front":
                # 助燃券到上限的提示只能确认;「前台区域无角色」确认后回到棋盘重新部署(M7A)
                return [Action(type="click_text", text="确认", reason=SCREEN_NAMES.get(screen, screen))]
            case "spotlight_info":
                # 只是查看(点左上角奇遇伙伴头像打开的);模型以前关它时点错过好几次位置
                if find(boxes, "前往重选") is None:
                    x, y = SPOTLIGHT_INFO_CLOSE
                    return [Action(type="click", x=x, y=y, reason="关掉主角光环与奇遇详情")]
            case "rail_shop":
                node = self._node or ""
                self._rail[node] = self._rail.get(node, 0) + 1
                if self._rail[node] > RAIL_STEPS:
                    x, y = CLOSE_CORNER
                    return [Action(type="click", x=x, y=y, reason="开拓商店停留太久,关掉")]
                launch = find(boxes, "星穹列车")
                if launch is not None and node not in self._rail_launched:
                    # 星穹列车的价格是 0 时免费启动,启动后才有商品目录(M7A)
                    price = re.sub(r"\D", "", read_crop(image, self.ocr, (0.580, 0.330, 0.025, 0.045)))
                    if price == "0":
                        self._rail_launched.add(node)
                        return [Action(type="click", x=float(launch.x), y=float(launch.y),
                                       reason="免费启动星穹列车")]
            case "prepare":
                # 残留的合成确认框会挡住所有操作(实机:确认没点下去,之后开商店、装备、出战全都无效)
                confirm = find_compose_confirm(image)
                if confirm is not None:
                    x, y = cancel_point(confirm)
                    return [Action(type="click", x=x, y=y, reason="清掉残留的合成确认框")]
                # 进入备战时屏幕中央会播「备战阶段」横幅,动画期间点击无效
                if find(boxes, "^备战阶段$", region=(0.3, 0.35, 0.4, 0.25)):
                    return [Action(type="wait", seconds=1.5, reason="备战阶段开场动画")]
            case "other":
                if find(boxes, TRANSIENT):
                    return [Action(type="wait", seconds=1.5, reason="自动消失的过场")]
                entry_actions = self.entry.plan(screen, image, boxes, in_run=self.in_run)
                if entry_actions is not None:
                    record["entry"] = {"page": self.entry.page}
                    return entry_actions
                if len(meaningful(boxes)) < 2:
                    return [Action(type="wait", seconds=1.5, reason="过场或加载中")]
                ep = self.episode
                if ep is not None and ep.waiting and similar(sig, self._last_sig) > 0.6 and self._waits < 10:
                    self._waits += 1
                    return [Action(type="wait", seconds=3, reason="画面与上一步相似,继续等待")]
        return None

    # ---------------------------------------------------------------- 回合

    def _key(self, screen: str, sig: set[str]) -> tuple:
        ep = self.episode
        if screen == "other":
            # 模型操作后弹出的界面(角色详情、确认框……)仍属于原来的回合
            return ep.key if ep is not None and ep.pending else ("other", frozenset(sig))
        return (screen, self._node)

    def close_episode(self) -> None:
        ep, self.episode = self.episode, None
        self._results = []
        if ep is None:
            return
        done = ";".join(ep.done)
        if ep.screen == "environment_choice":
            picked = ep.chosen.get("portals") or self._picked(ep.done, "portals")
            if picked:
                self.ledger.environment = picked[0]
                self.ledger.add_fact(f"投资环境 {picked[0]}:{picked[1]}")
            elif self.guides is not None:
                envs = sorted(self.guides.environments, key=len, reverse=True)
                for d in reversed(ep.done):
                    env = next((e for e in envs if e in d), None)
                    if env:
                        self.ledger.environment = env
                        break
        if ep.screen == "strategy_choice":
            picked = ep.chosen.get("augments") or self._picked(ep.done, "augments")
            if picked:
                self.ledger.add_fact(f"投资策略 {picked[0]}({ep.node or '开局'} 拿到):{picked[1]}")
        if ep.screen == "spotlight_choice":
            picked = ep.chosen.get("spotlights") or self._picked(ep.done, "spotlights")
            if picked:
                if picked[0] == "从师":
                    self._mentor_until = time.monotonic() + 18
                role = f" → {ep.spotlight_role}" if ep.spotlight_role else "(角色未识别)"
                self.ledger.add_fact(f"主角光环 {picked[0]}{role}({ep.node or '节点未知'} 选择):{picked[1]}")
        if ep.screen == "mentor":
            self._mentor_until = 0.0
        if ep.screen == "difficulty":
            return
        # 本局经历里留一行:程序记录的做了什么 + 模型自己写的回合小结(没写就用它最后的判断开头)
        if ep.summary:
            tail = f" | 小结:{ep.summary}"
        elif ep.analysis:
            tail = f" | 判断:{ep.analysis[:150]}" + ("…" if len(ep.analysis) > 150 else "")
        else:
            tail = ""
        # 一条最长 MAX_LINE 字,超出时只截操作流水的前段(完整流水在 steps.jsonl),小结和阵容留全:
        # 原来整条截到 600 字,操作和阵容长的时候末尾的小结整个丢掉(Codex 2026-11-16 复现)
        if ep.screen == "prepare" and ep.node:
            rec = self.ledger.node(ep.node)
            lineup = (rec.lineup or self._lineup())[:200]
            head = f"[{ep.node} 备战] " + (f"进场 {rec.start};" if rec.start and not rec.logged else "")
            rest = (f";阵容:{lineup}" if lineup else "") + tail
            room = max(60, MAX_LINE - len(head) - len(rest) - 4)
            ops = f"操作:{_tail(done, room)}" if done else "没有操作"
            rec.logged, rec.lineup = True, ""
            self.ledger.add(head + ops + rest)
        elif done or tail:
            name = SCREEN_NAMES.get(ep.screen, "其他界面")
            head = f"[{ep.node or '开局'} {name}] "
            room = max(60, MAX_LINE - len(head) - len(tail) - 4)
            self.ledger.add(head + (_tail(done, room) if done else "没有操作") + tail)

    def _picked(self, done: list[str], kind: str) -> tuple[str, str] | None:
        """选择界面最后点中的条目(按「点击「名字」」的文字查知识库):(名字, 官方描述)。点的是坐标就认不出,返回 None。
        投资策略的卡面标题有时只有前半截(「爆晶矿」对「爆晶矿•金」),按前缀也算。"""
        index = getattr(self.brain, "index", None)
        by_name = getattr(index, "by_name", None)
        if not by_name:
            return None
        for d in reversed(done):
            m = re.search(r"点击「(.+?)」", d)
            if not m or m.group(1) in ("确认", "确定", "选择", "刷新", "确认选择"):
                continue
            text = m.group(1)
            for name in [text] + sorted((n for n in by_name if n.startswith(text) or (len(n) >= 2 and n in text)),
                                        key=len, reverse=True):
                hit = next((e for e in by_name.get(name, []) if e.kind == kind), None)
                if hit is not None:
                    return hit.name, hit.text
        return None

    def _hints(self, screen: str, frame, boxes: list[TextBox], record: dict, opening: bool) -> tuple[str, list[str]]:
        hints, names = "", []
        if screen == "prepare":
            hints = ""   # 备战界面的操作说明已经在系统提示词里(整局不变、可缓存),不再每回合开场重发
            if self._notice:
                hints += "\n- " + self._notice
                self._notice = ""
            if find(boxes, "^幕后茶歇$", region=(0.85, 0.64, 0.15, 0.1)):
                hints += "\n- 这个节点是「幕后茶歇」:右侧按钮是「幕后茶歇」而不是「出战」,ready 会进入茶歇。"
            # 出战按钮那个位置会换字(M7A v2026.11.18):ready 点的是同一个位置
            if find(boxes, "^开启宝库$", region=(0.80, 0.60, 0.20, 0.15)):
                hints += "\n- 右侧按钮是「开启宝库」而不是「出战」:这是宝库节点,ready 会开启宝库。"
            skip = find(boxes, r"^跳过\s*[(（]\d+/\d+[)）]$", region=(0.80, 0.60, 0.20, 0.15))
            if skip:
                hints += (f"\n- 右侧按钮是「{skip.text}」而不是「出战」:ready 会用掉一次跳过、直接跳过这场战斗"
                          "(M7A 资料,跳过的结果待实测)。")
            full = self._crystal_full
            if full and full[0] == self._node:
                hints += (f"\n- 晶矿还剩 {full[1]} 个没开:备战席满了,晶矿里的角色放不下。出售或上场角色腾出备战席,"
                          "出战前程序会再开一次。")
            if find(boxes, SPOTLIGHT_BUTTON):
                hints += ("\n- 右下角有「选择主角」:这个首领节点的主角光环还没选,选完才能出战。"
                          "要选时 click_text「选择主角」进入(会先收起商店)。")
            if find(boxes, "前置流程未结束"):
                hints += "\n- 界面提示「前置流程未结束」:可能还有没做完的流程(例如幕后茶歇里的选择),先回去做完。"
            shop_entry = trait_shop(boxes)
            if shop_entry:
                hints += f"\n- {shop_entry}。"
            if find(boxes, "无法上阵同名角色|同名角色无法上阵"):
                hints += "\n- 界面提示「无法上阵同名角色」:场上已经有一个同名角色,刚才那次上场没成功。"
            if find(boxes, "剩余时间", region=(0.1, 0.0, 0.2, 0.12)):
                hints += "\n- 这是限时商店(左上角是剩余时间而不是「备战阶段」):时间到了自动关闭,期间可以买;不能出战。"
            try:
                seen, record["state"], names = self.perceive(frame.image, boxes, self._node)
                hints += "\n\n## 程序识别的局面\n" + seen
            except Exception as e:  # noqa: BLE001 - 识别出错不影响模型照常决策
                self.log(f"[{record['step']}] 局面识别出错:{type(e).__name__}: {e}")
            try:
                advice = self.guide_advice(opening, record)
                if advice:
                    hints += "\n\n## 攻略参考\n" + advice
            except Exception as e:  # noqa: BLE001
                self.log(f"[{record['step']}] 攻略匹配出错:{type(e).__name__}: {e}")
        elif opening and screen == "difficulty":
            hints = DIFFICULTY_HINT.format(target=self.difficulty)
        elif opening and screen == "spotlight_choice":
            hints = SPOTLIGHT_LAYOUT
        elif opening and screen == "environment_choice":
            advice = self.environment_advice(boxes)
            hints = "## 攻略参考\n" + advice if advice else ""
            left = investment_hint(boxes, "environment_choice")
            if left:
                hints = (hints + "\n\n" + left).strip()
        elif screen == "tea_break":
            hints = TEA_BREAK_HINT
        elif screen in OVERLAY_HINTS:
            hints = OVERLAY_HINTS[screen]
        if screen == "supply":
            try:
                packs = read_supply(frame.image, boxes)
                self._note_roster({p.role: p.trial for p in packs if p.role})
                text = describe_supply(packs, supply_required(boxes))
                if text:
                    hints = (hints + "\n\n" + text).strip()
                    record.setdefault("state", {})["supply"] = [
                        {"index": p.index, "role": p.role, "traits": p.traits, "trial": p.trial, "equipment": p.equipment}
                        for p in packs]
                    names = [p.role for p in packs if p.role] + [e for p in packs for e in p.equipment]
            except Exception as e:  # noqa: BLE001 - 认不出就照旧只给标号
                self.log(f"[{record['step']}] 补给识别出错:{type(e).__name__}: {e}")
        if screen == "strategy_choice":
            left = investment_hint(boxes, "strategy_choice")
            if left:
                hints = (hints + "\n\n" + left).strip()
        if opening and screen in CHOICE_SCREENS and screen != "environment_choice" and self._last_hud is not None:
            # 选择界面上看不到金币、生命和阵容,判断时要回翻旧回合(Codex 2026-11-16):附上最近一次备战读到的
            last = f"\n\n## 最近一次备战时读到的(不是这个界面上的)\n{self._last_hud.text()}"
            if self._lineup():
                last += f"\n阵容:{self._lineup()}"
            hints = (hints + last).strip()
        if screen in CHOICE_SCREENS:
            grey = [b.text for b in boxes if b.text in ("确认", "确认选择") and not lit(frame.image, b)]
            if grey:
                hints += "\n" + GREY_CONFIRM.format(text=grey[0])
        if self._same >= 3:
            # 只陈述程序看到的事实,不替模型下结论(设计文档第 20.4 节)
            hints += f"\n\n(程序观察:最近 {self._same} 步都在这个界面,画面几乎没变,前面的操作可能没有生效。)"
        return hints.strip(), names

    def decide(self, n: int, frame, boxes: list[TextBox], screen: str, record: dict, sig: set[str]) -> str:
        key = self._key(screen, sig)
        if self.episode is not None and self.episode.key != key:
            self.close_episode()
        ep = self.episode
        if ep is not None and ep.turns >= ep.max_turns:
            if ep.screen == "prepare" and screen == "prepare":
                self.log(f"[{n}] 本备战阶段的决策次数用完,直接出战")
                return self._finish_prepare(record, [Action(type="ready", reason="决策次数用完")], boxes, sig, screen)
            self.close_episode()
            ep = None

        if ep is None and screen == "prepare":
            try:
                did = self.macros(frame.image, boxes, self._node)
            except UserInterrupt:
                raise
            except Exception as e:  # noqa: BLE001 - 固定流程出错就交给模型
                did = None
                self.log(f"[{n}] 固定流程出错:{type(e).__name__}: {e}")
            if did:
                record["actions"] = [did]
                record.update(self._macro_detail)     # 读到的词条、伙伴,网页这一步显示详情
                self._macro_detail = {}
                if did.startswith("收集") and self.ledger.plane_nodes:
                    record["plane_nodes"] = self.ledger.plane_nodes
                self._last_sig = sig
                self.trace.write(record)
                return screen
            # 固定流程做完了(或这轮没有固定流程),再把角色商店打开给模型看。
            # 原来是在每轮开头就打开、紧接着固定流程又要求收起来,一开一收来回点,画面一直在闪
            # (维护者 2026-11-16 看到的「一直点商店」);现在只有真的要做固定流程时才收一次。
            if not self.prepare.shop_now(boxes):
                self._remember_hud(frame.image, boxes)
                opened = PrepareResult()
                boxes = self.prepare.set_shop(opened, boxes, True)
                frame = self.prepare.frame or frame
                record["shot"] = self.trace.shot(n, frame.image)
                record["texts"] = [str(b) for b in boxes]
                if not self.prepare.shop:
                    self.log(f"[{n}] 没能打开商店")
                now = self.screen_of(frame.image, boxes)
                if now != "prepare":      # 开商店时游戏可能弹出别的界面(投资策略、补给……)
                    record["screen"] = now
                    record["actions"] = [f"打开商店后界面变成了{SCREEN_NAMES.get(now, now)}"]
                    self._last_sig = sig
                    self.trace.write(record)
                    return now
            self._peek_items(n, boxes)
            fresh = self._identify_unknown(n)
            if fresh is not None:
                frame, boxes = fresh
        opening = ep is None
        opens = 0
        if opening and screen == "prepare" and self._node:
            opens = self._opens[self._node] = self._opens.get(self._node, 0) + 1
            if opens >= STUCK_PAUSE:
                self.log(f"[{n}] 节点 {self._node} 已经开了 {opens} 个备战回合还没推进,暂停")
                return self.finish(record, screen, [Action(type="pause", reason=f"节点 {self._node} 反复卡住")],
                                   boxes, sig)
        hints, names = self._hints(screen, frame, boxes, record, opening)
        if opens >= STUCK_WARN:
            hints += (f"\n\n(程序观察:这是节点 {self._node} 第 {opens} 次进入备战回合,前面的回合没有进入战斗;"
                      f"第 {STUCK_PAUSE} 次会暂停请人处理。)")
        if opening:
            ep = Episode(id=next(self._ids), key=key, screen=screen, node=self._node, role=role_of(screen),
                         max_turns=self.prepare_turns + 1 if screen == "prepare" else OTHER_TURNS)   # 多一轮留给单独的出战
            self.episode = ep
            self._set_vision(ep, n, screen, frame.image, boxes, record)
            ep.cache_at = self.ledger.stable_len()    # 整局已知事实 + 本局经历在第一条消息的最前面,只增不改
            # 传档案本身:新出现的条目说明、第一次相关的经验追加进档案,不再每回合重发(设计文档第 20.4 节)
            text, recalled = self.brain.opening(ep, boxes, ledger=self.ledger, hints=hints, names=names,
                                                nodes=self.ledger.kinds_near(self._node))
        else:
            self._set_vision(ep, n, screen, frame.image, boxes, record)
            text, recalled = self.brain.observation(ep, self._results, boxes, screen=screen, hints=hints,
                                                    names=names), []
        decision = self.brain.decide(ep, text, recalled)
        record["decision"] = to_jsonable(decision)
        if ep.request_messages:
            record["request"] = self.trace.request(n, self.brain.system, ep.request_messages)
        if decision.perception_issues:
            record["perception_issues"] = decision.perception_issues
        if decision.error:
            self.log(f"[{n}] 决策失败:{decision.error}")
            time.sleep(3)
            record["actions"] = ["等待 3s(决策失败,稍后重试)"]
            self._last_sig = sig
            self.trace.write(record)
            return screen
        if decision.run_notes:
            self.ledger.set_direction(decision.run_notes)
        if decision.guide:
            self.choose_guide(decision.guide, n)
        for note in decision.notes:   # 本局学到的,之后的回合也要知道
            self.ledger.add(f"[{self._node or '开局'} {note.kind}] {note.title}:{note.content}")
        self.log(f"[{n}] {screen} | {decision.analysis[:120]}")
        if (decision.actions and all(a.type in ("zoom", "inspect") for a in decision.actions)
                and ep.free_looks < FREE_LOOKS):
            # 只看不操作的一轮不占备战决策次数(Codex 2026-11-16:1-1 放大一次、查看两次后就被强制出战)
            ep.free_looks += 1
            ep.max_turns += 1
        if ep.screen == "prepare":
            return self._finish_prepare(record, decision.actions, boxes, sig, screen)
        return self.finish(record, screen, decision.actions, boxes, sig)

    def _set_vision(self, ep: Episode, step: int, screen: str, image: Image.Image, boxes, record) -> None:
        picture, marks = annotate(image, boxes, screen=screen, shop_open=self.prepare.shop_now(boxes),
                                  counts=self.prepare.counts, equipment=(record.get("state") or {}).get("equipment"),
                                  items=self._items if screen == "prepare" else None)
        self._vision_marks = {m.id: m for m in marks}
        ep.images = [picture]
        ep.annotation = legend(marks)
        if self._results and any("查看标号" in r for r in self._results):
            ep.annotation += "\n## 查看说明\n" + "\n".join(b.text for b in boxes)
        record["vision"] = [save_image(self.trace.root, f"{step:04d}-marked.jpg", picture)]
        if self._zoom_region is not None:
            x, y, w, h = self._zoom_region
            crop = image.crop((round(x * image.width), round(y * image.height),
                               round((x + w) * image.width), round((y + h) * image.height)))
            zoom = ImageInput.from_pil(crop, label=f"原分辨率局部图:{self._zoom_region}", max_size=None)
            ep.images.append(zoom)
            record["vision"].append(save_image(self.trace.root, f"{step:04d}-zoom.jpg", zoom))
            ep.annotation += f"\n另附原分辨率局部图,区域(x,y,宽,高)={self._zoom_region};点击坐标仍相对完整客户区。"
            self._zoom_region = None

    def _finish_prepare(self, record: dict, actions: list[Action], boxes: list[TextBox], sig: set[str],
                        screen: str) -> str:
        if any(a.type == "pause" for a in actions):
            return self.finish(record, screen, actions, boxes, sig)
        self._marks = []
        ep = self.episode
        result = self.prepare.run(actions, boxes, self._board, final=ep is not None and ep.turns >= ep.max_turns)
        if (ep is not None and result.observe and not result.ready and ep.turns >= ep.max_turns
                and ep.grace < GRACE_TURNS):
            # 最后一轮被程序打断(合成后装备栏重排、上场没成功):给模型看一眼结果再出战,不直接强制出战
            ep.grace += 1
            ep.max_turns += 1
        record["actions"] = result.results
        marks = result.marks + self._marks
        if marks:
            record["marks"] = marks
        if self.episode is not None:
            self.episode.done += [r for r in result.results if not r.startswith("等待")]
            self._results = result.results
        for role in result.sold:
            self.ledger.unequip(role)
        for role, item in result.equipped:
            self.ledger.equip(role, item)
        self.ledger.items.update(result.seen)
        for index in result.taken:
            self.ledger.items.pop(index, None)
        if result.ready:
            if self.episode is not None and self.episode.node and result.lineup():
                self.ledger.node(self.episode.node).lineup = result.lineup()
            hud = self._last_hud
            if result.ready_image is not None and hud is not None:
                # 最后一轮连着买卖再出战:金币按点出战前那一刻读(Codex 2026-11-16:出战前买了 3 张,记录还是操作前的 14)
                gold = read_gold(result.ready_image, self.ocr)
                if gold is not None:
                    hud = replace(hud, gold=gold)
            self._ready_facts = situation.at_ready(self._board, hud,
                                                   (self.prepare.counts or {}).get("front", 4), self.prepare.deployed)
            self.close_episode()
        self._same = 0
        self._last_sig = sig
        self.trace.write(record)
        return screen

    # ---------------------------------------------------------------- 一步

    def finish(self, record: dict, screen: str, actions: list[Action], boxes: list[TextBox], sig: set[str]) -> str:
        if any(a.type == "pause" for a in actions):
            record["actions"] = [action_text(a) for a in actions]
            self.trace.write(record)
            return "pause"
        waiting = bool(actions) and all(a.type == "wait" for a in actions)
        self._same = self._same + 1 if (not waiting and similar(sig, self._last_sig) > 0.85) else 0
        if not waiting:
            self._waits = 0
        self._marks = []
        results = self.execute(actions, boxes) if actions else []
        record["actions"] = results
        if self._marks:
            record["marks"] = self._marks
        if "decision" in record and self.episode is not None:
            self.episode.done += [r for r in results if not r.startswith("等待")]
            self._results = results
        self._last_sig = sig
        self.trace.write(record)
        return screen

    def screen_of(self, image, boxes: list[TextBox]) -> str:
        screen = classify(boxes)
        # 战斗中文字有时只认出零星几个:左侧行动值条目能读出数字就算战斗(备战界面的羁绊图标也是金色六边形,只认图标会误判)
        if screen == "other" and has_marker(image) and read_action_value(image, self.ocr) is not None:
            screen = "battle"
        return screen

    def watch_battle(self, n: int, record: dict, sig: set[str]) -> str:
        """战斗交给 BattleWatcher 一直看到结束:期间不存截图、不问模型(大招立绘、特效常被认成别的界面而叫醒模型),
        只记行动值、进度、阵亡和敌方机制提示;结束后在本局经历里记一行。记录里只留进入战斗时的那一张截图。"""
        self.close_episode()
        node = record["node"] or self._node
        watcher = BattleWatcher(self.device, self.ocr, classify=lambda im: self.screen_of(im, self.ocr(im)),
                                log=self.log, stop=lambda: self._stop is not None and self._stop.is_set(),
                                debug=self.trace.root / "debug")
        report = watcher.run()
        text = report.text()
        av = [a for a, _ in report.curve if a is not None]
        progress = [p for _, p in report.curve if p is not None]
        # 网页上这一步显示最后的行动值、进度和整场小结
        record["battle"] = {"av": av[-1] if av else None, "progress": progress[-1] if progress else None,
                            **report.as_dict()}
        record["battle_summary"] = text
        if node:
            facts, self._ready_facts = self._ready_facts, ""
            self.ledger.add(f"[{node} 战斗] {text}" + (f";{facts}" if facts else ""))
        self.log(f"[{n}] 战斗{f' {node}' if node else ''}:{text}")
        self._last_sig = sig
        self.trace.write(record)
        return "battle"

    def _mentor_transition(self, screen: str) -> list[Action] | None:
        """从师光环选完后先等后续页面,过渡备战画面不启动固定流程。"""
        ep = self.episode
        if screen in ("prepare", "mentor") and ep is not None and ep.screen == "spotlight_choice":
            picked = ep.chosen.get("spotlights") or self._picked(ep.done, "spotlights")
            if picked and picked[0] == "从师":
                self.close_episode()
        if screen == "mentor":
            self._mentor_until = 0.0
        if screen == "prepare" and self._mentor_until:
            if time.monotonic() < self._mentor_until:
                return [Action(type="wait", seconds=2, reason="从师光环后等待师父选择页")]
            self._mentor_until = 0.0
            self._notice = "选择了「从师」光环,等待后仍未识别出师父选择页;请核对当前截图。"
        return None

    def step(self, n: int) -> str:
        frame = self.settle()
        boxes = self.ocr(frame.image)
        screen = self.screen_of(frame.image, boxes)
        if screen == "environment_choice" and not self._env_waited:
            # 刚进投资环境选择时有约 3 秒的特效(维护者告知),等它过去再识别
            self._env_waited = True
            time.sleep(ENV_EFFECT)
            frame = self.device.grab()
            boxes = self.ocr(frame.image)
        if screen in ("environment_choice", "strategy_choice"):
            for _ in range(INVEST_WAITS):       # 三张卡是依次翻出来的,等都出来再交给模型
                if investment_cards(boxes, screen) >= 3:
                    break
                time.sleep(0.5)
                frame = self.device.grab()
                boxes = self.ocr(frame.image)
        node = node_of(boxes)
        if node:
            self._node = node
            self.prepare.node = node
        record: dict = {"step": n, "ts": time.strftime("%H:%M:%S"), "screen": screen, "node": node,
                        "shot": self.trace.shot(n, frame.image), "texts": [str(b) for b in boxes]}
        sig = text_signature(boxes)
        if screen in ("run_intro", "environment_choice", "prepare", "battle"):
            self.in_run = True
        self._last_screen = screen
        if screen == "battle":
            return self.watch_battle(n, record, sig)

        actions = self._mentor_transition(screen)
        if actions is None:
            actions = self.reflex(screen, frame.image, boxes, record, sig)
        if actions is not None:
            if screen in CLOSES:
                self.close_episode()
            return self.finish(record, screen, actions, boxes, sig)
        return self.decide(n, frame, boxes, screen, record, sig)

    def run(self, opts: RunOptions) -> str:
        started = time.monotonic()
        self._stop = opts.stop
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
                return "stopped" if opts.stop.is_set() else "interrupted"
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
