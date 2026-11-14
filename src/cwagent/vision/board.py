"""备战界面的阵容识别:前台、后台、备战席每一格是谁、几星。

卡面上没有名字,只能比对头像。头像来源两种:
- 官方接口的角色图标(data/templates/roles/<id>.png):卡面就是它按卡宽缩放后的上半部分;
- 游戏里商店卡片的立绘(data/templates/cards/<id>.png):有些角色的卡面和官方图标不是同一张图,
  商店卡片下方有名字,看到一次就存下来(learn_from_shop)。
坐标以 1920x1080 客户区实测(2026-11-14),全部归一化。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .ocr import TextBox

# 每格卡片的中心和大小(归一化)
FRONT_X = (0.3885, 0.4630, 0.5375, 0.6125)
FRONT_Y = 0.372
FRONT_SIZE = (0.062, 0.116)
BACK_X = (0.3115, 0.3870, 0.4620, 0.5375, 0.6120, 0.6875)
BACK_Y = 0.615
BACK_SIZE = (0.066, 0.120)
BENCH_X = (0.2285, 0.2935, 0.3585, 0.4240, 0.4890, 0.5535, 0.6185, 0.6840, 0.7485)
BENCH_Y = 0.845
BENCH_SIZE = (0.059, 0.120)
# 商店展开时棋盘会缩小下移(备战席不变)
FRONT_X_SHOP = (0.4100, 0.4700, 0.5300, 0.5910)
FRONT_Y_SHOP = 0.451
FRONT_SIZE_SHOP = (0.050, 0.093)
BACK_X_SHOP = (0.3520, 0.4120, 0.4700, 0.5300, 0.5900, 0.6480)
BACK_Y_SHOP = 0.638
BACK_SIZE_SHOP = (0.054, 0.094)

# 商店 5 张卡:立绘区域(卡片上半部,特性条之上)和名字所在的行
SHOP_X = (0.2550, 0.3960, 0.5350, 0.6760, 0.8160)
SHOP_ART = (0.126, 0.057, 0.115)   # 宽、顶、高
SHOP_NAME_Y = (0.262, 0.302)

MIN_SCORE = 0.6    # 低于这个分数就当作认不出(实测认对的在 0.67~0.9)


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

    @property
    def empty(self) -> bool:
        return self.name is None and self.score == 0.0


def slots(shop_open: bool = False) -> list[Slot]:
    if shop_open:
        out = [Slot("front", i, x, FRONT_Y_SHOP) for i, x in enumerate(FRONT_X_SHOP)]
        out += [Slot("back", i, x, BACK_Y_SHOP) for i, x in enumerate(BACK_X_SHOP)]
    else:
        out = [Slot("front", i, x, FRONT_Y) for i, x in enumerate(FRONT_X)]
        out += [Slot("back", i, x, BACK_Y) for i, x in enumerate(BACK_X)]
    out += [Slot("bench", i, x, BENCH_Y) for i, x in enumerate(BENCH_X)]
    return out


def _size(zone: str, shop_open: bool = False) -> tuple[float, float]:
    if shop_open and zone != "bench":
        return FRONT_SIZE_SHOP if zone == "front" else BACK_SIZE_SHOP
    return {"front": FRONT_SIZE, "back": BACK_SIZE, "bench": BENCH_SIZE}[zone]


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


def count_stars(card: np.ndarray) -> int:
    """卡片底部、费用条上方居中的发光星星个数:数亮黄色星芯占的列段。"""
    h, w = card.shape[:2]
    band = card[int(h * 0.70):int(h * 0.93), int(w * 0.12):int(w * 0.88)]
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    core = (hsv[..., 2] >= 235) & (hsv[..., 1] >= 50) & (hsv[..., 0] >= 10) & (hsv[..., 0] <= 40)
    cols = list(core.sum(axis=0)) + [0]
    runs, start = 0, None
    for i, v in enumerate(cols):
        if v > 0 and start is None:
            start = i
        elif v == 0 and start is not None:
            runs += i - start >= 2
            start = None
    return min(3, runs)


def _load(path: Path) -> np.ndarray:
    im = Image.open(path).convert("RGBA")
    bg = Image.new("RGBA", im.size, (0, 0, 0, 255))
    bg.alpha_composite(im)
    return _bgr(bg)


class Portraits:
    """头像库:官方图标 + 从商店学到的立绘。"""

    def __init__(self, templates_dir: Path, names: dict[str, str]):
        self.names = names                       # role_id -> 名字
        self.ids = {v: k for k, v in names.items()}
        self.cards_dir = templates_dir / "cards"
        self.icons: dict[str, np.ndarray] = {}
        self.cards: dict[str, np.ndarray] = {}
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

    def identify(self, card: np.ndarray) -> tuple[str | None, float]:
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

    def read(self, image: Image.Image, *, shop_open: bool = False) -> list[Slot]:
        img = _bgr(image.resize((1920, 1080)) if image.size != (1920, 1080) else image)
        out = []
        for s in slots(shop_open):
            w, h = _size(s.zone, shop_open)
            card = _crop(img, s.x, s.y, w, h)
            if not _filled(card):
                out.append(s)
                continue
            rid, score = self.identify(card)
            s.role_id, s.name, s.score = rid, self.names.get(rid) if rid else None, max(score, 0.01)
            s.stars = count_stars(card)
            out.append(s)
        return out

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


def format_board(board: list[Slot]) -> str:
    """给模型看的阵容(含每格坐标)。"""
    lines = []
    for zone, label in (("front", "前台"), ("back", "后台"), ("bench", "备战席")):
        cells = []
        for s in (b for b in board if b.zone == zone):
            if s.empty:
                cells.append(f"{s.index + 1}号(空) ({s.x:.3f},{s.y:.3f})")
            else:
                who = s.name or "认不出"
                star = f" {s.stars}星" if s.stars else ""
                cells.append(f"{s.index + 1}号 {who}{star} ({s.x:.3f},{s.y:.3f})")
        lines.append(f"- {label}:" + ";".join(cells))
    return "\n".join(lines)
