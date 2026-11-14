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
    if has(boxes, "^补给阶段$"):
        return "supply"
    if has(boxes, "选择一个角色获得主角光环"):
        return "spotlight_choice"
    if has(boxes, "备战阶段"):
        return "prepare"
    # 商店展开时会挡住顶部的「备战阶段」
    if has(boxes, "购买经验") and find(boxes, "^(商店|收起)$"):
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


def node_of(boxes: list[TextBox]) -> str | None:
    """顶部 HUD 的节点号(如 1-9)。"""
    b = find(boxes, r"^[1-3]-\d{1,2}(?![\d%])", region=(0, 0, 0.6, 0.14))
    m = re.match(r"[1-3]-\d{1,2}(?![\d%])", b.text) if b else None
    return m.group(0) if m else None


def text_signature(boxes: list[TextBox]) -> set[str]:
    return {re.sub(r"\d+", "#", b.text) for b in boxes}


def similar(a: set[str], b: set[str]) -> float:
    return len(a & b) / max(1, len(a | b))


# 备战阶段的布局(归一化坐标,1920x1080 客户区实测,商店收起时)
PREPARE_LAYOUT = """备战界面操作(坐标可直接用于 click / drag):
- 顶部爱心图标后的数字是小队生命值,不是金币。
- 每格的坐标和格子里是谁见下方「程序识别的局面」;商店展开时棋盘会缩小下移,以识别结果里的坐标为准。
- 上场:从备战席 drag 到前台或后台的空格;下场:从格子 drag 回备战席空位;同名角色不能同时上场两个。
- 商店:点右下「商店」打开、「收起」关闭。打开后顶部 5 张卡,中心 (0.255,0.15) (0.396,0.15) (0.535,0.15) (0.676,0.15) (0.816,0.15),点击卡片即购买,角色进入备战席第一个空位;卡片左下是名字、右下是价格。
- 刷新商店按 d,购买经验按 f;点角色会在右侧弹出详情(含「出售」按钮)。
- 「出战」(0.946,0.694):先收起商店再点,开始本节点战斗。"""


DIFFICULTY_HINT = """目标难度:{target}(「A8」是职级分组,后面的数字是组内等级)。
难度列表按职级分组,每组从高到低列出等级,当前选中的一行高亮显示在列表上方。
用 scroll 在 (0.5, 0.55) 滚动列表:负数向下(难度降低),正数向上(难度升高),大约 3 格变化一级。
确认高亮的一行正是目标难度(分组名和等级都对)之后,再点「开始对局」。"""


# 主角光环选择界面的布局(1920x1080 客户区实测,2026-11-14)
SPOTLIGHT_LAYOUT = """主角光环选择界面布局(坐标可直接用于 click / drag):
- 两张光环卡:左 (0.21, 0.45),右 (0.78, 0.45);单击选中。卡下「刷新」:左 (0.245, 0.714),右 (0.806, 0.716)。
- 中间是当前阵容的缩略图(角色卡上没有文字,按位置操作):
  前排格:(0.417,0.574) (0.474,0.574) (0.528,0.574) (0.583,0.574);
  后排格:(0.370,0.700) (0.422,0.700) (0.474,0.700) (0.528,0.700) (0.583,0.700) (0.630,0.700)。
  顺序与备战界面的前台、后台一致,空格不能拖。
- 主角空位在 (0.500, 0.407):把要当主角的角色从上面的格子 drag 到这里。
- 界面中间的「尚未选择主角角色和光环」消失即表示光环和主角都已选好,再点「确认」(0.504, 0.882)。
- 「返回备战界面」(0.922, 0.060) 只是返回,首领节点必须选完光环才能出战。"""
