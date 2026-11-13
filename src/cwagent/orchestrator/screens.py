"""靠界面文字判断当前是哪类界面。随采集到的界面逐步补充。"""

from __future__ import annotations

import re

from ..vision.ocr import TextBox, find, has


def classify(boxes: list[TextBox]) -> str:
    if has(boxes, "点击空白处继续"):
        return "tap_continue"
    if has(boxes, "本场对局首领") and find(boxes, "^下一步$"):
        return "run_intro"
    if has(boxes, "是否中断挑战"):
        return "quit_dialog"
    if has(boxes, "备战阶段"):
        return "prepare"
    if has(boxes, "^投资环境$") and find(boxes, "^确认$"):
        return "environment_choice"
    if find(boxes, "挑战失败|挑战成功|对局总结|通关"):
        return "settlement"
    if has(boxes, "开始「货币战争」"):
        return "home"
    if find(boxes, "^进入(标准|超频)博弈$"):
        return "mode_select"
    if has(boxes, "^开始对局$") and has(boxes, "敌人难度"):
        return "difficulty"
    return "other"


def companion_name(boxes: list[TextBox]) -> str | None:
    """开局界面上「本局奇遇」下方的伙伴名。"""
    label = find(boxes, "^本局奇遇$")
    if not label:
        return None
    below = [b for b in boxes if 0.01 < b.y - label.y < 0.09 and abs(b.x - label.x) < 0.12]
    return below[0].text if below else None


def text_signature(boxes: list[TextBox]) -> set[str]:
    return {re.sub(r"\d+", "#", b.text) for b in boxes}


def similar(a: set[str], b: set[str]) -> float:
    return len(a & b) / max(1, len(a | b))
