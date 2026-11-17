from cwagent.knowledge.affixes import AffixBook, clean_desc, valid_name
from cwagent.orchestrator.macros import affix_tooltip, companion_of_bubble
from cwagent.vision.ocr import TextBox


def test_affix_tooltip_only_reads_the_small_box():
    """2026-11-17 实机:点开「丢失幸运」时,上面首领节点的说明还开着,原来把它也当成了词条说明。"""
    affix = TextBox("丢失幸运", 0.68, 0.921, 0.05, 0.02, 0.9)
    boxes = [TextBox("1-9", 0.09, 0.672, 0.02, 0.02, 0.9),
             TextBox("进入本节点时，可为你的角色选择主角光环，为其提供独特强化，使其成为主角！", 0.44, 0.776, 0.39, 0.02, 0.9),
             TextBox("丢失幸运", 0.618, 0.841, 0.045, 0.02, 0.9),
             TextBox("我方小队的幸运一击率降低25%。", 0.67, 0.867, 0.149, 0.02, 0.9), affix]
    assert affix_tooltip(boxes, affix) == "我方小队的幸运一击率降低25%。"


def test_clean_affix_desc_and_name():
    assert clean_desc("图回敌人对1星的我方队员造成的伤害提高20%。") == "敌人对1星的我方队员造成的伤害提高20%。"
    assert clean_desc("1图进入本节点时，可为你的角色选择主角光环，为其提供独特强化，使其成为主角我方前台受到攻击后，"
                      "治疗强度降低50%。") == "我方前台受到攻击后，治疗强度降低50%。"
    assert clean_desc("0一商店中有10%概率出现【卖掉了】") == "商店中有10%概率出现【卖掉了】"
    assert not valid_name("al") and valid_name("首领强化")


def test_affix_book_cleans_old_records(tmp_path):
    local = tmp_path / "affixes.local.yaml"
    local.write_text("- name: 霸凌弱者\n  desc: 图回敌人对1星的我方队员造成的伤害提高20%。\n- name: al\n  desc: 残字\n",
                     encoding="utf-8")
    book = AffixBook(public=tmp_path / "none.yaml", local=local)
    assert book.all() == {"霸凌弱者": "敌人对1星的我方队员造成的伤害提高20%。"}


def test_companion_name_from_bubble():
    assert companion_of_bubble("汪汪丹想要保护你！ 本局奇遇：？？？")[0] == "汪汪丹"
    assert companion_of_bubble("不死狼将在【幕后茶歇】找你聊聊新的委托。")[0] == "不死狼"
    assert companion_of_bubble("你会在幕后茶歇遇到三月兔。")[0] == "三月兔"
    assert companion_of_bubble("小汪汪丹来了", known={"汪汪丹"})[0] == "汪汪丹"


def test_refresh_local_updates_knowledge(tmp_path, monkeypatch):
    """对局中读到的词条、伙伴不用重新 sync 就进知识库(维护者 2026-11-17)。"""
    import sqlite3

    from cwagent.knowledge import db, sync
    from cwagent.knowledge.build import Knowledge

    path = tmp_path / "k.sqlite"
    db.write(path, Knowledge(meta={}, entries={}, trait_layers=[], recipes=[], icons={}, warnings=[]), {})
    monkeypatch.setattr(sync, "load_affixes", lambda: [{"name": "首领强化", "desc": "首领敌人获得强化", "origin": "本机"}])
    monkeypatch.setattr(sync, "load_companions", lambda: [{"name": "汪汪丹", "desc": ""}])
    sync.refresh_local(path)
    con = sqlite3.connect(path)
    assert [r[0] for r in con.execute("SELECT name FROM affixes")] == ["首领强化"]
    assert [r[0] for r in con.execute("SELECT name FROM companions")] == ["汪汪丹"]


def test_supply_packs_and_trial(monkeypatch):
    """补给五组按位置配好角色、羁绊、试用和装备(2026-11-16 Codex 那局 1-5 的文字);装备图标匹配在实机图上另测。"""
    from PIL import Image

    from cwagent.vision import supply

    monkeypatch.setattr(supply, "_basic_templates", lambda: {})
    texts = [("试用", 0.554, 0.348), ("击破", 0.105, 0.453), ("梦境交织", 0.116, 0.487), ("大丽花", 0.089, 0.523),
             ("持续伤害", 0.485, 0.452), ("仙舟舰队", 0.486, 0.487), ("椒丘", 0.451, 0.524),
             ("量产型装甲", 0.141, 0.646), ("轮滑鞋", 0.495, 0.646)]
    boxes = [TextBox(t, x, y, 0.04, 0.02, 0.9) for t, x, y in texts]
    packs = supply.read_supply(Image.new("RGB", (960, 540)), boxes)
    assert packs[0].text().startswith("1 组 大丽花(击破、梦境交织) + 量产型装甲")
    assert packs[2].trial and packs[2].equipment == ["轮滑鞋"] and not packs[0].trial


def test_shop_trial_slots():
    from cwagent.vision.shop import format_shop, trial_slots

    boxes = [TextBox("试用", 0.444, 0.124, 0.02, 0.02, 0.9), TextBox("试用", 0.584, 0.124, 0.02, 0.02, 0.9)]
    assert trial_slots(boxes) == {2, 3}
    assert format_shop([(2, "赛飞儿", 3)], None, {2}) == "2号 赛飞儿[试用](3 金币)"


def test_guide_roster_notes():
    from cwagent.knowledge.guides import Guide, roster_notes

    g = Guide(id="x", title="【7持续伤害3奇兽花园】上分轮椅,全试用可玩", carries=[], final={}, early=set(), middle=set(),
              traits=[], environments=[], tempo=[], equip_order={}, idea="刚需雪衣满星魂。前期攒钱。", augments=[],
              use_count=0, hard=False)
    notes = roster_notes(g)
    assert "全试用可玩" in notes and "刚需雪衣满星魂" in notes and "前期攒钱" not in notes
