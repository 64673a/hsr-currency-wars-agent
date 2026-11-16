"""2026-11-15 实机对局暴露的流程问题:羁绊商店入口被当成弹窗、投资卡没翻完就识别、拖动被拒绝没察觉、敌人词条。"""

from PIL import Image

from cwagent.brain.screen_agent import Action
from cwagent.capture import Frame
from cwagent.knowledge.affixes import AffixBook
from cwagent.orchestrator.loop import overlay_texts
from cwagent.orchestrator.macros import read_affixes
from cwagent.orchestrator.prepare import PrepareExecutor
from cwagent.orchestrator.screens import investment_cards, trait_shop
from cwagent.vision.board import Slot
from cwagent.vision.ocr import TextBox


def _b(text, x, y):
    return TextBox(text, x, y, 0.05, 0.02, 0.9)


def test_trait_shop_entry_is_not_a_popup():
    boxes = [_b("打开商店", 0.328, 0.363), _b("×0", 0.36, 0.38), _b("有商品可购买", 0.30, 0.40),
             _b("当前情况下不可调整", 0.50, 0.50)]
    assert overlay_texts(boxes) == ["当前情况下不可调整"]
    assert "羁绊专属商店" in trait_shop(boxes) and "有商品可购买" in trait_shop(boxes)


def test_investment_cards():
    # 2026-11-15 实机:第三张投资环境还在翻牌,只认出半句效果说明
    early = [_b("过剩经费", 0.228, 0.363), _b("小小巨人邀请", 0.500, 0.358), _b("个投资农略是棱彩品质。", 0.780, 0.389)]
    assert investment_cards(early, "environment_choice") == 2
    done = early[:2] + [_b("连击邀请", 0.771, 0.363)]
    assert investment_cards(done, "environment_choice") == 3
    strategy = [_b("公司军火更新•金", 0.240, 0.453), _b("终结加演", 0.500, 0.453), _b("全武行", 0.760, 0.454)]
    assert investment_cards(strategy, "strategy_choice") == 3


class AffixDevice:
    """位面详情:点词条弹出说明,点左边空白收起。"""

    def __init__(self):
        self.popup = None
        self.clicks = []

    def grab(self):
        return Frame(image=Image.new("RGB", (8, 8)), timestamp=0)

    def click(self, x, y, *, button="left"):
        self.clicks.append((round(x, 3), round(y, 3)))
        self.popup = {0.461: "前台熄火"}.get(round(x, 3), self.popup) if y > 0.8 else None
        if round(x, 3) == 0.734:
            self.popup = "前台熄火"


def test_read_affixes_records_new_ones(tmp_path):
    book = AffixBook(public=tmp_path / "public.yaml", local=tmp_path / "local.yaml")
    book.record("开局不利", "开局时小队生命降低。")
    dev = AffixDevice()
    row = [_b("位面详情", 0.08, 0.07), _b("敌人难度67+3", 0.324, 0.919), _b("开局不利", 0.557, 0.92),
           _b("前台熄火", 0.734, 0.921)]

    def ocr(image, region=None):
        if dev.popup:
            return row + [_b("前台角色熄火,", 0.70, 0.80), _b("只能造成1点伤害。", 0.70, 0.83)]
        return row

    got = read_affixes(dev, ocr, row, book, log=lambda m: None)
    assert got == [("开局不利", "开局时小队生命降低。"), ("前台熄火", "前台角色熄火,只能造成1点伤害。")]
    assert dev.clicks[0] == (0.734, 0.921)          # 已知的「开局不利」不再点开
    assert book.get("前台熄火") == "前台角色熄火,只能造成1点伤害。"


class DragDevice:
    def __init__(self, refused):
        self.refused, self.drags = refused, []

    def grab(self):
        return Frame(image=Image.new("RGB", (8, 8)), timestamp=0)

    def drag(self, *a, **k):
        self.drags.append(a)


def test_refused_move_stops_the_turn():
    dev = DragDevice(refused=True)
    ocr = lambda image: [_b("当前情况下不可调整", 0.5, 0.5)]
    ex = PrepareExecutor(dev, ocr, raw=lambda a, b: "", sleep=lambda s: None)
    board = [Slot("bench", 0, 0.2285, 0.845, name="刻律德菈", score=0.9)]
    out = ex.run([Action(type="move", zone="bench", index=1, to_zone="back", to_index=2),
                  Action(type="sell", zone="bench", index=2), Action(type="ready")], [], board, final=True)
    assert out.results == ["备战席1号 → 后台2号:没成功(界面提示「当前情况下不可调整」),后面的操作先不做"]
    assert out.observe and not out.ready and len(dev.drags) == 1


# ---------------------------------------------------------------- 2026-11-16 实机

def test_read_companions_gives_up_when_page_does_not_open():
    """点左上角没打开「主角光环与奇遇详情」就放弃,不要接着在棋盘上乱点(2026-11-16 实机坐标)。"""
    clicks = []

    class Dev:
        def click(self, x, y):
            clicks.append((x, y))

        def move(self, x, y):
            pass

        def grab(self):
            return Frame(image=Image.new("RGB", (8, 8)), timestamp=0)

    from cwagent.orchestrator import macros

    assert macros.read_companions(Dev(), lambda im, region=None: [], lambda s: None) is None
    assert clicks == [macros.COMPANION_ICON]


def test_companion_bubble_drops_single_char_noise():
    """气泡右侧会蹭到头像下面名字标签的残字(实机读到过末尾多一个「狼」),单字要丢掉。"""
    from cwagent.orchestrator import macros

    boxes = [TextBox("不死狼将在【幕后茶歇】找你聊聊新的委托。", 0.40, 0.80, 0.30, 0.03, 0.9),
             TextBox("狼", 0.29, 0.885, 0.01, 0.03, 0.9)]
    text = macros._bubble(lambda im, region=None: boxes, Image.new("RGB", (8, 8)))
    assert text == "不死狼将在【幕后茶歇】找你聊聊新的委托。"


def test_refresh_points_at_button_and_reports_no_change():
    """刷新改成点商店的「刷新」按钮(原来按 D 键实机没反应),没换牌时要把原因报回去。"""
    from cwagent.orchestrator.prepare import PrepareExecutor

    clicks = []
    page = [TextBox("刷新", 0.842, 0.451, 0.05, 0.02, 0.9), TextBox("3", 0.852, 0.492, 0.02, 0.02, 0.9),
            TextBox("金币不足", 0.5, 0.5, 0.1, 0.03, 0.9)]
    #

    class Dev:
        def click(self, x, y):
            clicks.append((x, y))

        def key(self, k):
            raise AssertionError("认得出「刷新」按钮时不该按 D 键")

        def grab(self):
            return Frame(image=Image.new("RGB", (8, 8)), timestamp=0)

    ex = PrepareExecutor(Dev(), ocr=lambda im, region=None: page, raw=lambda a, b: "", sleep=lambda s: None)
    r = ex.run([Action(type="refresh")], page, None)
    assert clicks and abs(clicks[0][0] - 0.842) < 1e-6
    assert "刷新没有生效" in r.results[0] and "金币不足" in r.results[0]


def test_companion_banner_recognised_on_any_screen():
    """奇遇伙伴的横幅随时可能出现(投资策略、补给、备战),不是每局都有:认出就跳过,不要当成卡住的界面。"""
    from cwagent.orchestrator.screens import classify, companion_banner

    banner = [_b("本局奇遇", 0.50, 0.44), _b("第一位面的最后的奖励节点将替换为幕后茶歇。", 0.50, 0.505),
              _b("点击空白处继续", 0.50, 0.655)]
    # 盖在投资策略界面上
    assert classify(banner + [_b("请选择投资策略", 0.5, 0.09)]) == "tap_continue"
    # 盖在补给界面上
    assert classify(banner + [_b("补给阶段", 0.5, 0.143)]) == "tap_continue"
    # 点在提示文字本身(固定点 0.62 点不到横幅底部那条提示)
    assert companion_banner(banner) == (0.50, 0.655)
    # OCR 没读到提示文字时退回横幅中间
    assert companion_banner(banner[:2]) == (0.5, 0.655)
    # 开局介绍界面也有「本局奇遇」,但它带「下一步」,不能当横幅点掉
    intro = [_b("本局奇遇", 0.63, 0.58), _b("菇菇兔", 0.65, 0.635), _b("下一步", 0.73, 0.91)]
    assert companion_banner(intro) is None
    assert classify(intro + [_b("本场对局首领", 0.3, 0.18)]) == "run_intro"


def test_companions_read_once_after_first_plane_survey(tmp_path, monkeypatch):
    """奇遇伙伴整局只读一次,时机是第一位面收集完词条和节点之后(维护者 2026-11-16)。"""
    import cwagent.orchestrator.loop as loop
    from cwagent.orchestrator.loop import Runner, Trace
    from tests.test_loop import FakeBrain

    calls = []

    class Sim:
        state = "prep"

        def grab(self):
            return Frame(image=Image.new("RGB", (8, 8)), timestamp=0)

    monkeypatch.setattr(loop, "read_companions", lambda *a, **k: calls.append(1) or ["不死狼将在【幕后茶歇】找你聊聊新的委托。"])
    monkeypatch.setattr(loop, "survey_plane", lambda *a, **k: ["战斗", "奖励"])
    runner = Runner(Sim(), FakeBrain({}), Trace(tmp_path / "run"), ocr=lambda im, region=None: [], log=lambda m: None,
                    settle=False)
    monkeypatch.setattr(runner, "ready_prepare", lambda: (Sim().grab(), []))

    assert runner.macros(None, [], "1-2") == "收集本位面的节点序列"     # 第一位面:先收集词条和节点
    assert calls == []
    assert runner.macros(None, [], "1-2") == "读取本局奇遇伙伴"          # 收集完接着读伙伴
    assert calls == [1]
    assert any("奇遇伙伴" in f for f in runner.ledger.facts)
    runner._crystal_fails["1-2"] = 99
    runner.macros(None, [], "1-3")
    assert calls == [1]                                                  # 之后不再读


def test_open_plane_detail_waits_for_animation_then_retries():
    """节点图标还在播「获得经验」时点击会被吞(2026-11-16 实机):点完轮询等它出来,等不到再点一次。"""
    from cwagent.orchestrator import macros

    clicks = []

    class Dev:
        def click(self, x, y):
            clicks.append((x, y))

        def grab(self):
            return Frame(image=Image.new("RGB", (8, 8)), timestamp=0)

    def ocr(im, region=None):
        # 第一次点击之后一直没开(被动画吞掉),第二次点击后才打开
        return [_b("位面详情", 0.5, 0.06)] if len(clicks) >= 2 else []

    got = macros.open_plane_detail(Dev(), ocr, lambda s: None, wait=0.5)
    assert got is not None and len(clicks) == 2      # 第一次被吞,第二次成功,最多点两次


def test_companion_name_from_bubble():
    """气泡两种写法都要能抽出伙伴名(实机:不死狼在句首、三月兔在「遇到」后面)。"""
    from cwagent.orchestrator.macros import companion_of_bubble

    name, desc = companion_of_bubble("不死狼将在【幕后茶歇】找你聊聊新的委托。 第一位面的最后的奖励节点将替换为幕后茶歇。")
    assert name == "不死狼" and desc.startswith("不死狼将在")
    assert companion_of_bubble("你会在幕后茶歇遇到三月兔。")[0] == "三月兔"
    assert companion_of_bubble("第一位面的最后的奖励节点将替换为幕后茶歇。")[0] == ""


def test_invest_refresh_reads_numbers_from_ui():
    """刷新次数的规则要由程序读出来交给模型:投资环境一次全刷、投资策略各刷各的(维护者 2026-11-16)。"""
    from cwagent.orchestrator.screens import invest_refresh, investment_hint

    env = [_b("刷刷刷", 0.229, 0.363), _b("召唤邀请", 0.5, 0.363), _b("黄金时代", 0.771, 0.363),
           _b("剩余次数:1", 0.402, 0.910)]
    info = invest_refresh(env, "environment_choice")
    assert info["env"] == 1
    hint = investment_hint(env, "environment_choice")
    assert "全部" in hint and "1 次" in hint

    strat = [_b("好运令牌·金", 0.24, 0.454), _b("伙伴总动员", 0.5, 0.453), _b("奇美成长记", 0.76, 0.454),
             _b("刷新次数1", 0.248, 0.791), _b("刷新次数2", 0.508, 0.791), _b("刷新次数1", 0.768, 0.791)]
    assert invest_refresh(strat, "strategy_choice")["cards"] == [1, 2, 1]
    hint = investment_hint(strat, "strategy_choice")
    assert "只换这一张" in hint and "第2张还剩 2 次" in hint
