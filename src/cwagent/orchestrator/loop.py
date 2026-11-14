"""主循环:截图(等画面稳定)→ 识别界面 → 规则或模型决定 → 执行 → 记录。设计见 docs/TECH_DESIGN.md 第 10 节。

- 过场、加载、固定界面由规则处理,不调用模型;
- 需要判断的界面按「回合」组织(一个备战阶段或一个选择界面):回合内多轮对话,模型看得到自己之前的判断和
  执行结果;回合结束后只在本局档案里留一行摘要(brain/ledger.py)。
"""

from __future__ import annotations

import itertools
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from ..brain.ledger import RunLedger
from ..knowledge.equipment import EquipmentBook
from ..brain.screen_agent import Action, Decision, Episode, ScreenAgent, action_text, to_jsonable
from .. import paths
from ..device import UserInterrupt
from ..vision.battle import has_marker, read_action_value, read_progress, summarize
from ..vision.board import Portraits, Slot, format_board
from ..vision.hud import read_hud
from ..vision.ocr import Ocr, TextBox, find
from ..vision.shop import format_shop, read_shop
from . import difficulty as difficulty_select
from .macros import open_crystals, survey_plane
from ..vision.boxes import item_slots
from .prepare import PrepareExecutor, PrepareResult
from .screens import (
    DIFFICULTY_HINT,
    PREPARE_LAYOUT,
    SCREEN_NAMES,
    SPOTLIGHT_LAYOUT,
    TEA_BREAK_HINT,
    classify,
    companion_name,
    intro_text,
    meaningful,
    node_of,
    role_of,
    similar,
    text_signature,
)

ACTION_GAP = 0.7      # 每个操作之后等画面响应
STABLE_GAP = 0.3      # 判断画面是否稳定:隔这么久再截一张比较
STABLE_TRIES = 6
STABLE_DIFF = 3.0     # 缩略灰度图逐像素平均差(0~255)低于这个值算稳定
OTHER_TURNS = 4       # 备战以外的回合最多问几轮,超过就重新开一个回合(清掉对话)
STUCK_WARN = 3        # 同一节点第几次开备战回合时提醒模型前面没推进
STUCK_PAUSE = 5       # 同一节点开到第几次备战回合就暂停,请人处理
DIFFICULTY_LLM_STEPS = 8   # 难度交给模型后最多调几步
# 出现这些界面说明之前的回合已经结束
CLOSES = {"battle", "battle_end", "node_result", "settlement", "run_intro", "home", "mode_select", "previous_run"}


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


def frame_diff(a: Image.Image, b: Image.Image) -> float:
    small = [np.asarray(im.convert("L").resize((96, 54)), dtype=np.int16) for im in (a, b)]
    return float(np.abs(small[0] - small[1]).mean())


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
                 difficulty: str = "A8-1", portraits: Portraits | None = None, prepare_turns: int = 3,
                 settle: bool = True, equipment: EquipmentBook | None = None):
        self.device = device
        self.difficulty = difficulty
        self.brain = brain
        self.trace = trace
        self.ocr = ocr or Ocr()
        self.portraits = portraits
        self.on_companion = on_companion
        self.log = log
        self.prepare_turns = prepare_turns
        self.settle_frames = settle
        self.ledger = RunLedger(difficulty=difficulty)
        self.episode: Episode | None = None
        self._ids = itertools.count(1)
        self._results: list[str] = []     # 上一组操作的执行结果,下一轮交给模型
        self.equipment = equipment
        self.equipment_reader = equipment.reader(paths.templates_dir() / "equipment") if equipment else None
        self.prepare = PrepareExecutor(device, self.ocr, raw=self._raw, equipment=self.equipment_reader)
        self._node: str | None = None
        self._board: list[Slot] | None = None
        self._last_screen = ""
        self._last_sig: set[str] = set()
        self._waits = 0
        self._quits = 0
        self.in_run = False  # 本次运行是否已经开局(用来区分上一局遗留的结算界面)
        self._same = 0       # 连续几步在同一界面、画面几乎没变且做的不是等待
        self._marks: list[dict] = []  # 本步实际点击 / 拖拽的位置,给网页在截图上标出来
        self._curve: list[tuple[int | None, int | None]] = []  # 当前这场战斗的(剩余行动值, 进度)采样
        self._curve_node: str | None = None
        self._surveyed: str | None = None                      # 已经收集过节点的位面
        self._crystal_tries: dict[str, int] = {}               # 每个节点开晶矿的次数
        self._crystal_ok = True                                # 划开晶矿没效果两次后就不再尝试
        self._opens: dict[str, int] = {}                       # 每个节点开过几次备战回合
        self._difficulty_tries = 0                             # 程序自动选难度试了几次
        self._difficulty_llm = 0                               # 交给模型后在难度界面走了几步

    # ---------------------------------------------------------------- 截图

    def settle(self):
        """等画面稳定再返回(过场动画、界面切换中途不识别)。一直在动的画面(战斗)最多等 STABLE_TRIES 次。"""
        frame = self.device.grab()
        if not self.settle_frames or self._last_screen == "battle":
            return frame
        for _ in range(STABLE_TRIES):
            time.sleep(STABLE_GAP)
            nxt = self.device.grab()
            if frame_diff(frame.image, nxt.image) < STABLE_DIFF:
                return nxt
            frame = nxt
        return frame

    # ---------------------------------------------------------------- 执行

    def _raw(self, a: Action, boxes: list[TextBox]) -> str:
        """执行一个坐标类操作,返回结果说明。"""
        if a.type == "click_text":
            matches = [b for b in boxes if b.text == a.text] or [b for b in boxes if a.text and a.text in b.text]
            if len(matches) <= a.nth:
                return f"{action_text(a)}:界面上没找到这段文字"
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
        else:
            return f"{action_text(a)}:这里不能用"
        time.sleep(ACTION_GAP)
        return action_text(a)

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
        state: dict = {"hud": hud.as_dict()}
        lines = [hud.text()]
        names: list[str] = []
        shop = self.prepare.shop_now(boxes)
        if shop:
            lines.append("商店(从左到右):" + format_shop(read_shop(boxes)))
        self._board = None
        if self.portraits is not None:
            if shop:
                learned = self.portraits.learn_from_shop(image, boxes)
                if learned:
                    self.log(f"从商店记下了立绘:{'、'.join(learned)}")
            board = self.portraits.read(image, shop_open=shop)
            for s in board:
                # 备战席里带「开启」的是武装箱一类的道具(维护者告知),头像比对会把它认成某个角色
                if s.zone == "bench" and any(b.text == "开启" and abs(b.x - s.x) < 0.035 and abs(b.y - s.y) < 0.065
                                             for b in boxes):
                    s.name, s.stars, s.score = "道具(可开启)", 0, max(s.score, 0.01)
            self._board = board
            state["board"] = [{"zone": b.zone, "index": b.index, "x": round(b.x, 3), "y": round(b.y, 3),
                               "name": b.name, "stars": b.stars, "score": round(b.score, 2)}
                              for b in board if not b.empty]
            lines.append("阵容(格子编号从左到右、从 1 开始;「认不出」表示有角色但没认出是谁):\n" + format_board(board))
            names = sorted({b.name for b in board if b.name and not b.name.startswith("道具")})
        if self.equipment is not None and self.equipment_reader is not None:
            items = self.equipment_reader.read(image)
            state["equipment"] = [{"index": i.index, "name": i.name, "score": round(i.score, 2)} for i in items]
            text, equip_names = self.equipment.describe(items, names, self.ledger.items)
            lines.append(text)
            names = names + equip_names
        if node and not self.ledger.node(node).start:
            self.ledger.node(node).start = hud.text().replace(f"节点 {node},", "")
        return "\n".join(lines), state, names

    def _peek_items(self, n: int, boxes: list[TextBox]) -> None:
        """备战席里没看过的道具(武装箱等)先点开看选项、不选:模型据此决定现在开还是留着凑配方。"""
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

    def _shop_closed(self, boxes: list[TextBox]):
        """打完一关回到备战界面时,游戏会自动打开商店(维护者告知),手柄、拖动等操作会被商店吞掉:
        先等这个自动打开发生完,开着就收起。返回(截图, 文字);收不起来时返回 None。"""
        time.sleep(1.0)
        frame = self.device.grab()
        boxes = self.ocr(frame.image)
        if self.prepare.shop_now(boxes):
            boxes = self.prepare.set_shop(PrepareResult(), boxes, False)
            frame = self.prepare.frame or frame
            if self.prepare.shop_now(boxes):
                self.log("固定流程:商店收不起来,先跳过")
                return None
        return frame, boxes

    def macros(self, image, boxes: list[TextBox], node: str | None) -> str | None:
        """备战界面先做的固定流程(先确保商店收起)。做了什么就返回说明(这一步不再问模型),什么都没做返回 None。"""
        if not node:
            return None
        plane = node.split("-")[0]
        tries = self._crystal_tries.get(node, 0)
        if plane == self._surveyed and not (self._crystal_ok and tries < 2):
            return None   # 没有要做的
        closed = self._shop_closed(boxes)
        if closed is None:
            return None
        image, boxes = closed[0].image, closed[1]
        if plane != self._surveyed:
            self._surveyed = plane
            lines = survey_plane(self.device, self.ocr, self.log)
            if lines:
                self.ledger.plane_nodes = f"位面 {plane}(从左到右):\n" + "\n".join(f"- {t}" for t in lines)
                self.log(f"位面 {plane} 的节点:{' | '.join(lines)[:300]}")
            return "收集本位面的节点序列"
        if self._crystal_ok and tries < 2:
            opened = open_crystals(self.device, image, self.log, ocr=self.ocr, debug=self.trace.root / "debug")
            if opened is None:
                return None
            self._crystal_tries[node] = tries + 1
            if not opened and sum(self._crystal_tries.values()) >= 3:
                self._crystal_ok = False
                self.log("划开晶矿没有效果,本局不再尝试")
            return "划开晶矿" if opened else "尝试划开晶矿(没变化)"
        return None

    def reflex(self, screen: str, image, boxes: list[TextBox], record: dict, sig: set[str]) -> list[Action] | None:
        """不需要模型的界面:返回要做的操作;需要模型判断时返回 None。"""
        if screen != "quit_dialog":
            self._quits = 0
        match screen:
            case "tap_continue":
                return [Action(type="click", x=0.5, y=0.62, reason="点击空白处继续")]
            case "battle_end":
                return [Action(type="click", x=0.5, y=0.83, reason="点击空白加速")]
            case "run_intro":
                name = companion_name(boxes)
                if name and not self.ledger.companion:
                    self.ledger.companion = name
                    if self.on_companion:
                        self.on_companion(name)
                self.ledger.intro = intro_text(boxes)[:600]
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
            case "settlement":
                record["note"] = "对局结束"
                return [Action(type="click_text", text="下一步", reason="离开结算界面")] if find(boxes, "^下一步$") else []
            case "node_result":
                return [Action(type="click_text", text="继续挑战", reason="节点结算")]
            case "deploy_confirm":
                # 模型已经决定出战(没上满人);勾上「本局不再提示」,以后不再弹
                return [Action(type="click_text", text="本局不再提示"), Action(type="click_text", text="确认")]
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
            case "battle":
                av, progress = read_action_value(image, self.ocr), read_progress(boxes)
                self._curve.append((av, progress))
                self._curve_node = record["node"] or self._curve_node
                record["battle"] = {"av": av, "progress": progress}
                return [Action(type="wait", seconds=4, reason="战斗进行中")]
            case "prepare":
                # 进入备战时屏幕中央会播「备战阶段」横幅,动画期间点击无效
                if find(boxes, "^备战阶段$", region=(0.3, 0.35, 0.4, 0.25)):
                    return [Action(type="wait", seconds=1.5, reason="备战阶段开场动画")]
            case "other":
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
        if ep.screen == "prepare" and ep.node:
            rec = self.ledger.node(ep.node)
            if done:   # 同一节点开过多个回合时,保留每次做过的操作(只留最后 300 字)
                rec.actions = (f"{rec.actions} | {done}" if rec.actions else done)[-300:]
            rec.lineup = rec.lineup or self._lineup()
        elif done and ep.screen not in ("difficulty",):
            self.ledger.event(f"{ep.node or ''} {SCREEN_NAMES.get(ep.screen, ep.screen)}:{done}"[:160].strip())

    def _hints(self, screen: str, frame, boxes: list[TextBox], record: dict, opening: bool) -> tuple[str, list[str]]:
        hints, names = "", []
        if screen == "prepare":
            hints = PREPARE_LAYOUT if opening else ""
            if find(boxes, "^幕后茶歇$", region=(0.85, 0.64, 0.15, 0.1)):
                hints += "\n- 这个节点是「幕后茶歇」:右侧按钮是「幕后茶歇」而不是「出战」,ready 会进入茶歇。"
            if find(boxes, "前置流程未结束"):
                hints += "\n- 界面提示「前置流程未结束」:可能还有没做完的流程(例如幕后茶歇里的选择),先回去做完。"
            try:
                seen, record["state"], names = self.perceive(frame.image, boxes, self._node)
                hints += "\n\n## 程序识别的局面\n" + seen
            except Exception as e:  # noqa: BLE001 - 识别出错不影响模型照常决策
                self.log(f"[{record['step']}] 局面识别出错:{type(e).__name__}: {e}")
        elif opening and screen == "difficulty":
            hints = DIFFICULTY_HINT.format(target=self.difficulty)
        elif opening and screen == "spotlight_choice":
            hints = SPOTLIGHT_LAYOUT
        elif screen == "tea_break":
            hints = TEA_BREAK_HINT
        if self._same >= 3:
            hints += (f"\n\n注意:最近 {self._same} 步都在这个界面、画面几乎没变,前面的操作没有起作用。"
                      "先读界面中间的提示文字找原因,换一种做法,不要重复同样的操作。")
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
                if did.startswith("收集") and self.ledger.plane_nodes:
                    record["plane_nodes"] = self.ledger.plane_nodes
                self._last_sig = sig
                self.trace.write(record)
                return screen
            self._peek_items(n, boxes)
            if not self.prepare.shop_now(boxes):
                # 先打开商店,让模型第一眼就看到能买什么
                opened = PrepareResult()
                boxes = self.prepare.set_shop(opened, boxes, True)
                frame = self.prepare.frame or frame
                record["shot"] = self.trace.shot(n, frame.image)
                record["texts"] = [str(b) for b in boxes]
                if not self.prepare.shop:
                    self.log(f"[{n}] 没能打开商店")

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
            hints += (f"\n\n注意:这是节点 {self._node} 第 {opens} 次进入备战回合,前面的回合都没能推进"
                      "(本局档案里有这个节点做过的操作)。先从界面提示文字弄清卡在哪里,换一种做法;实在不行用 pause 请人处理。")
        if opening:
            ep = Episode(id=next(self._ids), key=key, screen=screen, node=self._node, role=role_of(screen),
                         max_turns=self.prepare_turns if screen == "prepare" else OTHER_TURNS)
            self.episode = ep
            text, recalled = self.brain.opening(ep, boxes, ledger=self.ledger.render(), hints=hints, names=names)
        else:
            text, recalled = self.brain.observation(ep, self._results, boxes, screen=screen, hints=hints,
                                                    names=names), []
        decision = self.brain.decide(ep, text, recalled)
        record["decision"] = to_jsonable(decision)
        if decision.error:
            self.log(f"[{n}] 决策失败:{decision.error}")
            time.sleep(3)
            record["actions"] = ["等待 3s(决策失败,稍后重试)"]
            self._last_sig = sig
            self.trace.write(record)
            return screen
        if decision.run_notes:
            self.ledger.set_direction(decision.run_notes)
        self.log(f"[{n}] {screen} | {decision.analysis[:120]}")
        if ep.screen == "prepare":
            return self._finish_prepare(record, decision.actions, boxes, sig, screen)
        return self.finish(record, screen, decision.actions, boxes, sig)

    def _finish_prepare(self, record: dict, actions: list[Action], boxes: list[TextBox], sig: set[str],
                        screen: str) -> str:
        if any(a.type == "pause" for a in actions):
            return self.finish(record, screen, actions, boxes, sig)
        self._marks = []
        result = self.prepare.run(actions, boxes, self._board)
        record["actions"] = result.results
        marks = result.marks + self._marks
        if marks:
            record["marks"] = marks
        if self.episode is not None:
            self.episode.done += [r for r in result.results if not r.startswith("等待")]
            self._results = result.results
        for role, item in result.equipped:
            self.ledger.equip(role, item)
        self.ledger.items.update(result.seen)
        for index in result.taken:
            self.ledger.items.pop(index, None)
        if result.ready:
            if self.episode is not None and self.episode.node and result.lineup():
                self.ledger.node(self.episode.node).lineup = result.lineup()
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

    def step(self, n: int) -> str:
        frame = self.settle()
        boxes = self.ocr(frame.image)
        screen = classify(boxes)
        # 战斗中文字有时只认出零星几个:左侧行动值条目能读出数字就算战斗(备战界面的羁绊图标也是金色六边形,只认图标会误判)
        if screen == "other" and has_marker(frame.image) and read_action_value(frame.image, self.ocr) is not None:
            screen = "battle"
        node = node_of(boxes)
        if node:
            self._node = node
        record: dict = {"step": n, "ts": time.strftime("%H:%M:%S"), "screen": screen, "node": node,
                        "shot": self.trace.shot(n, frame.image), "texts": [str(b) for b in boxes]}
        sig = text_signature(boxes)
        if screen in ("run_intro", "environment_choice", "prepare", "battle"):
            self.in_run = True
        if self._curve and screen in ("prepare", "supply", "spotlight_choice", "settlement", "environment_choice",
                                      "node_result", "strategy_choice", "encounter_choice", "tea_break"):
            summary = summarize(self._curve)
            if self._curve_node:
                self.ledger.node(self._curve_node).battle = summary
            record["battle_summary"] = f"{self._curve_node or ''} {summary}"
            self.log(f"[{n}] 上一场战斗:{record['battle_summary']}")
            self._curve, self._curve_node = [], None
        self._last_screen = screen

        actions = self.reflex(screen, frame.image, boxes, record, sig)
        if actions is not None:
            if screen in CLOSES:
                self.close_episode()
            return self.finish(record, screen, actions, boxes, sig)
        return self.decide(n, frame, boxes, screen, record, sig)

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
