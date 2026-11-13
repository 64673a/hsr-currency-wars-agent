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
    if find(boxes, "对局评价|对局总结"):
        return "settlement"
    if find(boxes, "^战斗阶段$|^战斗开始$|特殊机制生效中|^(我方|敌方)行动中$|^总伤害$") or find(
            boxes, r"^[1-3]-\d{1,2}\s*\d{1,3}%$"):
        return "battle"
    # 顶部 HUD 的节点号和进度被识别成两段文字(如「1-7」「0%」)
    if not has(boxes, "备战阶段") and find(boxes, r"^[1-3]-\d{1,2}$") and find(boxes, r"^\d{1,3}%$"):
        return "battle"
    if has(boxes, "备战阶段"):
        return "prepare"
    if has(boxes, "^投资环境$") and find(boxes, "^确认$"):
        return "environment_choice"
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


# 备战阶段的布局(归一化坐标,1920x1080 客户区实测,商店收起时)
PREPARE_LAYOUT = """备战界面布局(坐标为格子中心,可直接用于 click / drag):
- 前台格(从左到右):(0.410,0.400) (0.475,0.400) (0.550,0.400) (0.625,0.400);格数随等级增加,上场总人数上限 = 等级。
- 后台格(从左到右):(0.330,0.610) (0.405,0.610) (0.480,0.610) (0.555,0.610) (0.630,0.610) (0.700,0.610)。
- 备战席 9 格(从左到右):x = 0.228, 0.293, 0.358, 0.423, 0.488, 0.553, 0.618, 0.683, 0.748,y = 0.845。
- 上场:从备战席 drag 到前台或后台的空格;下场:从格子 drag 回备战席空位;同名角色不能同时上场两个。
- 商店:点右下「商店」打开、「收起」关闭。打开后顶部 5 张卡,中心 (0.25,0.15) (0.39,0.15) (0.53,0.15) (0.67,0.15) (0.81,0.15),点击卡片即购买,角色进入备战席第一个空位;卡片左下是名字、右下是价格。
- 刷新商店按 d,购买经验按 f;点角色会在右侧弹出详情(含「出售」按钮)。
- 「出战」(0.946,0.694):先收起商店再点,开始本节点战斗。"""


DIFFICULTY_HINT = """目标难度:{target}(「A8」是职级分组,后面的数字是组内等级)。
难度列表按职级分组,每组从高到低列出等级,当前选中的一行高亮显示在列表上方。
用 scroll 在 (0.5, 0.55) 滚动列表:负数向下(难度降低),正数向上(难度升高),大约 3 格变化一级。
确认高亮的一行正是目标难度(分组名和等级都对)之后,再点「开始对局」。"""
