"""角色卡面上头像以外的信息:费用、星级(金星 / 蓝星),以及备战席的特殊格。

参考 M7A 的 S2 版(tasks/weekly/currency_wars_ui.py),位置改成按卡面大小的比例算,商店展开、棋盘缩小时也能用。
- 费用:卡面最下方的色条,1~5 费依次是灰、绿、蓝、紫、金(M7A 的调色板)。立绘里也可能有接近的颜色(绯英的白灰色
  衣服像 1 费的灰),所以只认最下面那一段左右两侧都均匀、颜色一致的色条。实测(2026-11-14,586 张认出的卡)见测试。
- 星级:费用条上方居中的星星。金色是普通角色,蓝色是选了主角光环的角色(维护者告知,2026-11-15),都有 1~3 星。
- 角色带的装备不从画面读:程序每次装备时记进本局档案(维护者:装备是可追溯的,2026-11-15)。
- 备战席的特殊格(M7A 的模板,1920x1080 原尺寸):乱流格(奇遇:站在里面的角色下个节点会被换成同费用的别的角色)、
  角色卡上方的锁(「N 节点后解锁上阵」)。
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

ASSETS = Path(__file__).resolve().parents[1] / "assets" / "m7a"
PALETTE = np.array(((128, 131, 148), (44, 130, 121), (89, 112, 226), (143, 109, 248), (209, 170, 79)),
                   np.int16)[:, ::-1]   # M7A 的 RGB 调色板,转成 BGR
COST_DIST = 45         # 两侧中位色和调色板的距离(三个通道差的和)
COST_STD = 22          # 色条是纯色,两侧色块的标准差要小(立绘有纹理)


def card_cost(card: np.ndarray) -> int | None:
    """卡面(BGR,按格子大小截下来、下方多带一点)最下方色条的费用档 1~5;看不出返回 None。"""
    bar = _cost_bar(card)
    return bar[1] if bar else None


def _cost_bar(card: np.ndarray) -> tuple[int, int] | None:
    """最下方费用色条:(色条最上面一行, 费用档);看不出返回 None。"""
    h, w = card.shape[:2]
    left = card[:, int(w * 0.08):int(w * 0.22)].astype(np.int16)
    right = card[:, int(w * 0.78):int(w * 0.92)].astype(np.int16)
    hits: list[tuple[int, int]] = []           # (行, 费用)
    for r in range(int(h * 0.70), h):
        segs = (left[r], right[r])
        if max(float(s.std(axis=0).max()) for s in segs) > COST_STD:
            continue
        dl, dr = (np.abs(PALETTE - np.median(s, axis=0)).sum(axis=1) for s in segs)
        k = int(np.argmin(dl + dr))
        if dl[k] <= COST_DIST and dr[k] <= COST_DIST:
            hits.append((r, k + 1))
    if not hits:
        return None
    # 最下面的一段连续同色的行就是色条(卡片被选中抬起时整张上移,色条仍是最下面那段)
    last_row, cost = hits[-1]
    run = [r for r, c in hits if c == cost and last_row - r <= h * 0.08]
    return (min(run), cost) if len(run) >= 2 else None


def star_colors(card: np.ndarray) -> tuple[int, int]:
    """卡片底部星星:(金星数, 蓝星数)。M7A 的颜色门槛(RGB):金 r>223、g>171、g-b>17;蓝 b>228、g>202、b-r>35。"""
    h, w = card.shape[:2]
    bar = _cost_bar(card)
    # 星星在费用色条正上方。认得出色条时只看色条上方那一段:卡片被选中抬起时,下面露出的装备图标有金色,
    # 原来按固定比例取下半截会把它们也数成金星(2026-11-17 实机,1 星那刻夏被认成 3 星)
    top, bottom = ((max(0, bar[0] - int(h * 0.27)), bar[0]) if bar and bar[0] > h * 0.5
                   else (int(h * 0.68), int(h * 0.95)))
    band = card[top:bottom, int(w * 0.12):int(w * 0.88)].astype(np.int16)
    b, g, r = band[..., 0], band[..., 1], band[..., 2]
    gold = (r > 223) & (g > 171) & (g - b > 17)
    blue = (b > 228) & (g > 202) & (b - r > 35)
    scale = (h / 130) ** 2                    # M7A 的面积门槛按备战席卡高 130 像素定的

    def count(mask: np.ndarray) -> int:
        n, _, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8))
        # 星星是近似正方的小块(实机约 10×11 像素);卡片抬起时下沿的发光边、装备图标上沿是细横条,不算。
        # 几颗星排在同一行:只数最靠下的那一行,立绘里更高处的金色(金发等)不算
        rows = [stats[k, cv2.CC_STAT_TOP] + stats[k, cv2.CC_STAT_HEIGHT] / 2 for k in range(1, n)
                if 12 * scale <= stats[k, cv2.CC_STAT_AREA] <= 160 * scale and stats[k, cv2.CC_STAT_WIDTH] <= w * 0.2
                and h * 0.03 <= stats[k, cv2.CC_STAT_HEIGHT] <= h * 0.16
                and 0.5 <= stats[k, cv2.CC_STAT_WIDTH] / stats[k, cv2.CC_STAT_HEIGHT] <= 2.0]
        return sum(1 for y in rows if max(rows) - y <= h * 0.05) if rows else 0

    return min(3, count(gold)), min(3, count(blue))


_templates: dict[str, np.ndarray] = {}


def template(name: str) -> np.ndarray | None:
    if name not in _templates:
        t = cv2.imread(str(ASSETS / f"{name}.png"))
        if t is None:
            return None
        _templates[name] = t
    return _templates[name]


def match(img: np.ndarray, name: str, box: tuple[int, int, int, int]) -> float:
    """在 1920x1080 的 BGR 图的 box (x0, y0, x1, y1) 里找 M7A 的模板,返回最高分。"""
    t = template(name)
    x0, y0, x1, y1 = box
    area = img[max(0, y0):y1, max(0, x0):x1]
    if t is None or area.shape[0] < t.shape[0] or area.shape[1] < t.shape[1]:
        return -1.0
    return float(cv2.matchTemplate(area, t, cv2.TM_CCOEFF_NORMED).max())


PEPPY = "佩佩"


def is_peppy(img: np.ndarray, x: int, y: int) -> bool:
    """后台格子 (x, y)(1920x1080 像素)站的是不是佩佩:占后台格和上场人数,不能拖动、不能出售。
    照 M7A v2026.11.18 对每个后台格子匹配头像(之前有误拖佩佩的 bug),不假设它只站在最右边。"""
    return match(img, "s2_peppy", (x - 52, y - 54, x + 52, y + 39)) >= 0.85


def bench_marks(img: np.ndarray, x: int, y: int) -> str:
    """备战席格子 (x, y)(1920x1080 像素)的特殊标记:「乱流格」「锁定」或空串。门槛和位置照搬 M7A。"""
    if match(img, "s2_black_hole_slot", (x - 47, y - 48, x + 47, y + 48)) >= 0.60:
        return "乱流格"
    if match(img, "s2_bench_deployment_lock", (x - 39, y - 104, x + 17, y - 71)) >= 0.75:
        return "锁定"
    return ""
