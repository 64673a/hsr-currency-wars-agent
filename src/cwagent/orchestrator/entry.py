"""开局前按页面导航到货币战争首页,参考 M7A 的 screens.json 页面图。"""

from __future__ import annotations

from collections import Counter
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ..brain.screen_agent import Action
from ..vision.ocr import TextBox, find

ASSETS = Path(__file__).resolve().parents[1] / "assets" / "m7a" / "entry"
MATCH = 0.88
MAX_ACTIONS = 24
MAX_RECOVERIES = 3
MAX_PAGE_ACTIONS = 4


@lru_cache(maxsize=1)
def templates() -> dict[str, np.ndarray]:
    return {p.stem: cv2.imread(str(p)) for p in ASSETS.glob("*.png")}


def targets(image: Image.Image) -> dict[str, tuple[float, float]]:
    """匹配 M7A 的 1080p 页面图标,返回相对完整客户区的中心。"""
    picture = cv2.cvtColor(np.asarray(image.convert("RGB").resize((1920, 1080))), cv2.COLOR_RGB2BGR)
    out = {}
    for name, template in templates().items():
        if template is None:
            continue
        _, score, _, point = cv2.minMaxLoc(cv2.matchTemplate(picture, template, cv2.TM_CCOEFF_NORMED))
        if score >= MATCH:
            h, w = template.shape[:2]
            out[name] = ((point[0] + w / 2) / 1920, (point[1] + h / 2) / 1080)
    return out


class EntryNavigator:
    def __init__(self):
        self.page = ""
        self.actions = 0
        self.recoveries = 0
        self.attempts: Counter = Counter()
        self.joining = False

    def _action(self, page: str, action: Action) -> list[Action]:
        self.page = page
        self.actions += 1
        self.attempts[page] += 1
        limit = 15 if page == "loading" else 8 if page == "interaction" else MAX_PAGE_ACTIONS
        if self.actions > MAX_ACTIONS or self.attempts[page] > limit:
            return [Action(type="pause", reason="货币战争入口多次操作后仍未推进,保留当前页面")]
        return [action]

    def _click(self, page: str, point: tuple[float, float], reason: str) -> list[Action]:
        return self._action(page, Action(type="click", x=point[0], y=point[1], reason=reason))

    def plan(self, screen: str, image: Image.Image | None, boxes: list[TextBox], *, in_run: bool) -> list[Action] | None:
        if in_run or screen != "other" or image is None or image.width < 640:
            return None
        points = targets(image)
        if "homepage" in points:
            return self._click("homepage", points["homepage"], "已识别货币战争首页,进入模式选择")
        guide5 = "guide5" in points or find(boxes, "旷宇纷争", region=(0.0, 0.0, 0.9, 0.25)) is not None
        guide = guide5 or any(name in points for name in ("guide2", "guide3", "guide4"))
        if guide:
            if not guide5:
                if "guide5_tab" in points:
                    return self._click("guide_tab", points["guide5_tab"], "指南切到旷宇纷争")
                return self._action("guide_tab", Action(type="wait", seconds=2, reason="等待指南页签显示"))
            detail = find(boxes, "货币战争|入画奇航", region=(0.36, 0.25, 0.52, 0.62))
            if detail is not None:
                enter = find(boxes, "前往参与", region=(0.35, 0.25, 0.53, 0.65))
                if enter is not None:
                    self.joining = True
                    return self._click("participate", (enter.x, enter.y), "确认货币战争详情后前往参与")
            row = find(boxes, "货币战争", region=(0.12, 0.23, 0.24, 0.50))
            if row is not None:
                return self._click("currency_row", (row.x, row.y), "指南选择货币战争,核对详情后再进入")
            return self._action("currency_row", Action(type="wait", seconds=2, reason="等待指南中的货币战争入口"))
        guide_icon = points.get("phone_guide")
        if guide_icon and 0.65 <= guide_icon[0] <= 0.95 and 0.25 <= guide_icon[1] <= 0.96:
            return self._click("phone", guide_icon, "从手机菜单打开星际和平指南")
        if "world" in points:
            if self.joining:
                interaction = find(boxes, "货币战争", region=(0.40, 0.20, 0.58, 0.65))
                if interaction is not None:
                    return self._action("interaction", Action(type="key", key="f", reason="与货币战争入口交互"))
                return self._action("interaction", Action(type="wait", seconds=2, reason="等待前往参与后的传送和交互入口"))
            return self._action("world", Action(type="key", key="esc", reason="从大世界打开手机菜单"))
        if "click_enter" in points:
            return self._click("click_enter", points["click_enter"], "进入已登录的游戏")
        if len(boxes) < 2:
            return self._action("loading", Action(type="wait", seconds=2, reason="等待开局前的加载画面"))
        self.recoveries += 1
        if self.recoveries > MAX_RECOVERIES:
            return [Action(type="pause", reason="开局前多次返回仍未识别导航入口,保留当前页面")]
        return self._action("recovery", Action(type="key", key="esc", reason="关闭当前菜单后重新识别入口"))
