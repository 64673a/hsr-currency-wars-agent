"""战斗期间的监视:不存截图、不问模型,只高频看三样东西(维护者要求,2026-11-15)。

战斗是自动的,原来每 4 秒截一张图、整屏识别一次:大招立绘和特效会挡住行动值,画面认成「其他界面」时还会叫醒模型;
而角色阵亡的提示只停留约 1 秒,定时截图几乎抓不到。现在进入战斗后由这里接管,直到战斗结束:
- 阵亡:画面中间偏上(首领血条下方)弹出鲑红色横幅「受到致命攻击,战斗倒计时减少!」(维护者截图,2026-11-15)。
  每 0.25 秒按颜色找横幅(只看一小块,很便宜),找到再对横幅那一条做文字识别确认;同一条横幅只算一次。
  同样的横幅也用来报敌方机制(对局截图里见过「守护者之影的责罚降临了!」「【堕神之血•亚婆离】受到攻击,所有其他
  敌方目标行动提前并锁定攻击者景元!」),这些记成战斗事件;红色的大招特效颜色也像,靠文字区分。
- 行动值:左侧行动顺序条里的「⧗N 数字」,每秒读一次;被挡住读不到的跳过。阵亡后 0.6 秒再读一次,看掉了多少。
- 进度:顶部节点号旁的百分比,每 2 秒读一次(只识别顶部那一条)。
战斗结束:行动值条目(金色六边形)连续 2.5 秒看不到,或者每 8 秒一次的整屏识别认出已经不是战斗界面。
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ..vision.battle import has_marker, read_action_value, read_progress, summarize
from ..vision.ocr import Ocr

POLL = 0.25            # 找横幅的间隔(横幅只停留约 1 秒)
AV_EVERY = 1.0         # 读行动值的间隔
PROGRESS_EVERY = 2.0   # 读进度的间隔
CHECK_EVERY = 8.0      # 整屏识别核对还在不在战斗
GONE_SECONDS = 2.5     # 行动值条目连续这么久看不到就整屏核对一次
RECHECK = 1.5          # 看不到行动值条目时,整屏核对之间至少隔这么久
OTHER_SECONDS = 20.0   # 整屏识别一直认不出是什么界面、行动值条目也不在:交回主循环
MAX_SECONDS = 600.0    # 一场战斗最长(M7A 也是 10 分钟)
BANNER_GAP = 1.5       # 同一条横幅的间隔内只算一次
MISS_GAP = 0.75        # 颜色像、文字不是横幅(红色特效)时,隔这么久再识别
MAX_EVENTS = 6         # 最多记几条其他横幅(敌方机制提示)
AV_AFTER_DEATH = 0.6   # 阵亡后隔多久再读行动值
DEBUG_CROPS = 3        # 每局最多存几张横幅小图(核对检测用,只存横幅那一条)

# 横幅位置:由维护者截图对齐顶部关卡进度条换算(1920x1080 约 681~1259, 187~236)。找的范围放宽一些。
BANNER_SCAN = (0.37, 0.12, 0.63, 0.34)    # (x0, y0, x1, y1),只在横幅内部取列,避开两边的首领图标
BANNER_OCR_X = (0.12, 0.88)   # 敌方机制的提示可能很长,横幅跟着变宽
BANNER_ROWS = 30       # 1080 高时横幅约 49 行;首领血条掉血的橙色段颜色接近,但只有约 9 行
BANNER_FILL = 0.35     # 一行里横幅色占比(上下两条满色,中间夹白字的行约 0.45)
BANNER_WORDS = ("致命攻击", "致命", "倒计时减少")
PROGRESS_REGION = (0.30, 0.0, 0.32, 0.07)


def banner_rows(image: Image.Image) -> tuple[int, int] | None:
    """按颜色找阵亡横幅,返回它在整张图里的(上沿, 下沿)行号;没有返回 None。"""
    W, H = image.size
    x0, y0, x1, y1 = BANNER_SCAN
    crop = np.asarray(image.convert("RGB").crop((int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H))))
    hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    salmon = ((h <= 8) | (h >= 172)) & (s >= 90) & (s <= 190) & (v >= 150) & (v <= 230)
    rows = salmon.mean(axis=1) >= BANNER_FILL
    need = BANNER_ROWS * H / 1080
    best, start = None, None
    for i, on in enumerate(list(rows) + [False]):
        if on and start is None:
            start = i
        elif not on and start is not None:
            if i - start >= need and (best is None or i - start > best[1] - best[0]):
                best = (start, i)
            start = None
    if best is None:
        return None
    top = int(y0 * H)
    return top + best[0], top + best[1]


def banner_text(image: Image.Image, ocr: Ocr, rows: tuple[int, int]) -> str:
    """横幅那一条的文字(红色特效凑出的同色区域一般认不出字)。"""
    H = image.size[1]
    a, b = rows
    pad = 6 * H / 1080
    region = (BANNER_OCR_X[0], max(0.0, (a - pad) / H), BANNER_OCR_X[1] - BANNER_OCR_X[0], (b - a + 2 * pad) / H)
    return "".join(t.text for t in ocr(image, region))


def is_death(text: str) -> bool:
    return any(w in text for w in BANNER_WORDS)


@dataclass
class BattleReport:
    curve: list[tuple[int | None, int | None]] = field(default_factory=list)   # (行动值, 进度) 采样
    samples: list[tuple[float, int | None, int | None]] = field(default_factory=list)   # (第几秒, 行动值, 进度),给网页画图
    deaths: list[dict] = field(default_factory=list)   # 每次阵亡:{t: 第几秒, av: 之前的行动值, after: 之后的行动值}
    events: list[str] = field(default_factory=list)    # 其他横幅的文字(敌方机制提示)
    seconds: float = 0.0
    end: str = ""          # 怎么结束的:screen=<界面> / gone / timeout / stopped
    candidates: int = 0    # 颜色像横幅、文字没确认的次数(调检测用)

    def drop_count(self) -> int:
        """行动值单次下降 ≥35 的次数:1 回合 100 行动值,阵亡一次 -35、两个同时 -70。

        这是独立于横幅的第二个信号(横幅只停留约 1 秒,被大招特效挡住时颜色和文字都可能认不出来,
        2026-11-16 实机有两场一次横幅都没认出),两个对不上时要让模型知道。
        """
        av = [a for _, a, _ in self.samples if a is not None]
        return sum(1 for prev, a in zip(av, av[1:]) if prev - a >= 35)

    def text(self) -> str:
        parts = [summarize(self.curve)]
        if self.deaths:
            items = []
            for d in self.deaths:
                drop = f",行动值 {d['av']}→{d['after']}" if d.get("av") is not None and d.get("after") is not None else ""
                items.append(f"第 {d['t']:.0f} 秒{drop}")
            parts.append(f"阵亡 {len(self.deaths)} 次(" + ";".join(items) + ")")
        else:
            parts.append("没有看到阵亡提示")
        missed = self.drop_count() - len(self.deaths)
        if missed > 0:
            parts.append(f"另有 {missed} 次行动值骤降 ≥35 没认出阵亡横幅(可能被大招特效挡住,按阵亡算)")
        if self.events:
            parts.append("战斗提示:" + " / ".join(self.events))
        parts.append(f"用时 {self.seconds:.0f} 秒")
        return ";".join(parts)

    def as_dict(self) -> dict:
        return {"samples": self.samples, "deaths": self.deaths, "events": self.events, "seconds": round(self.seconds, 1),
                "end": self.end, "candidates": self.candidates}


class BattleWatcher:
    def __init__(self, device, ocr: Ocr, *, classify: Callable[[Image.Image], str], log: Callable[[str], None] = print,
                 stop: Callable[[], bool] = lambda: False, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, debug: Path | None = None):
        self.device = device
        self.ocr = ocr
        self.classify = classify      # 整屏识别 → 界面名(和主循环同一套)
        self.log = log
        self.stop = stop
        self.clock = clock
        self.sleep = sleep
        self.debug = debug
        self.saved = 0
        self.saved_miss = 0    # 颜色像横幅、文字没确认的存图数(核对为什么漏检用)

    def _save(self, image: Image.Image, rows: tuple[int, int], label: str) -> None:
        if self.debug is None:
            return
        if label.startswith("候选"):
            if self.saved_miss >= DEBUG_CROPS:
                return
            self.saved_miss += 1
        elif self.saved >= DEBUG_CROPS:
            return
        W = image.size[0]
        a, b = rows
        crop = image.convert("RGB").crop((int(BANNER_OCR_X[0] * W), max(0, a - 10), int(BANNER_OCR_X[1] * W), b + 10))
        self.debug.mkdir(parents=True, exist_ok=True)
        crop.save(self.debug / f"{time.strftime('%H%M%S')}-{label}.jpg", quality=80)
        if not label.startswith("候选"):
            self.saved += 1

    def run(self) -> BattleReport:
        rep = BattleReport()
        start = self.clock()
        next_av = next_progress = start
        last_check = start
        next_banner = start
        marker_seen = start
        other_since: float | None = None
        pending_after: tuple[float, dict] | None = None   # 阵亡后要补读的行动值
        last_av: int | None = None
        while True:
            now = self.clock()
            rep.seconds = now - start
            if self.stop():
                rep.end = "stopped"
                return rep
            if rep.seconds > MAX_SECONDS:
                rep.end = "timeout"
                return rep
            image = self.device.grab().image
            # 阵亡横幅
            rows = banner_rows(image) if now >= next_banner else None
            if rows is not None:
                text = banner_text(image, self.ocr, rows)
                if is_death(text):
                    next_banner = now + BANNER_GAP
                    death = {"t": round(rep.seconds, 1), "av": last_av, "after": None}
                    rep.deaths.append(death)
                    pending_after = (now + AV_AFTER_DEATH, death)
                    self._save(image, rows, "阵亡横幅")
                    self.log(f"战斗第 {rep.seconds:.0f} 秒:有角色阵亡(第 {len(rep.deaths)} 次)")
                elif len(re.sub(r"\W", "", text)) >= 6:
                    next_banner = now + BANNER_GAP
                    if len(rep.events) < MAX_EVENTS and all(SequenceMatcher(None, text, e).ratio() < 0.6
                                                            for e in rep.events):
                        rep.events.append(text)
                        self._save(image, rows, "战斗横幅")
                else:
                    next_banner = now + MISS_GAP
                    rep.candidates += 1
                    self._save(image, rows, "候选")   # 颜色像、文字不是:存小图,回头查是特效还是真横幅 
            # 行动值、进度
            if now >= next_av or (pending_after and now >= pending_after[0]):
                next_av = now + AV_EVERY
                av = read_action_value(image, self.ocr)
                if av is not None:
                    last_av = av
                    rep.curve.append((av, None))
                    rep.samples.append((round(rep.seconds, 1), av, None))
                    if pending_after and now >= pending_after[0]:
                        pending_after[1]["after"] = av
                        pending_after = None
            if now >= next_progress:
                next_progress = now + PROGRESS_EVERY
                progress = read_progress(self.ocr(image, PROGRESS_REGION))
                if progress is not None:
                    rep.curve.append((None, progress))
                    rep.samples.append((round(rep.seconds, 1), None, progress))
            # 还在不在战斗
            if has_marker(image):
                marker_seen = now
            gone = now - marker_seen >= GONE_SECONDS
            if (gone and now - last_check >= RECHECK) or now - last_check >= CHECK_EVERY:
                last_check = now
                screen = self.classify(image)
                if screen == "battle":
                    other_since = None
                    marker_seen = now
                elif screen == "other":
                    other_since = other_since or now
                    if now - other_since >= OTHER_SECONDS and now - marker_seen >= GONE_SECONDS:
                        rep.end = "gone"
                        return rep
                else:
                    rep.end = f"screen={screen}"
                    return rep
            self.sleep(max(0.0, POLL - (self.clock() - now)))
