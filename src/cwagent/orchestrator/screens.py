"""靠界面文字判断当前是哪类界面。随采集到的界面逐步补充。"""

from __future__ import annotations

import re

from ..vision.ocr import TextBox, find, has


def _top(boxes: list[TextBox], pattern: str, y1: float = 0.12) -> bool:
    """界面标题一带(顶部居中)有没有这段文字。左侧羁绊列表里也有「奇兽花园」「开拓银轨」这类名字,要排除。"""
    return find(boxes, pattern, region=(0.28, 0.0, 0.48, y1)) is not None


def companion_banner(boxes: list[TextBox]) -> tuple[float, float] | None:
    """奇遇伙伴的介绍横幅:返回点哪里能跳过,不是横幅返回 None。

    奇遇伙伴出现时机不定(维护者 2026-11-16):那个发光的页面就是它,左边伙伴立绘,中间标题「本局奇遇」,
    正文写它带来什么,底部「点击空白处继续」(实机 y≈0.655)。它会出现任何界面上(投资策略、补给、备战),
    不是每局都有,也不用做任何选择,认出就点掉。
    OCR 没读到「点击空白处继续」时退回横幅中间(那也是空白处);开局介绍界面也有「本局奇遇」,

    但它带「下一步」,不归这里管。
    """
    if find(boxes, "^本局奇遇$") is None or find(boxes, "^下一步$") is not None:
        return None
    hint = find(boxes, "点击空白处继续")
    return (float(hint.x), float(hint.y)) if hint is not None else (0.5, 0.655)


def classify(boxes: list[TextBox]) -> str:
    # 断线提示盖在任何界面上面(M7A S2 资料)
    if has(boxes, "失去网络连接") and has(boxes, "是否尝试重新连接"):
        return "reconnect"
    if (has(boxes, "连接中断") and has(boxes, "退出游戏")) or (has(boxes, "加载失败") and has(boxes, "立即切换")):
        return "disconnected"
    # 奇遇伙伴的介绍横幅可能出现 ANY 时刻(投资策略、补给、备战……),而且不是每局都有(维护者 2026-11-16),
    # 所以在所有界面判断之前先认它,认出就点掉
    if has(boxes, "点击空白处继续") or companion_banner(boxes) is not None:
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
    if find(boxes, "^前往结算$") and find(boxes, "挑战结束"):   # 小队生命耗尽,整局失败(M7A)
        return "settlement"
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
    "spotlight_replace": "strategic",
    "encounter_choice": "chooser", "supply": "chooser", "tea_break": "chooser",
    "rail_shop": "chooser", "black_market": "chooser", "ranger": "chooser", "dance": "chooser", "garden": "chooser",
    "hack": "chooser",
}


def role_of(screen: str) -> str:
    return ROLES.get(screen, "navigator")   # 难度选择、没见过的界面:只是找路,用导航层(可配便宜、少思考的模型)


SCREEN_NAMES = {
    "prepare": "备战", "environment_choice": "投资环境", "strategy_choice": "投资策略", "spotlight_choice": "主角光环",
    "encounter_choice": "遭遇节点", "supply": "补给", "tea_break": "幕后茶歇", "difficulty": "难度选择",
    "spotlight_replace": "替换主角光环", "rail_shop": "开拓商店", "black_market": "黑市商店", "ranger": "巡海游侠任命",
    "dance": "梦境交织共舞", "garden": "奇兽花园", "hack": "骇入效果",
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
- compose:把装备栏第 item 件拖到第 onto 件上合成(程序会点合成确认)。合成后装备栏会重新排序,程序会给出新的装备栏;
  同一轮里合成之后的合成 / 装备不会执行,按新编号再发。
- equip:把装备栏第 item 件装给 zone 第 index 格的角色。
- 备战席里的「道具(可开启)」(如简易武装箱):程序已经点开看过选项(见本局档案和「用道具能凑成的」),没选。
  take:从 zone=bench 第 index 格的道具里拿名为 text 的选项;open:重新看一次选项(不选)。
  道具占一个备战席格子,但可以先留着:等别的渠道拿到某个配方的一半,再开箱选另一半。
- 合成什么、给谁:看「程序识别的局面」里的装备栏、能合成的组合和攻略参考,结合装备效果和当前阵容(谁是主 C、缺什么属性、
  羁绊差几个人)判断,不必照搬攻略;主 C 按角色条目里的「定位」和技能定,辅助角色不要当主 C、不要给他们堆输出装备;
  星徽装上就加入对应羁绊,能补齐差一个人的羁绊。
- 买谁、往哪个方向凑:参考「攻略参考」(程序按现有角色、商店和投资环境挑的),当前方向见本局档案;
  商店刷出别的攻略的主 C、或者现有阵容更贴近另一篇时可以转向,在 guide 字段填候选编号。
- ready:出战,一个备战阶段以 ready 结束。出战要单独一轮:和别的操作一起发时程序先不出战,把操作后的实际局面给你看;
  确认上场人数、站位、装备都对了再只发 ready(本阶段最后一次决策例外,前面的操作都成功才会接着出战)。
  上场、换位没成功(游戏提示「当前情况下不可调整」等)时,程序会停下来告诉你,后面的操作不做。
- 右下角「商店 / 收起」是角色商店,程序每一轮都会帮你打开,买角色用 buy。棋盘左上角写着「打开商店」「有商品可购买」的
  是羁绊专属商店(开拓银轨的开拓商店等,用羁绊货币买,不花金币),和角色商店无关,要用时才点它。
- 其他操作仍用 click / drag,坐标见「界面文字」;点角色会在右侧弹出详情。"""


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
}
GREY_CONFIRM = "「{text}」按钮现在是灰色的(还没选够,点了没用):先选中选项再确认。"


# 投资环境 / 投资策略三张卡的标题行(M7A S2 版坐标换算,环境标题下沿按实机收紧:标题约 y 0.36、效果说明从 0.389 起;环境标题 y 0.333~0.383,策略标题 y 0.425~0.478,三列中心 x 0.24/0.5/0.76)
INVEST_TITLE_Y = {"environment_choice": (0.333, 0.383), "strategy_choice": (0.425, 0.478)}
INVEST_X = (0.24, 0.5, 0.76)


INVEST_REFRESH_ENV = (0.25, 0.86, 0.45, 0.10)      # 投资环境底部那行「剩余次数:1」(实机 0.402, 0.910)
INVEST_REFRESH_STRAT = (0.76, 0.82)                 # 投资策略每张卡下面的「刷新次数N」(实机 y 0.791)


def invest_refresh(boxes: list[TextBox], screen: str) -> dict:
    """投资界面还剩几次刷新(读界面上的数字,不是猜)。

    - 投资环境:底部只有一处「剩余次数:N」,**一次刷新把三张全部换掉**,整局就这一次(维护者 2026-11-16)。
    - 投资策略:每张卡下面各写「刷新次数N」,**刷新只换这一张**,每次进入这个界面次数重新给,不用就作废
      (维护者 2026-11-16;次数会被某些投资策略改动,所以每次都现读)。
    """
    out: dict = {"env": None, "cards": [None, None, None]}
    if screen == "environment_choice":
        b = find(boxes, "剩余次数|刷新次数", region=INVEST_REFRESH_ENV)
        if b is not None:
            out["env"] = int(re.sub(r"\D", "", b.text) or 0)
    else:
        for i, cx in enumerate(INVEST_X):
            b = find(boxes, "刷新", region=(cx - 0.10, INVEST_REFRESH_STRAT[0], 0.20,
                                            INVEST_REFRESH_STRAT[1] - INVEST_REFRESH_STRAT[0]))
            if b is not None:
                out["cards"][i] = int(re.sub(r"\D", "", b.text) or 0)
    return out


def investment_hint(boxes: list[TextBox], screen: str) -> str:
    """把刷新次数讲清楚交给模型:数字由程序读,规则由这里说明,免得模型以为不能刷新或者刷错对象。"""
    info = invest_refresh(boxes, screen)
    if screen == "environment_choice" and info["env"] is not None:
        if info["env"] <= 0:
            return "刷新:这一屏的「剩余次数」已经是 0,只能在三张里选一张确认。"
        return (f"刷新:底部还剩 {info['env']} 次「剩余次数」。点一次会把三张**全部**换掉(不是只换一张),"
                "整局的次数有限;三张都不合用、或者只有一张能用时才刷。")
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
    y0, y1 = INVEST_TITLE_Y[screen]
    return sum(1 for cx in INVEST_X if any(abs(b.x - cx) < 0.12 and y0 <= b.y <= y1
                                            and re.search(r"[一-鿿]{2}", b.text) for b in boxes))


def trait_shop(boxes: list[TextBox]) -> str:
    """棋盘左上角羁绊专属商店(开拓银轨的开拓商店等)的入口说明;没有入口返回空串。"""
    entry = find(boxes, "^打开商店$", region=(0.0, 0.15, 0.45, 0.3))
    if entry is None:
        return ""
    near = [b.text for b in boxes if abs(b.x - entry.x) < 0.12 and abs(b.y - entry.y) < 0.06 and b is not entry]
    return ("棋盘左上角有羁绊专属商店的入口(" + "、".join(near) + ";开拓银轨的是开拓商店,用羁绊货币买),"
            "和右下角的角色商店无关" if near else "棋盘左上角有羁绊专属商店的入口,和右下角的角色商店无关")
