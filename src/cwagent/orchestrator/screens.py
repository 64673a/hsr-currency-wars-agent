"""靠界面文字判断当前是哪类界面。随采集到的界面逐步补充。"""

from __future__ import annotations

import re

from ..vision.ocr import TextBox, find, has


def classify(boxes: list[TextBox]) -> str:
    if has(boxes, "点击空白处继续"):
        return "tap_continue"
    if has(boxes, "点击空白加速"):
        return "battle_end"
    if has(boxes, "可出战角色人数未达上限"):
        return "deploy_confirm"
    if find(boxes, "^继续挑战$") and find(boxes, "挑战成功|挑战结束|获得金币总览"):
        return "node_result"
    if has(boxes, "对局未完成") or find(boxes, "^返回货币战争$"):
        return "previous_run"
    if has(boxes, "本场对局首领") and find(boxes, "^下一步$"):
        return "run_intro"
    if has(boxes, "是否中断挑战"):
        return "quit_dialog"
    if find(boxes, "对局评价|对局总结"):
        return "settlement"
    # 进度文字前面常带一个被认成字母的图标,如「X1-3 0%」
    if find(boxes, "^战斗阶段$|^战斗开始$|特殊机制生效中|^(我方|敌方)行动中$|^总伤害$") or find(
            boxes, r"^\D?[1-3]-\d{1,2}\s*\d{1,3}%$"):
        return "battle"
    # 顶部 HUD 的节点号和进度被识别成两段文字(如「1-7」「0%」)
    if not has(boxes, "备战阶段") and find(boxes, r"^[1-3]-\d{1,2}$") and find(boxes, r"^\d{1,3}%$"):
        return "battle"
    if has(boxes, "^补给阶段$"):
        return "supply"
    if has(boxes, "选择一个角色获得主角光环"):
        return "spotlight_choice"
    if has(boxes, "请选择投资策略"):
        return "strategy_choice"
    if find(boxes, "^遭遇节点$") or (has(boxes, "遭遇其") and has(boxes, "奖励预览")):
        return "encounter_choice"
    # 幕后茶歇(奇遇伙伴替换奖励节点):选项页和选项里的二级页都有「返回备战界面」
    if find(boxes, "^返回备战界面$") and find(boxes, "^(幕后茶歇|享用茶歇|醒香猫语)$|^聊一聊$|^摇一摇$"):
        return "tea_break"
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


# 需要模型判断的界面交给哪个决策层的模型(config.yaml 的 roles;没配的用对局选的模型)
ROLES = {
    "prepare": "tactical",
    "environment_choice": "strategic", "strategy_choice": "strategic", "spotlight_choice": "strategic",
    "encounter_choice": "chooser", "supply": "chooser", "tea_break": "chooser",
}


def role_of(screen: str) -> str:
    return ROLES.get(screen, "navigator")   # 难度选择、没见过的界面:只是找路,用导航层(可配便宜、少思考的模型)


SCREEN_NAMES = {
    "prepare": "备战", "environment_choice": "投资环境", "strategy_choice": "投资策略", "spotlight_choice": "主角光环",
    "encounter_choice": "遭遇节点", "supply": "补给", "tea_break": "幕后茶歇", "difficulty": "难度选择",
    "other": "其他界面",
}


def meaningful(boxes: list[TextBox]) -> list[TextBox]:
    """去掉 UID、延迟数字、单个字符这些不说明界面内容的文字。"""
    return [b for b in boxes if len(b.text) > 1 and not re.match(r"^(UID|\d+ms$)", b.text)]


def intro_text(boxes: list[TextBox]) -> str:
    """开局介绍界面的文字(首领、怪物词条、奇遇伙伴),按行拼起来记进本局档案。"""
    rows: dict[int, list[TextBox]] = {}
    for b in meaningful(boxes):
        if b.text in ("下一步",):
            continue
        rows.setdefault(round(b.y * 40), []).append(b)
    return "\n".join(" ".join(b.text for b in sorted(r, key=lambda b: b.x)) for _, r in sorted(rows.items()))


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


# 备战阶段的说明(1920x1080 客户区实测)
PREPARE_LAYOUT = """备战界面(程序已经打开商店):
- 用 buy / sell / move / refresh / buy_exp / ready 操作,坐标由程序换算;格子编号见下方「程序识别的局面」,从左到右从 1 开始。
- 顶部爱心图标后的数字是小队生命值,不是金币。
- buy:买商店第几格(1~5),角色进入备战席第一个空位。
- move:把角色从一个格子移到另一个格子(上场、下场、换位),目标最好是空格。同名角色不能同时上场两个。
- sell:卖掉某格的角色。refresh:刷新商店,刷新后程序会停下,把新商店给你看。buy_exp:买经验,times 为次数。
- ready:出战(程序会先收起商店)。一个备战阶段以 ready 结束。
- 装备、晶矿等其他操作仍用 click / drag,坐标见「界面文字」;点角色会在右侧弹出详情。"""


TEA_BREAK_HINT = """幕后茶歇(流程见规则卡):选一个选项后,选项右边会出现「确认」按钮,点确认才算选定;
之后出现帽子画面时点击空白处继续。选定之前不要点「返回」或「返回备战界面」。"""


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
