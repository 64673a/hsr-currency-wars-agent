"""备战界面的阵容识别:前台、后台、备战席每一格是谁、几星。

卡面上没有名字,只能比对头像。头像来源按优先级:
- March7thAssistant 的角色头像(data/templates/avatars/,知识同步时下载):和卡面是同一套立绘、
  比例也接近,还包括皮肤(装了皮肤的角色卡面跟着换,官方图标对不上,例如火花「甜梦电波」);
- 官方接口的角色图标(data/templates/roles/<id>.png):卡面就是它按卡宽缩放后的上半部分;
- 游戏里商店卡片的立绘(data/templates/cards/<id>.png):前两种都没有的角色或皮肤,
  商店卡片下方有名字,看到一次就存下来(learn_from_shop);
- 点开详情面板读到名字的卡面(data/templates/learned/<id>-<n>.png):还是认不出的卡,程序点开详情读名字
  (M7A 的做法),把卡面中央按 M7A 头像的比例存下来,之后和 M7A 头像一起比(learn_card)。
坐标以 1920x1080 客户区实测(2026-11-14),全部归一化。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .cards import bench_marks, card_cost, star_colors
from .ocr import TextBox

# 每格卡片的中心和大小(归一化)。前台、后台每排居中、等距。前台固定 4 格;后台 6 格起,每个佩佩 / 叽米、
# 每个财富宝钻多一格,最多 9 格(M7A 的规则;2026-11-14 实机见过 6 格和 7 格),格子数见 detect_counts。
FRONT_Y = 0.372
FRONT_SIZE = (0.062, 0.116)
BACK_Y = 0.615
BACK_SIZE = (0.066, 0.120)
BENCH_X = (0.2285, 0.2935, 0.3585, 0.4240, 0.4890, 0.5535, 0.6185, 0.6840, 0.7485)
BENCH_Y = 0.845
BENCH_SIZE = (0.059, 0.120)
# 商店展开时棋盘会缩小下移(备战席不变)
FRONT_Y_SHOP = 0.451
FRONT_SIZE_SHOP = (0.050, 0.093)
BACK_Y_SHOP = 0.638
BACK_SIZE_SHOP = (0.054, 0.094)
ROW_X = {   # (zone, 商店展开): (整排中心, 格距)
    ("front", False): (0.5006, 0.07467), ("back", False): (0.4995, 0.0752),
    ("front", True): (0.5003, 0.0603), ("back", True): (0.5000, 0.0592),
}
DEFAULT_COUNTS = {"front": 4, "back": 6}
BACK_COUNTS = range(6, 10)
COUNT_MIN = 0.5      # 分数低于这个(弹窗、动画挡住棋盘)就看不出奇偶

# 商店 5 张卡:立绘区域(卡片上半部,特性条之上)和名字所在的行
SHOP_X = (0.2550, 0.3960, 0.5350, 0.6760, 0.8160)
SHOP_ART = (0.126, 0.057, 0.115)   # 宽、顶、高
SHOP_NAME_Y = (0.262, 0.302)

MIN_SCORE = 0.6    # 低于这个分数就当作认不出(实测认对的在 0.67~0.9)

# M7A 头像:取中央 64x64 像素(避开左上角的属性图标和四周底色),缩放到卡面比例后在卡面里找。
# 实测(2026-11-14):宽 113 像素的卡面上是 0.9 倍,匹配中心落在卡面 (0.45~0.56, 0.33~0.48);
# 认对的 ≥0.8,认错的 ≤0.73,晶矿、弹窗遮挡等非角色 ≤0.70。
# 先用中间倍率比一遍所有头像,再对分数最高的几张细调倍率。
AVATAR_SIDE = 64
AVATAR_SCALE = 0.9 / 113
AVATAR_STEPS = (1.0, 0.94, 0.97, 1.03)
AVATAR_CENTER = (0.50, 0.44)
AVATAR_SLACK = 0.12
AVATAR_REFINE = 6
AVATAR_MIN = 0.75


@dataclass
class Slot:
    zone: str      # front / back / bench
    index: int     # 从 0 开始,从左到右
    x: float       # 格子中心,可直接用于点击和拖拽
    y: float
    name: str | None = None
    role_id: str | None = None
    score: float = 0.0
    stars: int = 0
    cost: int | None = None     # 卡面底部色条读出的费用档(1~5)
    halo: bool = False          # 星星是蓝色的:选了主角光环(维护者告知)
    mark: str = ""              # 备战席的特殊格:乱流格 / 锁定(M7A 的模板)

    @property
    def empty(self) -> bool:
        return self.name is None and self.score == 0.0


def row_x(zone: str, shop_open: bool, n: int) -> list[float]:
    cx, pitch = ROW_X[(zone, shop_open)]
    return [cx + (i - (n - 1) / 2) * pitch for i in range(n)]


def _row_y(zone: str, shop_open: bool) -> float:
    return {"front": FRONT_Y_SHOP, "back": BACK_Y_SHOP}[zone] if shop_open else {"front": FRONT_Y, "back": BACK_Y}[zone]


def slots(shop_open: bool = False, counts: dict[str, int] | None = None) -> list[Slot]:
    counts = {**DEFAULT_COUNTS, **(counts or {})}
    out = []
    for zone in ("front", "back"):
        y = _row_y(zone, shop_open)
        out += [Slot(zone, i, x, y) for i, x in enumerate(row_x(zone, shop_open, counts[zone]))]
    out += [Slot("bench", i, x, BENCH_Y) for i, x in enumerate(BENCH_X)]
    return out


def back_hint(level: int | None, deployed: str | None) -> int | None:
    """按界面数字推后台格子数:团队规模上限 = 等级 + 财富宝钻数,「已上场/上限」的上限还要加上佩佩数,
    后台格子 = 6 + 佩佩数 + 宝钻数 = 6 + (上限 − 等级)(M7A)。"""
    m = re.fullmatch(r"\d+/(\d+)", deployed or "")
    if level is None or m is None:
        return None
    n = 6 + int(m.group(1)) - level
    return n if n in BACK_COUNTS else None


def back_scores(img: np.ndarray, shop_open: bool = False) -> dict[int, float]:
    """后台各种格子数和画面的吻合程度。每张卡(有角色的卡面、空格的框)底边都是一段横向边缘,格子之间断开:
    取卡片底边一带每列的纵向梯度,比较「格子里」和「格缝、两端外侧」的强弱。
    实测(2026-11-14,88 张备战截图):奇偶对的约 1.5~2.8,奇偶不对的 ≤0.1;同奇偶的(6 和 8)分不开,
    格子数要靠 back_hint。"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    H, W = gray.shape
    w, h = _size("back", shop_open)
    bottom = _row_y("back", shop_open) + h / 2
    band = gray[int((bottom - 0.015) * H):int((bottom + 0.015) * H)]
    prof = np.abs(cv2.Sobel(band, cv2.CV_32F, 0, 1, ksize=3)).max(axis=0)
    pitch = ROW_X[("back", shop_open)][1]

    def mean(x0: float, x1: float) -> float:
        a, b = int(x0 * W), int(x1 * W)
        return float(prof[max(0, a):max(a + 1, b)].mean())

    scores = {}
    for n in BACK_COUNTS:
        xs = row_x("back", shop_open, n)
        inner = [mean(x - w * 0.35, x + w * 0.35) for x in xs]
        gaps = [mean((a + b) / 2 - 0.002, (a + b) / 2 + 0.002) for a, b in zip(xs, xs[1:])]
        gaps += [mean(x - w * 0.35, x + w * 0.35) for x in (xs[0] - pitch, xs[-1] + pitch)]
        scores[n] = float(np.log(np.mean(inner) + 1) - np.log(np.mean(gaps) + 1))
    return scores


def back_count(img: np.ndarray, shop_open: bool, hint: int | None, last: int) -> int:
    """后台几格:界面数字推出来的(没有就用上次的),再用画面核对奇偶;奇偶对不上时取相邻两种里和画面更吻合的。"""
    scores = back_scores(img, shop_open)
    base = hint if hint is not None else last
    even, odd = max(scores[6], scores[8]), max(scores[7], scores[9])
    if max(even, odd) < COUNT_MIN or abs(even - odd) < COUNT_MIN or (base % 2 == 0) == (even > odd):
        return base
    return max((n for n in (base - 1, base + 1) if n in BACK_COUNTS), key=lambda n: scores[n])


def _size(zone: str, shop_open: bool = False) -> tuple[float, float]:
    if shop_open and zone != "bench":
        return FRONT_SIZE_SHOP if zone == "front" else BACK_SIZE_SHOP
    return {"front": FRONT_SIZE, "back": BACK_SIZE, "bench": BENCH_SIZE}[zone]


def occupied_slots(image: Image.Image, shop_open: bool, counts: dict[str, int] | None = None) -> set[tuple[str, int]] | None:
    """哪些格子里有卡(只看是不是棋盘底色,不认是谁),给拖动后的核对用。画面太小(测试里的假画面)时返回 None。"""
    if image.size[0] < 640:
        return None
    img = _bgr(image.resize((1920, 1080)) if image.size != (1920, 1080) else image)
    return {(s.zone, s.index) for s in slots(shop_open, counts)
            if _filled(_crop(img, s.x, s.y, *_size(s.zone, shop_open)))}


def shop_is_open(boxes: list[TextBox]) -> bool:
    """商店展开时右下角的按钮是「收起」。"""
    return any(b.text == "收起" and b.x > 0.8 and b.y > 0.85 for b in boxes)


def _bgr(image: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2BGR)


def _crop(img: np.ndarray, cx: float, cy: float, w: float, h: float) -> np.ndarray:
    H, W = img.shape[:2]
    x0, y0 = int((cx - w / 2) * W), int((cy - h / 2) * H)
    return img[max(0, y0):y0 + int(h * H), max(0, x0):x0 + int(w * W)]


def _inner(card: np.ndarray) -> np.ndarray:
    """卡面去掉右上角站位图标和底部星级、费用条后的头像区域。"""
    h, w = card.shape[:2]
    return card[int(h * 0.08):int(h * 0.62), int(w * 0.08):int(w * 0.72)]


def _filled(card: np.ndarray) -> bool:
    """空格几乎全是棋盘的紫色底(实测空格 ≥0.98,有角色的 ≤0.31)。"""
    hsv = cv2.cvtColor(_inner(card), cv2.COLOR_BGR2HSV)
    purple = (hsv[..., 0] >= 110) & (hsv[..., 0] <= 150) & (hsv[..., 1] >= 60)
    return float(purple.mean()) < 0.8


def _load(path: Path) -> np.ndarray:
    im = Image.open(path).convert("RGBA")
    bg = Image.new("RGBA", im.size, (0, 0, 0, 255))
    bg.alpha_composite(im)
    return _bgr(bg)


def _norm(name: str) -> str:
    return re.sub(r"[·•・]", "·", re.sub(r"\s+", "", name))


def avatar_roles(avatar_names: dict[str, str], names: dict[str, str]) -> dict[str, str]:
    """M7A 头像文件名 -> 角色 id。先按全名对(「砂金·戏浪」是单独的角色),对不上再去掉皮肤名
    (「火花·甜梦电波」「流萤「春日手信」」)和命途(「三月七（虚数）」「星（毁灭）」)归到本体。"""
    ids = {_norm(n): rid for rid, n in names.items()}
    out = {}
    for key, cn in avatar_names.items():
        n = _norm(cn)
        for cand in (n, re.sub(r"「.*」$", "", n), re.sub(r"[（(].*[）)]$", "", n), n.split("·")[0]):
            cand = "开拓者" if cand in ("星", "穹") else cand
            if cand in ids:
                out[key] = ids[cand]
                break
    return out


def _center(img: np.ndarray, side: int = AVATAR_SIDE) -> np.ndarray:
    h, w = img.shape[:2]
    s = min(side, h, w)
    y, x = (h - s) // 2, (w - s) // 2
    return img[y:y + s, x:x + s]


class Portraits:
    """头像库:M7A 头像 + 官方图标 + 从商店学到的立绘。"""

    def __init__(self, templates_dir: Path, names: dict[str, str]):
        self.names = names                       # role_id -> 名字
        self.counts = dict(DEFAULT_COUNTS)       # 前台、后台现在几格(最近一次看得出来的)
        self.ids = {v: k for k, v in names.items()}
        self.cards_dir = templates_dir / "cards"
        self.avatars: list[tuple[str, np.ndarray]] = []   # (role_id, 头像中央),一个角色可以有多张(皮肤)
        self._scaled: dict[int, list[tuple[str, list[np.ndarray]]]] = {}   # 卡宽 -> 缩放好的头像
        self.icons: dict[str, np.ndarray] = {}
        self.cards: dict[str, np.ndarray] = {}
        self.learned_dir = templates_dir / "learned"
        avatars = templates_dir / "avatars"
        if (avatars / "names.json").exists():
            table = json.loads((avatars / "names.json").read_text(encoding="utf-8"))
            for key, rid in avatar_roles(table, names).items():
                p = avatars / f"{key}.png"
                if p.exists():
                    self.avatars.append((rid, _center(_load(p))))
        for p in sorted(self.learned_dir.glob("*.png")):
            rid = p.stem.split("-")[0]
            if rid in names:
                self.avatars.append((rid, _bgr(Image.open(p))))
        roles = templates_dir / "roles"
        for rid in names:
            p = roles / f"{rid}.png"
            if p.exists():
                self.icons[rid] = _load(p)
            p = self.cards_dir / f"{rid}.png"
            if p.exists():
                self.cards[rid] = _bgr(Image.open(p))

    # ---------------------------------------------------------------- 比对

    def _score_icon(self, q: np.ndarray, card_w: int, t: np.ndarray) -> float:
        best = -1.0
        for ratio in (0.88, 0.94, 1.0):          # 实测卡面 = 图标按卡宽的 0.94 倍缩放
            tw = int(card_w * ratio)
            th = int(t.shape[0] * tw / t.shape[1])
            if th < q.shape[0] or tw < q.shape[1]:
                continue
            tt = cv2.resize(t, (tw, th), interpolation=cv2.INTER_AREA)
            best = max(best, float(cv2.matchTemplate(tt, q, cv2.TM_CCOEFF_NORMED).max()))
        return best

    def _score_card(self, q: np.ndarray, art: np.ndarray) -> float:
        best = -1.0
        for scale in (1.32, 1.44, 1.56):         # 实测商店立绘里的头像约是卡面的 1.44 倍
            qq = cv2.resize(q, (int(q.shape[1] * scale), int(q.shape[0] * scale)))
            if qq.shape[0] > art.shape[0] or qq.shape[1] > art.shape[1]:
                continue
            best = max(best, float(cv2.matchTemplate(art, qq, cv2.TM_CCOEFF_NORMED).max()))
        return best

    def _score_avatars(self, card: np.ndarray) -> dict[str, float]:
        h, w = card.shape[:2]
        if w not in self._scaled:
            self._scaled[w] = [(rid, [cv2.resize(t, None, fx=w * AVATAR_SCALE * f, fy=w * AVATAR_SCALE * f,
                                                 interpolation=cv2.INTER_AREA) for f in AVATAR_STEPS])
                               for rid, t in self.avatars]
        half = AVATAR_SIDE * w * AVATAR_SCALE * max(AVATAR_STEPS) / 2
        cx, cy = AVATAR_CENTER[0] * w, AVATAR_CENTER[1] * h
        dx, dy = half + AVATAR_SLACK * w, half + AVATAR_SLACK * h
        window = card[max(0, int(cy - dy)):int(cy + dy), max(0, int(cx - dx)):int(cx + dx)]

        def match(t: np.ndarray) -> float:
            if t.shape[0] > window.shape[0] or t.shape[1] > window.shape[1]:
                return -1.0
            return float(cv2.matchTemplate(window, t, cv2.TM_CCOEFF_NORMED).max())

        first = [match(ts[0]) for _, ts in self._scaled[w]]
        top = sorted(range(len(first)), key=first.__getitem__, reverse=True)[:AVATAR_REFINE]
        scores: dict[str, float] = {}
        for i, (rid, ts) in enumerate(self._scaled[w]):
            s = max([first[i]] + [match(t) for t in ts[1:]]) if i in top else first[i]
            scores[rid] = max(scores.get(rid, -1.0), s)
        return scores

    def identify(self, card: np.ndarray) -> tuple[str | None, float]:
        if self.avatars:
            scores = self._score_avatars(card)
            if scores:
                rid = max(scores, key=scores.__getitem__)
                if scores[rid] >= AVATAR_MIN:
                    return rid, scores[rid]
        q = _inner(card)
        w = card.shape[1]
        scores: dict[str, float] = {}
        for rid, t in self.icons.items():
            scores[rid] = self._score_icon(q, w, t)
        for rid, art in self.cards.items():
            scores[rid] = max(scores.get(rid, -1.0), self._score_card(q, art))
        if not scores:
            return None, 0.0
        rid = max(scores, key=scores.__getitem__)
        return (rid, scores[rid]) if scores[rid] >= MIN_SCORE else (None, scores[rid])

    # ---------------------------------------------------------------- 阵容

    def read(self, image: Image.Image, *, shop_open: bool = False, back_hint: int | None = None) -> list[Slot]:
        img = _bgr(image.resize((1920, 1080)) if image.size != (1920, 1080) else image)
        self.counts["back"] = back_count(img, shop_open, back_hint, self.counts["back"])
        out = []
        for s in slots(shop_open, self.counts):
            w, h = _size(s.zone, shop_open)
            card = _crop(img, s.x, s.y, w, h)
            if s.zone == "bench":
                s.mark = bench_marks(img, round(s.x * 1920), round(s.y * 1080))
            if not _filled(card) or s.mark == "乱流格":     # 空的乱流格上只有一个标记,不是角色
                out.append(s)
                continue
            rid, score = self.identify(card)
            s.role_id, s.name, s.score = rid, self.names.get(rid) if rid else None, max(score, 0.01)
            gold, blue = star_colors(card)
            s.stars, s.halo = (blue, True) if blue and not gold else (gold, False)
            s.cost = card_cost(card)
            out.append(s)
        return out

    # ---------------------------------------------------------------- 从详情面板学头像

    def role_of(self, text: str) -> str | None:
        """详情面板里读到的名字 → 角色 id。名字后面可能粘着别的字,取包含在里面的最长的已知名字。"""
        t = _norm(text)
        hits = [(len(_norm(n)), rid) for rid, n in self.names.items() if _norm(n) and _norm(n) in t]
        return max(hits)[1] if hits else None

    def learn_card(self, card: np.ndarray, rid: str) -> None:
        """把认不出的卡面中央(和 M7A 头像中央 64 像素对应的那块)存成这个角色的头像模板。"""
        h, w = card.shape[:2]
        side = AVATAR_SIDE * w * AVATAR_SCALE
        cx, cy = AVATAR_CENTER[0] * w, AVATAR_CENTER[1] * h
        crop = card[max(0, int(cy - side / 2)):int(cy + side / 2), max(0, int(cx - side / 2)):int(cx + side / 2)]
        if crop.size == 0:
            return
        t = cv2.resize(crop, (AVATAR_SIDE, AVATAR_SIDE), interpolation=cv2.INTER_CUBIC)
        self.learned_dir.mkdir(parents=True, exist_ok=True)
        n = len(list(self.learned_dir.glob(f"{rid}-*.png")))
        Image.fromarray(cv2.cvtColor(t, cv2.COLOR_BGR2RGB)).save(self.learned_dir / f"{rid}-{n + 1}.png")
        self.avatars.append((rid, t))
        self._scaled.clear()

    def card_image(self, image: Image.Image, slot: Slot, shop_open: bool = False) -> np.ndarray:
        img = _bgr(image.resize((1920, 1080)) if image.size != (1920, 1080) else image)
        w, h = _size(slot.zone, shop_open)
        return _crop(img, slot.x, slot.y, w, h)

    # ---------------------------------------------------------------- 从商店学立绘

    def learn_from_shop(self, image: Image.Image, boxes: list[TextBox]) -> list[str]:
        """商店展开时,把每张卡的立绘按卡下的名字存起来。返回新学到的名字。"""
        img = _bgr(image.resize((1920, 1080)) if image.size != (1920, 1080) else image)
        learned = []
        for cx in SHOP_X:
            names = [b for b in boxes if abs(b.x - cx) < 0.065 and SHOP_NAME_Y[0] <= b.y <= SHOP_NAME_Y[1]]
            rid = next((self.ids[t] for b in names for t in [re.sub(r"\s+", "", b.text)] if t in self.ids), None)
            if rid is None or rid in self.cards:
                continue
            w, top, h = SHOP_ART
            H, W = img.shape[:2]
            art = img[int(top * H):int((top + h) * H), int((cx - w / 2) * W):int((cx + w / 2) * W)]
            if art.size == 0 or float(art.std()) < 20:
                continue
            self.cards_dir.mkdir(parents=True, exist_ok=True)
            Image.fromarray(cv2.cvtColor(art, cv2.COLOR_BGR2RGB)).save(self.cards_dir / f"{rid}.png")
            self.cards[rid] = art
            learned.append(self.names[rid])
        return learned


DETAIL_ROLE = re.compile(r"^(前台|后台|前后台)$")
DETAIL_SKIP = {"试用", "试用角色", "备战阶段", "角色详情", "装备追踪中"}


def detail_name(boxes: list[TextBox]) -> str | None:
    """角色详情面板(右侧)里的名字:在「前台 / 后台 / 前后台」标签正上方(2026-11-14 实测:名字约 (0.79, 0.213),
    标签约 (0.81, 0.250);试用角色的名字上方还有「试用」)。没有详情面板返回 None。"""
    role = next((b for b in boxes if DETAIL_ROLE.match(b.text) and b.x > 0.74 and 0.15 < b.y < 0.4), None)
    if role is None:
        return None
    above = [b for b in boxes if b.x > 0.73 and 0.008 < role.y - b.y < 0.06 and b.text not in DETAIL_SKIP
             and re.search(r"[\u4e00-\u9fff]", b.text)]
    return max(above, key=lambda b: b.y).text if above else None


def format_board(board: list[Slot]) -> str:
    """给模型看的阵容(含每格坐标)。"""
    lines = []
    for zone, label in (("front", "前台"), ("back", "后台"), ("bench", "备战席")):
        cells = []
        for s in (b for b in board if b.zone == zone):
            mark = f",{s.mark}" if s.mark else ""
            if s.empty:
                cells.append(f"{s.index + 1}号(空{mark}) ({s.x:.3f},{s.y:.3f})")
            else:
                who = s.name or "认不出"
                star = f" {s.stars}星" if s.stars else ""
                extra = "、".join(x for x in (f"{s.cost}费" if s.cost else "", "主角光环" if s.halo else "",
                                              s.mark) if x)
                cells.append(f"{s.index + 1}号 {who}{star}" + (f"({extra})" if extra else "") + f" ({s.x:.3f},{s.y:.3f})")
        lines.append(f"- {label}:" + ";".join(cells))
    return "\n".join(lines)
