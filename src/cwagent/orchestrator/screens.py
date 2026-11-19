"""靠界面文字判断当前是哪类界面。随采集到的界面逐步补充。"""

from __future__ import annotations

import re

from ..vision.ocr import TextBox, find, has


RESULT_TITLE = (0.21, 0.12, 0.49, 0.26)    # 结算页标题一带(M7A 的 RESULT_TITLE,1920x1080 下 410,135~1350,410)
MATCH_WINS = ("挑战成功", "对局胜利", "晋升成功")             # 整局结算页的标题(M7A)
MATCH_LOSSES = ("挑战失败", "对局失败", "晋升失败")
MATCH_TITLES = MATCH_WINS[1:] + MATCH_LOSSES[1:]             # 只有整局结算才有的标题


def match_outcome(boxes: list[TextBox]) -> str | None:
    """整局结算页上的胜负:通关 / 失败,认不出返回 None。"""
    if find(boxes, f"^({'|'.join(MATCH_LOSSES)})$") is not None:
        return "失败"
    if find(boxes, f"^({'|'.join(MATCH_WINS)})$") is not None:
        return "通关"
    return None


def _top(boxes: list[TextBox], pattern: str, y1: float = 0.12) -> bool:
    """界面标题一带(顶部居中)有没有这段文字。左侧羁绊列表里也有「奇兽花园」「开拓银轨」这类名字,要排除。"""
    return find(boxes, pattern, region=(0.28, 0.0, 0.48, y1)) is not None


def result_page(boxes: list[TextBox]) -> str | None:
    """结算后续的阵容总结或伤害统计页;按钮漏读时用页面栏目识别。"""
    if find(boxes, "^返回货币战争$") is not None or (
            find(boxes, "^伤害统计$", region=(0.65, 0.15, 0.25, 0.18)) is not None
            and has(boxes, "^总经济$", "^阵容价值$")):
        return "statistics"
    if find(boxes, "对局未完成") is not None or (
            find(boxes, "^我的阵容$", region=(0.35, 0.30, 0.20, 0.14)) is not None
            and has(boxes, "^本局奇遇$|^羁绊信息$")):
        return "summary"
    return None


def companion_banner(boxes: list[TextBox]) -> tuple[float, float] | None:
    """奇遇伙伴的介绍横幅:返回点哪里能跳过,不是横幅返回 None。

    奇遇伙伴出现时机不定(维护者 2026-11-16):那个发光的页面就是它,左边伙伴立绘,中间标题「本局奇遇」,
    正文写它带来什么,底部「点击空白处继续」(实机 y≈0.655)。它会出现任何界面上(投资策略、补给、备战),
    不是每局都有,也不用做任何选择,认出就点掉。
    OCR 没读到「点击空白处继续」时退回横幅中间(那也是空白处);开局介绍界面也有「本局奇遇」,

    但它带「下一步」,不归这里管。
    """
    if (result_page(boxes) is not None or find(boxes, "^本局奇遇$") is None
            or find(boxes, "^下一步$|^下一页$|^返回货币战争$") is not None
            or find(boxes, "对局未完成|对局评价|我的阵容") is not None):
        return None
    hint = find(boxes, "点击空白处继续")
    return (float(hint.x), float(hint.y)) if hint is not None else (0.5, 0.655)


def classify(boxes: list[TextBox]) -> str:
    # 断线提示盖在任何界面上面(M7A S2 资料)
    if has(boxes, "失去网络连接") and has(boxes, "是否尝试重新连接"):
        return "reconnect"
    if (has(boxes, "连接中断") and has(boxes, "退出游戏")) or (has(boxes, "加载失败") and has(boxes, "立即切换")):
        return "disconnected"
    page = result_page(boxes)
    # 伙伴横幅可覆盖投资策略、补给或备战;结算栏目不当作伙伴横幅。
    if page is None and (has(boxes, "点击空白处继续") or companion_banner(boxes) is not None):
        return "tap_continue"
    if has(boxes, "点击空白处关闭"):
        return "tap_close"
    if has(boxes, "点击空白加速"):
        return "battle_end"
    if has(boxes, "助燃券持有数量已达上限"):
        return "reward_limit"
    if has(boxes, "可出战角色人数未达上限") or (has(boxes, "本局不再提示") and find(boxes, "^确认$")):
        return "deploy_confirm"
    if has(boxes, "前台区域无角色|前台无角色") and find(boxes, "^确认$"):
        return "empty_front"
    if page is not None:
        return "previous_run"
    # 整局结算:「挑战成功 / 挑战失败」带「对局评价」那一页,对局到这里才算结束;晋升赛等的标题是「对局胜利」等(M7A)
    if find(boxes, "对局评价|对局总结") or find(boxes, f"^({'|'.join(MATCH_TITLES)})$", region=RESULT_TITLE):
        return "settlement"
    # 每场战斗的结算:最终首领打完、小队生命耗尽时按钮是「前往结算」,其他是「继续挑战」(M7A 都算节点结算)
    if find(boxes, "^继续挑战$|^前往结算$") and find(boxes, "挑战成功|挑战失败|挑战结束|获得金币总览"):
        return "node_result"
    if has(boxes, "本场对局首领") and find(boxes, "^下一步$"):
        return "run_intro"
    if has(boxes, "是否中断挑战"):
        return "quit_dialog"
    # 进度文字前面常带一个被认成字母的图标,如「X1-3 0%」
    if find(boxes, "^战斗阶段$|^战斗开始$|特殊机制生效中|^(我方|敌方)行动中$|^总伤害$") or find(
            boxes, r"^\D?[1-3]-\d{1,2}\s*\d{1,3}%$"):
        return "battle"
    # 顶部 HUD 的节点号和进度被识别成两段文字(如「1-7」「0%」)
    if not has(boxes, "备战阶段") and find(boxes, r"^[1-3]-\d{1,2}$") and find(boxes, r"^\d{1,3}%$"):
        return "battle"
    if has(boxes, "^补给阶段$"):
        return "supply"
    # 备战界面上叠的面板:背景里仍有「备战阶段」,要先于备战界面判断(M7A S2 资料;开拓商店 2026-11-14 实机见过,
    # 当成备战界面时点商店只提示「该状态下不可使用」,卡住了一个节点)
    if _top(boxes, "开拓商店") and (has(boxes, "星穹列车") or has(boxes, "累计消耗解锁") or find(boxes, "^详情$")):
        return "rail_shop"
    if has(boxes, "黑市主理人") and has(boxes, "轮换商品|招牌商品"):
        return "black_market"
    if _top(boxes, "巡海游侠") and (has(boxes, "任命1名角色|任命一名角色") or find(boxes, "^确认选择$")):
        return "ranger"
    if _top(boxes, "梦境交织") and has(boxes, "进行共舞"):
        return "dance"
    if _top(boxes, "奇兽花园") and (has(boxes, "选择奇美拉") or find(boxes, "^确认选择$")):
        return "garden"
    if has(boxes, "骇入效果") and (find(boxes, "^确认选择$") or find(boxes, "^详情$")):
        return "hack"
    # 以下三个界面取自 M7A v2026.11.18(local/ref/m7a-2026.11.18.md),还没实机核对;原来都落到 other 交给导航层
    if find(boxes, "机心巧匠", region=(0.45, 0.04, 0.17, 0.07)) and (
            has(boxes, "请选择1个强化效果|请选择一个强化效果") or find(boxes, "^确认选择$")):
        return "clara_mod"
    if find(boxes, "请选择[1一]名角色作为师父", region=(0.25, 0.05, 0.50, 0.10)) or (
            find(boxes, "^从师$", region=(0.40, 0.03, 0.25, 0.10)) and (
            has(boxes, "作为师父|请选择一名角色") or find(boxes, "^确认选择$"))):
        return "mentor"
    if find(boxes, "^选择装备$", region=(0.43, 0.0, 0.21, 0.07)) and (
            has(boxes, "请选择1个|请选择一个") or find(boxes, "^查看详情$")):
        return "equipment_reward"
    if has(boxes, "替换该角色原有光环"):
        return "spotlight_replace"
    if has(boxes, "主角光环与奇遇详情"):
        return "spotlight_info"
    if has(boxes, "选择一个角色获得主角光环"):
        return "spotlight_choice"
    if has(boxes, "请选择投资策略"):
        return "strategy_choice"
    if find(boxes, "^遭遇节点$") or (has(boxes, "遭遇其") and has(boxes, "奖励预览")):
        return "encounter_choice"
    # 幕后茶歇(奇遇伙伴替换奖励节点):选项页和选项里的二级页都有「返回备战界面」
    if find(boxes, "^返回备战界面$") and find(boxes, "^(幕后茶歇|享用茶歇|醒香猫语)$|^聊一聊$|^摇一摇$"):
        return "tea_break"
    # 投资环境要在备战之前判断:「轮岗」的说明是「备战阶段开始时,使一个随机费用的刷新概率翻倍」,按子串会被认成备战
    # (2026-11-16 实机,模型先得指出「这其实是投资环境选择」)
    if find(boxes, "^投资环境$") and find(boxes, "^确认$"):
        return "environment_choice"
    if has(boxes, "备战阶段"):
        return "prepare"
    # 商店展开时会挡住顶部的「备战阶段」
    if has(boxes, "购买经验") and find(boxes, "^(商店|收起)$"):
        return "prepare"
    if has(boxes, "开始「货币战争」"):
        return "home"
    if find(boxes, "^继续进度$") and has(boxes, "当前进度"):
        return "mode_resume"
    if find(boxes, "^进入(标准|超频)博弈$"):
        return "mode_select"
    if has(boxes, "^开始对局$") and has(boxes, "敌人难度"):
        return "difficulty"
    return "other"


# 需要模型判断的界面交给哪个决策层的模型(config.yaml 的 roles;没配的用对局选的模型)
ROLES = {
    "prepare": "tactical",
    "environment_choice": "strategic", "strategy_choice": "strategic", "spotlight_choice": "strategic",
    "spotlight_replace": "strategic",
    "encounter_choice": "chooser", "supply": "chooser", "tea_break": "chooser",
    "rail_shop": "chooser", "black_market": "chooser", "ranger": "chooser", "dance": "chooser", "garden": "chooser",
    "hack": "chooser", "clara_mod": "strategic", "equipment_reward": "chooser", "mentor": "strategic",
}


def role_of(screen: str) -> str:
    return ROLES.get(screen, "navigator")   # 难度选择、没见过的界面:只是找路,用导航层(可配便宜、少思考的模型)


SCREEN_NAMES = {
    "prepare": "备战", "environment_choice": "投资环境", "strategy_choice": "投资策略", "spotlight_choice": "主角光环",
    "encounter_choice": "遭遇节点", "supply": "补给", "tea_break": "幕后茶歇", "difficulty": "难度选择",
    "spotlight_replace": "替换主角光环", "rail_shop": "开拓商店", "black_market": "黑市商店", "ranger": "巡海游侠任命",
    "dance": "梦境交织共舞", "garden": "奇兽花园", "hack": "骇入效果",
    "clara_mod": "机心巧匠改装", "mentor": "从师", "equipment_reward": "选择装备",
    "mode_resume": "继续已有对局",
    "other": "其他界面",
}


# 维护者电脑上的性能监控叠加层:最上面一行「FPS 118 Low 1% 93 CPU 61C 5294 MHz 79 W GPU … 显存 4.9G/8.0G」,
# OCR 常拆成好几段(「82 W」「##℃ ##%/##%」单独一段),所以认出关键字后整行都去掉。
# 两局实测都在 y 0.008~0.010,游戏自己的文字从 0.012(延迟)开始。2026-11-18 那局被当成开局界面的文字记进了整局已知事实
OVERLAY = re.compile(r"FPS|显存|CPU|GPU|MHz")
OVERLAY_Y = 0.012


def meaningful(boxes: list[TextBox]) -> list[TextBox]:
    """去掉 UID、延迟数字、单个字符、性能监控叠加层这些不说明界面内容的文字。"""
    top = any(b.y < OVERLAY_Y and OVERLAY.search(b.text) for b in boxes)
    return [b for b in boxes if len(b.text) > 1 and not re.match(r"^(UID|\d+ms$)", b.text)
            and not (top and b.y < OVERLAY_Y)]


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


# 备战界面的操作说明 2026-11-16 起放进系统提示词(brain/prompts/system.md),整局不变、可缓存



TEA_BREAK_HINT = """幕后茶歇(流程见规则卡):选一个选项后,选项右边会出现「确认」按钮,点确认才算选定;
之后出现帽子画面时点击空白处继续。选定之前不要点「返回」或「返回备战界面」。"""


DIFFICULTY_HINT = """目标难度:{target}(「A8」是职级分组,后面的数字是组内第几层,从 1 开始)。程序没能自动选好,请手动调。
左侧是当前选中行所在的职级组(如「A8财富造物主」);底部「敌人难度」标签上面的数字是当前的敌人难度(不是词缀数量)。
同一组里每往上一层敌人难度 +1。点列表上方的「∧」(0.5, 0.139) 往上一层,点下方的「∨」(0.5, 0.782) 往下一层(难度降低)。
找到目标组的第 1 层(再往下就换成下一个职级组),往上数到目标层,确认后再点「开始对局」。"""


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


# 备战界面上叠的面板(坐标取自 M7A S2 版,1920x1080 设计坐标换算;标「待实测」的还没在实机核对)
CLOSE_CORNER = (0.006, 0.011)    # 左上角最边上的空白:M7A 用它关闭角色详情、开拓商店、黑市
OVERLAY_HINTS = {
    "rail_shop": """开拓商店(开拓银轨羁绊的商店,盖在备战界面上;M7A S2 资料,待实测):
- 面板左上的数字(约 0.33, 0.10)是开拓力余额;商品用开拓力买,不花金币。
- 「星穹列车,跃迁!」(约 0.53, 0.35)右边的数字是它的价格,为 0 时点它免费启动,启动后才出现商品目录;
  目录每列下方有「详情」,右上「累计消耗解锁」是累计消耗开拓力解锁的进度。
- 商品不会自动刷新(维护者 2026-11-18):只有买下「全部刷新」这件商品,整个商店才换一批;买下某一件商品后,
  只有那一格换成新商品。这次不买的商品下次打开还在,不用急着买。
- 看完或不买:点左上角最边上的空白 (0.006, 0.011) 关闭面板回到备战界面。不要点左上的退出图标(会弹出中断对局)、
  也不要点左侧奇遇伙伴头像旁的 X(会打开主角光环详情)。""",
    "black_market": """黑市商店(盖在备战界面上;M7A S2 资料,待实测):
- 顶部「轮换商品」「招牌商品」两栏,每件商品下方有「详情」(约 y 0.45),看清效果和价格再决定买不买。
- 不买就点左上角最边上的空白 (0.006, 0.011) 关闭,回到备战界面。""",
    "ranger": """巡海游侠任命(M7A S2 资料,待实测):从候选角色里任命 1 名。
- 点角色名字(约 y 0.33)选中,名字下方的「详情」能看说明;再点「确认选择」(约 0.78, 0.53)。""",
    "dance": """梦境交织共舞(梦境交织羁绊,2026-11-14 实机):选 2 个角色进行共舞,增加战斗内连携效果。
- 候选角色名字约在 y 0.33,下方是「详情」;点两个角色选中(卡上方出现「已选择」),再点「确认选择」(约 0.78, 0.53)。""",
    "garden": """奇兽花园:三选一选择奇美拉(M7A S2 资料,待实测)。
- 三列中心约 x 0.33 / 0.54 / 0.76,名字约 y 0.375,上方是效果说明。点名字选中,再点「确认选择」(约 0.777, 0.555)。""",
    "hack": """骇入效果:二选一(M7A S2 资料,待实测)。
- 两列中心约 x 0.41 / 0.68,名字约 y 0.375,上方是效果说明。点名字选中,再点「确认选择」(约 0.777, 0.555)。""",
    "spotlight_replace": """这个角色已经有主角光环,确认就会替换掉原来的光环;不想替换就点「取消」。""",
    "clara_mod": """机心巧匠改装(克拉拉专属羁绊,每进 2 个新节点出一次;M7A S2 资料,待实测):3 张或 4 张强化卡选 1 张,
每张卡右上标着金币价格,有的是 0(免费);选中后点「确认选择」,确认会消耗卡面标出的金币。""",
    "mentor": """从师(主角光环选了「从师」后出现;M7A S2 资料,待实测):选 1 名角色当师父,主角受益于师父的羁绊效果。
候选卡可以横向滚动(scroll),没选时上方写着「请选择一名角色」;选中后「确认选择」才亮。""",
    "equipment_reward": """选择装备(投资策略「独家代言」等给的;M7A S2 资料,待实测):几件进阶装备选 1 件,
每张卡有「查看详情」可看效果;选完可能还要点确认。""",
}
GREY_CONFIRM = "「{text}」按钮现在是灰色的(还没选够,点了没用):先选中选项再确认。"


# 投资环境 / 投资策略三张卡的标题行(M7A S2 版坐标换算,环境标题下沿按实机收紧:标题约 y 0.36、效果说明从 0.389 起;环境标题 y 0.333~0.383,策略标题 y 0.425~0.478,三列中心 x 0.24/0.5/0.76)
INVEST_TITLE_Y = {"environment_choice": (0.333, 0.383), "strategy_choice": (0.425, 0.478)}
INVEST_X = (0.24, 0.5, 0.76)
PAIRED_INVEST_X = (514 / 1920, 1005 / 1920, 1409 / 1920)
PAIRED_INVEST_TITLE_Y = (537 / 1080, 597 / 1080)
PAIRED_INVEST_REFRESH = ((441 / 1920, 841 / 1080), (1135 / 1920, 841 / 1080))


# 投资环境开局固定 1 次刷新,刷过就是 0(维护者 2026-11-18):由程序记,不识别底部的「剩余次数」
# (那个数字是单独一位,整屏 OCR 常漏检,2026-11-18 那局只读出「剩余次数:」被当成 0 告诉了模型)
ENV_REFRESHES = 1
INVEST_REFRESH_STRAT = (0.76, 0.82)                 # 投资策略每张卡下面的「刷新次数N」(实机 y 0.791)


def paired_investment(boxes: list[TextBox], screen: str) -> bool:
    """泡泡狼的左一张、右两张布局,按顶部组标题判断。"""
    return screen == "strategy_choice" and find(boxes, "^(选择一个|两个都带走)$", region=(0.15, 0.20, 0.70, 0.08)) is not None


def invest_titles(boxes: list[TextBox], screen: str) -> list[str]:
    """三张投资卡的标题,从左到右(没读出的不算)。"""
    y0, y1 = INVEST_TITLE_Y[screen]
    out = []
    for cx in INVEST_X:
        b = next((b for b in boxes if y0 <= b.y <= y1 and abs(b.x - cx) < 0.10), None)
        if b is not None:
            out.append(b.text)
    return out


def invest_refresh(boxes: list[TextBox], screen: str) -> dict:
    """投资策略还剩几次刷新(读界面上的数字,不是猜)。

    每张卡下面各写「刷新次数N」,**刷新只换这一张**,每次进入这个界面次数重新给,不用就作废
    (维护者 2026-11-16;次数会被某些投资策略改动,所以每次都现读)。投资环境的次数不读,见 ENV_REFRESHES。
    """
    out: dict = {"cards": [None, None, None]}
    if paired_investment(boxes, screen):
        out["groups"] = []
        for columns, (x, y) in zip(((1,), (2, 3)), PAIRED_INVEST_REFRESH, strict=True):
            b = find(boxes, "刷新", region=(x + 18 / 1920, y - 31 / 1080, 158 / 1920, 62 / 1080))
            digits = re.search(r"\d+", b.text) if b is not None else None
            out["groups"].append({"columns": columns, "remaining": int(digits[0]) if digits else None})
    else:
        for i, cx in enumerate(INVEST_X):
            b = find(boxes, "刷新", region=(cx - 0.10, INVEST_REFRESH_STRAT[0], 0.20,
                                            INVEST_REFRESH_STRAT[1] - INVEST_REFRESH_STRAT[0]))
            if b is not None:
                out["cards"][i] = int(re.sub(r"\D", "", b.text) or 0)
    return out


def investment_hint(boxes: list[TextBox], screen: str, env_left: int = ENV_REFRESHES) -> str:
    """把刷新次数讲清楚交给模型:投资策略的次数由程序读,投资环境的由程序记(env_left),
    规则由这里说明,免得模型以为不能刷新或者刷错对象。"""
    if screen == "environment_choice":
        if env_left <= 0:
            return "刷新:这一屏的刷新已经用过了,只能在三张里选一张确认。"
        # 原来写「整局的次数有限」,模型理解成要给以后留着,三张一般也不刷(2026-11-16 维护者指出):
        # 投资环境整局只选这一次,剩余次数过了这个界面就作废
        return (f"刷新:还能刷新 {env_left} 次。点一次会把三张**全部**换掉(不是只换一张)。"
                "投资环境整局只在这里选一次,刷新次数过了这个界面就作废、不用留着:三张里有好的就直接选,三张都一般就刷新。")
    info = invest_refresh(boxes, screen)
    if "groups" in info:
        counts = [f"{'左侧' if i == 0 else '右侧'}刷新剩余 {g['remaining']} 次" if g["remaining"] is not None
                  else f"{'左侧' if i == 0 else '右侧'}刷新次数没读出" for i, g in enumerate(info["groups"])]
        return ("泡泡狼的投资策略布局(M7A 资料,待实测):左边「选择一个」选 1 张;右边「两个都带走」两张一起拿。"
                "右侧两张共用刷新,刷新右侧会一起更换这两张,左侧不变;左侧刷新只换左侧一张。"
                + "、".join(counts) + "。")
    if screen == "strategy_choice":
        left = [f"第{i + 1}张还剩 {n} 次" for i, n in enumerate(info["cards"]) if n]
        if not left:
            return ""
        return ("刷新:每张卡各自独立," + "、".join(left) + ";刷新**只换这一张**,其它两张不动。"
                "每次选投资策略时次数重新给,不用就作废——不合用的那张直接刷掉再看。")
    return ""


def investment_cards(boxes: list[TextBox], screen: str) -> int:
    """投资界面已经显示出标题的卡有几张。卡片是翻牌动画依次出现的,截图太快会少一张(2026-11-15 实机:第三张投资环境
    只认出了半句效果说明)。"""
    paired = paired_investment(boxes, screen)
    y0, y1 = PAIRED_INVEST_TITLE_Y if paired else INVEST_TITLE_Y[screen]
    centers = PAIRED_INVEST_X if paired else INVEST_X
    return sum(1 for cx in centers if any(abs(b.x - cx) < 0.10 and y0 <= b.y <= y1
                                            and re.search(r"[一-鿿]{2}", b.text) for b in boxes))


def trait_shop(boxes: list[TextBox]) -> str:
    """棋盘左上角羁绊专属商店(开拓银轨的开拓商店等)的入口说明;没有入口返回空串。"""
    entry = find(boxes, "^打开商店$", region=(0.0, 0.15, 0.45, 0.3))
    if entry is None:
        return ""
    near = [b.text for b in boxes if abs(b.x - entry.x) < 0.12 and abs(b.y - entry.y) < 0.06 and b is not entry]
    return ("棋盘左上角有羁绊专属商店的入口(" + "、".join(near) + ";开拓银轨的是开拓商店,用羁绊货币买),"
            "和右下角的角色商店无关" if near else "棋盘左上角有羁绊专属商店的入口,和右下角的角色商店无关")
