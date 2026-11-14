"""备战席里的武装箱等道具(卡面下方写着「开启」)和点开后的选择面板。

点道具弹出「<道具名> 请选择1个」面板,4 个选项(如以太钻头、折叠小刀、量产型装甲、幸运星),选项名在 y≈0.31;
点棋盘空白处可以不选、收起面板,道具留着(2026-11-14 实测)。所以可以先看选项再决定什么时候开(维护者:
屯着武装箱,等别的渠道拿到配方的一半,再开箱选另一半)。
"""

from __future__ import annotations

from .board import BENCH_X
from .ocr import TextBox, find

CLOSE_POINT = (0.75, 0.5)     # 棋盘右侧空白处:不选、收起面板


def item_slots(boxes: list[TextBox]) -> list[int]:
    """备战席里有道具的格子(从 1 开始)。"""
    out = []
    for b in boxes:
        if b.text == "开启" and 0.8 < b.y < 0.93:
            i = min(range(len(BENCH_X)), key=lambda k: abs(BENCH_X[k] - b.x))
            if abs(BENCH_X[i] - b.x) < 0.03:
                out.append(i + 1)
    return sorted(set(out))


def read_panel(boxes: list[TextBox]) -> tuple[str, list[TextBox]] | None:
    """选择面板:(道具名, 选项的文字框);没有面板时返回 None。"""
    prompt = find(boxes, "^请选择")
    if prompt is None:
        return None
    title = next((b.text for b in boxes if 0 < prompt.y - b.y < 0.06 and abs(b.x - prompt.x) < 0.12), "道具")
    options = sorted((b for b in boxes if 0.26 < b.y < 0.35 and 0.2 < b.x < 0.85 and len(b.text) >= 2
                      and "详情" not in b.text), key=lambda b: b.x)
    return title, options
