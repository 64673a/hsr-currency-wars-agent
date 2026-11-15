"""程序直接执行的固定流程(不经过模型):收集本位面的节点序列、打开晶矿。

晶矿的检测和时机参考 March7thAssistant(GPL-3.0)S2 版的 _gather / reward_orbs:霍夫圆找出每个晶矿球,
每个备战回合一开始先开、出战前再看一次,失败只放过当前节点。开法优先用虚拟手柄(LT+RT 一次全开,
device/gamepad.py),手柄用不了时像 M7A 那样用鼠标逐个横向划过。

都只在备战界面、商店收起时执行,每一步都截图确认效果,不对就放弃,交回模型。
坐标以 1920x1080 客户区实测(2026-11-14)。
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ..vision.ocr import Ocr, TextBox, find, has

TOP_FIRST_NODE = (0.307, 0.058)     # 备战界面顶部节点轨道的第一个图标
PLANE_WAIT = 4.0                    # 点开后最多等多久「位面详情」出来(节点图标可能还在播「获得经验」动画)
DETAIL_ROW = (0.48, 0.61)           # 位面详情里中间那一排节点图标的纵向范围
DETAIL_PANEL = (0.0, 0.60, 1.0, 0.25)
CLOSE_X = (0.969, 0.063)
ORB_SEARCH = (1275, 202, 1624, 465)   # 找晶矿球的范围(像素,x0, y0, x1, y1):避开上方道具栏(M7A)
GIFT_SEARCH = (1280, 112, 1570, 270)  # 奇遇倒计时礼盒可能出现的范围
GIFT_TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "crystals" / "timed_gift.png"   # 取自 M7A
BENCH_FULL_AREA = (0.2, 0.22, 0.6, 0.12)   # 「备战席已满」提示
AFFIX_ROW = (0.40, 0.89, 0.58, 0.06)  # 位面详情底部的敌人词条(在「敌人难度」右边;2026-11-14 截图:y 0.92,x 0.45~0.73)
AFFIX_BLANK = (0.12, 0.45)            # 位面详情左边的空白:点它收起词条说明【待核实】


def _arr(image: Image.Image) -> np.ndarray:
    im = image.convert("RGB")
    if im.size != (1920, 1080):
        im = im.resize((1920, 1080))
    return np.asarray(im)


# ---------------------------------------------------------------- 位面节点序列

def detail_nodes(image: Image.Image) -> list[float]:
    """位面详情界面中间一排节点图标的横坐标(从左到右)。"""
    a = _arr(image)
    y0, y1 = int(DETAIL_ROW[0] * 1080), int(DETAIL_ROW[1] * 1080)
    gray = cv2.cvtColor(a[y0:y1], cv2.COLOR_RGB2GRAY)
    circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1.2, minDist=60, param1=120, param2=30,
                               minRadius=18, maxRadius=40)
    if circles is None:
        return []
    cs = circles[0]
    row = float(np.median(cs[:, 1]))          # 节点都在同一条线上,偏离这一行的是别的圆形图案
    return sorted(round(float(c[0]) / 1920, 3) for c in cs if abs(float(c[1]) - row) <= 10)


def _panel_text(boxes: list[TextBox]) -> str:
    x, y, w, h = DETAIL_PANEL
    inside = [b for b in boxes if x <= b.x <= x + w and y <= b.y <= y + h and not b.text.startswith("UID")]
    inside.sort(key=lambda b: (round(b.y, 2), b.x))
    return " ".join(b.text for b in inside)


def read_affixes(device, ocr: Ocr, boxes: list[TextBox], book, log: Callable[[str], None],
                 debug: Path | None = None) -> list[tuple[str, str]]:
    """位面详情底部的敌人词条:[(名字, 说明)]。词条库里有说明的直接用;没有的点开读,说明存进词条库(维护者:官方接口
    没有词条数据,边点边存)。点开后界面上新出现的文字就是说明;每张说明截一张图到 debug 里,方便核对。"""
    x, y, w, h = AFFIX_ROW
    row = sorted((b for b in boxes if x <= b.x <= x + w and y <= b.y <= y + h and not b.text.startswith("敌人难度")
                  and len(b.text) >= 2 and not b.text[0].isdigit()), key=lambda b: b.x)
    out: list[tuple[str, str]] = []
    for b in row:
        desc = book.get(b.text) if book is not None else ""
        if not desc and book is not None:
            before = {t.text for t in boxes}
            device.click(b.x, b.y)
            time.sleep(0.8)
            shot = device.grab()
            now = ocr(shot.image)
            fresh = [t for t in now if t.text not in before and not (y <= t.y <= y + h)]
            desc = "".join(t.text for t in sorted(fresh, key=lambda t: (round(t.y, 2), t.x)))
            if debug is not None:
                debug.mkdir(parents=True, exist_ok=True)
                shot.image.convert("RGB").save(debug / f"{time.strftime('%H%M%S')}-词条-{b.text}.jpg", quality=80)
            device.click(*AFFIX_BLANK)
            time.sleep(0.6)
            boxes = ocr(device.grab().image)
            if desc:
                book.record(b.text, desc)
            if not has(boxes, "位面详情"):
                log("读敌人词条:收起说明后不在位面详情了,剩下的词条不读")
                out.append((b.text, desc))
                break
        out.append((b.text, desc))
    return out


def open_plane_detail(device, ocr: Ocr, log: Callable[[str], None], *, wait: float = PLANE_WAIT):
    """点顶部第一个节点图标打开「位面详情」,返回 (画面, 界面文字);打不开返回 None。

    刚打完后节点图标还在播「获得经验」之类的动画,这时候点会被吞(2026-11-16 实机,1-2 那一次就是这样
    白丢了一个位面的词条)。所以点完不是等一下再看一眼,而是轮询等它出来;等不到再点一次,两次都不行才放弃。
    维护者建议:与其失败了下个回合重试,不如一开始就把等待放长。
    """
    for _ in range(2):
        frame = device.grab()                     # 点击之前先看一眼:已经开着就别再点,免得把它关掉
        boxes = ocr(frame.image)
        if has(boxes, "位面详情"):
            return frame, boxes
        device.click(*TOP_FIRST_NODE)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            time.sleep(0.4)
            frame = device.grab()
            boxes = ocr(frame.image)
            if has(boxes, "位面详情"):
                return frame, boxes
    log("收集节点:等了两个来回也没打开位面详情,放弃")
    return None


def survey_plane(device, ocr: Ocr, log: Callable[[str], None], *, affixes: list | None = None, book=None,
                 debug: Path | None = None) -> list[str] | None:
    """打开位面详情,逐个点节点读说明(传了 affixes 时再读底部的敌人词条,放进这个列表),关闭后回到备战界面。
    失败返回 None。"""
    opened = open_plane_detail(device, ocr, log)
    if opened is None:
        return None
    frame, boxes = opened
    xs = detail_nodes(frame.image)
    y = (DETAIL_ROW[0] + DETAIL_ROW[1]) / 2 - 0.003
    lines: list[str] = []
    for x in xs:
        device.click(x, y)
        time.sleep(0.6)
        text = _panel_text(ocr(device.grab().image))
        if text and text not in lines:
            lines.append(text)
    if affixes is not None:
        try:
            affixes.extend(read_affixes(device, ocr, ocr(device.grab().image), book, log, debug))
        except Exception as e:  # noqa: BLE001 - 词条读不出不影响收集节点
            log(f"读敌人词条出错:{type(e).__name__}: {e}")
    device.click(*CLOSE_X)
    time.sleep(1.0)
    back = ocr(device.grab().image)
    if not find(back, "备战阶段"):
        log("收集节点:关闭位面详情后没回到备战界面")
    return lines


# ---------------------------------------------------------------- 奇遇伙伴

COMPANION_ICON = (0.039, 0.123)          # 备战界面左上角的奇遇伙伴头像(2026-11-16 实机)
COMPANION_BUBBLE = (0.28, 0.75, 0.55, 0.18)   # 详情页里悬停头像后弹出的气泡(名字 + 它带来什么)
COMPANION_FIRST_X = 0.263                # 左下角第一个伙伴头像的 x(1920x1080 实测)
COMPANION_STEP = 0.077                   # 头像间距,按它往右一个个试
COMPANION_Y = 0.863                      # 头像的 y(悬停位置)
COMPANION_SLOTS = 6                      # 最多试几个
COMPANION_CLOSE = (0.970, 0.060)         # 详情页右上角的 X


def _bubble(ocr: Ocr, image: Image.Image) -> str:
    # 气泡区域右边会蹭到头像下面那块名字标签的残字(实机读到过末尾多一个「狼」),单字丢掉
    rows = sorted((b for b in ocr(image, COMPANION_BUBBLE) if len(b.text) > 1), key=lambda b: (round(b.y, 2), b.x))
    return " ".join(b.text for b in rows)


def companion_of_bubble(text: str) -> tuple[str, str]:
    """气泡文字 → (伙伴名, 效果原文)。

    实机见过两种写法:「不死狼将在【幕后茶歇】找你聊聊新的委托。」(名字在句首)、
    「你会在幕后茶歇遇到三月兔。」(名字在「遇到」后面)。都抽不到就返回 ("", 全文),名字交给调用方兜底。
    """
    m = re.match(r"^([\u4e00-\u9fffA-Za-z0-9·★]{2,10}?)(?:将在|会|找你)", text)
    if m:
        return m.group(1), text
    m = re.search(r"(?:遇到|遇见|碰上)([\u4e00-\u9fffA-Za-z0-9·★]{2,10})[。,，]", text)
    return (m.group(1), text) if m else ("", text)


def read_companions(device, ocr: Ocr, log: Callable[[str], None]) -> list[str] | None:
    """点备战界面左上角的奇遇伙伴头像,在「主角光环与奇遇详情」页面左下角逐个悬停,读出本局的奇遇伙伴。

    奇遇伙伴的出场时机不定(维护者 2026-11-16);这一页左下角列着本局所有的伙伴,鼠标停在头像上会弹气泡,
    写着它是谁、带来什么,例如「不死狼将在【幕后茶歇】找你聊聊新的委托。/ 第一位面的最后的奖励节点将替换为幕后茶歇。」
    (2026-11-16 实机截图)。读完点右上角 X 回备战界面。打不开或读不到就返回 None,不影响备战。
    """
    device.click(*COMPANION_ICON)
    time.sleep(1.2)
    if not has(ocr(device.grab().image), "主角光环与奇遇详情"):
        log("读奇遇伙伴:没有打开详情页,放弃")
        return None
    lines: list[str] = []
    misses = 0
    for i in range(COMPANION_SLOTS):
        device.move(COMPANION_FIRST_X + i * COMPANION_STEP, COMPANION_Y)
        time.sleep(0.7)
        text = _bubble(ocr, device.grab().image)
        if not text or text in lines:
            misses += 1
            if misses >= 2:      # 连续两个位置读不到就认为没有更多伙伴
                break
            continue
        misses = 0
        lines.append(text)
    device.click(*COMPANION_CLOSE)
    time.sleep(0.8)
    if not find(ocr(device.grab().image), "备战阶段"):
        log("读奇遇伙伴:关掉详情页后没回到备战界面")
    return lines


# ---------------------------------------------------------------- 晶矿

_GIFT: np.ndarray | None = None


def _gift(gray: np.ndarray) -> tuple[int, int] | None:
    """奇遇的倒计时礼盒(会在面板上停留好几个回合)的中心。"""
    global _GIFT
    if _GIFT is None:
        _GIFT = cv2.cvtColor(np.asarray(Image.open(GIFT_TEMPLATE).convert("RGB")), cv2.COLOR_RGB2GRAY)
    x0, y0, x1, y1 = GIFT_SEARCH
    scores = cv2.matchTemplate(gray[y0:y1, x0:x1], _GIFT, cv2.TM_CCOEFF_NORMED)
    _, best, _, (x, y) = cv2.minMaxLoc(scores)
    return (x0 + x + _GIFT.shape[1] // 2, y0 + y + _GIFT.shape[0] // 2) if best >= 0.86 else None


def find_orbs(image: Image.Image) -> list[tuple[int, int, int]]:
    """面板里的晶矿球(像素坐标 x, y, 半径)。商店收起时才准(商店卡片会被认成很多圆)。

    做法取自 M7A 的 reward_orbs:霍夫圆检测,避开上方道具栏,奇遇的倒计时礼盒不算。另外按圆心颜色核对
    (晶矿是蓝、灰白、金色的亮球),筛掉浮窗文字等形成的圆,并去掉重叠的重复圆。
    实测(2026-11-15,第一、二位面 20 多张截图):空面板全是 0,有晶矿时都能检出;刚掉落还在放特效时会多数。"""
    a = _arr(image)
    gray = cv2.cvtColor(a, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(a, cv2.COLOR_RGB2HSV)
    x0, y0, x1, y1 = ORB_SEARCH
    circles = cv2.HoughCircles(cv2.medianBlur(gray[y0:y1, x0:x1], 5), cv2.HOUGH_GRADIENT, 1.1, 22,
                               param1=85, param2=17, minRadius=12, maxRadius=37)
    if circles is None:
        return []
    gift = _gift(gray)
    orbs: list[tuple[int, int, int]] = []
    for cx, cy, cr in circles[0]:
        x, y, r = x0 + int(cx), y0 + int(cy), int(cr)
        if gift and gift[0] - 83 <= x <= gift[0] + 83 and gift[1] - 51 <= y <= gift[1] + 112:
            continue
        k = max(4, int(r * 0.55))
        h, s, v = np.median(hsv[y - k:y + k, x - k:x + k].reshape(-1, 3), axis=0)
        blue = 95 <= h <= 125 and s >= 90 and v >= 150
        grey = s <= 70 and v >= 140
        gold = 15 <= h <= 35 and s >= 100 and v >= 170
        if not (blue or grey or gold):
            continue
        if any((x - ox) ** 2 + (y - oy) ** 2 < (0.75 * max(r, orr)) ** 2 for ox, oy, orr in orbs):
            continue
        orbs.append((x, y, r))
    return orbs


def settled_orbs(device, image: Image.Image, *, seconds: float = 4.0) -> tuple[Image.Image, int]:
    """等晶矿个数稳定(刚掉落时有特效,个数会跳),返回最后一张截图和个数。"""
    count = len(find_orbs(image))
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(0.7)
        image = device.grab().image
        now = len(find_orbs(image))
        if now == count:
            break
        count = now
    return image, count


def bench_full(boxes: list[TextBox]) -> bool:
    """界面中间「备战席已满」的提示:晶矿里的角色放不下。"""
    return find(boxes, "备战席已满", region=BENCH_FULL_AREA) is not None


@dataclass
class Crystals:
    before: int
    after: int
    how: str
    full: bool = False    # 备战席满了,晶矿里的角色放不下

    @property
    def opened(self) -> bool:
        return self.after < self.before

    def text(self) -> str:
        if self.after == 0:
            return f"划开晶矿({self.before} 个)"
        if self.full:
            done = f"划开 {self.before - self.after} 个晶矿," if self.opened else "晶矿没开成:"
            return f"{done}还剩 {self.after} 个,备战席满了,晶矿里的角色放不下"
        return f"划开 {self.before - self.after} 个晶矿,还剩 {self.after} 个" if self.opened else "尝试划开晶矿(没变化)"


PROMPT_AREA = (0.62, 0.08, 0.3, 0.42)   # 手柄界面里晶矿上的「开启 LT + RT」提示
SHOP_OPEN = (0.8, 0.85, 0.1, 0.12)      # 商店展开时右下角是「收起」


def _save(debug: Path | None, name: str, image: Image.Image) -> None:
    if debug is not None:
        debug.mkdir(parents=True, exist_ok=True)
        image.convert("RGB").save(debug / f"{time.strftime('%H%M%S')}-{name}.jpg", quality=80)


def _by_pad(device, ocr: Ocr | None, log: Callable[[str], None], debug: Path | None) -> bool:
    """手柄 LT+RT 一次全开。切到手柄界面后看不到「开启」提示就不按,返回 False。"""
    enter = getattr(device, "pad_enter", None)
    if enter is None or not enter():
        error = getattr(getattr(device, "pad", None), "error", "")
        if error:
            log(f"晶矿:{error},改用鼠标")
        return False
    shot = device.grab().image
    _save(debug, "晶矿-手柄界面", shot)
    if ocr is not None:
        boxes = ocr(shot)
        if not find(boxes, "^开启$", region=PROMPT_AREA) and find(boxes, "^收起$", region=SHOP_OPEN):
            # 切到手柄界面时商店有时会自己展开(2026-11-14 实机,1-2、1-8 各一次),按 B 收起
            device.pad_press("B")
            shot = device.grab().image
            _save(debug, "晶矿-手柄收起商店", shot)
            boxes = ocr(shot)
        if not find(boxes, "^开启$", region=PROMPT_AREA):
            # 被弹窗挡住时按 LT+RT 会被别的界面吞掉,甚至触发别的操作。不按,改用鼠标
            log("晶矿:手柄界面里没看到「开启」提示,改用鼠标")
            device.pad_exit()
            return False
    device.pad_press_triggers()
    _save(debug, "晶矿-按了LT+RT", device.grab().image)
    device.pad_exit()
    return True


def _by_mouse(device, count: int) -> None:
    """鼠标逐个横向划过晶矿(M7A 的 _gather):晶矿会漂移,每次都按最新位置划。连续两次个数没减少就停。"""
    misses = 0
    for _ in range(count + 2):
        orbs = find_orbs(device.grab().image)
        if not orbs:
            return
        x, y, _ = orbs[0]
        device.drag(max(1275, x - 86) / 1920, y / 1080, min(1638, x + 88) / 1920, y / 1080, duration=0.5)
        time.sleep(0.8)
        now = len(find_orbs(device.grab().image))
        misses = misses + 1 if now >= len(orbs) else 0
        if misses >= 2:
            return


def open_crystals(device, image: Image.Image, log: Callable[[str], None], *, ocr: Ocr | None = None,
                  debug: Path | None = None) -> Crystals | None:
    """打开晶矿(调用前确保备战界面、商店收起、没有弹窗)。没有晶矿返回 None。

    先用手柄 LT+RT 一次全开(2026-11-14 实机验证);手柄用不了或没看到「开启」提示时,改用鼠标逐个划。
    晶矿里有角色而备战席满了时开不了(界面中间提示「备战席已满」),交给模型腾位置。每一步截图存到 debug 目录。"""
    if not find_orbs(image):
        return None
    image, before = settled_orbs(device, image)
    if not before:
        return None
    _save(debug, "晶矿-开之前", image)
    how = "手柄" if _by_pad(device, ocr, log, debug) else "鼠标"
    if how == "鼠标":
        _by_mouse(device, before)
    after_image, after = settled_orbs(device, device.grab().image, seconds=2.5)
    _save(debug, "晶矿-开之后", after_image)
    full = bool(after) and ocr is not None and bench_full(ocr(after_image))
    result = Crystals(before, after, how, full)
    log(f"晶矿({how}):开之前 {before} 个,开之后 {after} 个" + (",备战席已满" if full else ""))
    return result
