from cwagent.brain.ledger import MAX_LINE, RunLedger


def test_render_delta_sends_state_only_when_changed():
    book = RunLedger(difficulty="A8-1")
    book.guide = "攻略卡片" * 50
    first = book.render_delta()
    assert "攻略卡片" in first and "我的笔记" in first
    second = book.render_delta()
    assert "攻略卡片" not in second and "(和之前相同)" in second
    book.set_direction("主 C 景元")
    third = book.render_delta()
    assert "主 C 景元" in third and "攻略卡片" not in third


def test_entries_delta_sends_current_ones_not_received_yet():
    book = RunLedger()
    book.render_delta()
    book.add_entry("景元", "- [角色] 景元:……")
    book.relevant = ["景元"]
    assert "景元" in book.render_delta()
    assert "景元" not in book.render_delta()        # 收到过就不再发
    book.add_entry("白露", "- [角色] 白露:……")
    book.delivered_entries.add("白露")              # 回合中途已经随观察发过(episode.seen 和它是同一份)
    book.relevant = ["白露"]
    assert "白露" not in book.render_delta()


def test_equip_cap_and_unequip():
    book = RunLedger()
    for item in ("引擎", "手册", "引擎", "电锯"):
        book.equip("景元", item)
    assert book.equipped["景元"] == ["手册", "引擎", "电锯"]    # 一个角色最多 3 件,多出来说明前面的过期了
    book.equip("白露", "花")
    book.unequip("景元")
    assert list(book.equipped) == ["白露"]
    book.unequip()
    assert not book.equipped and "程序给角色穿上的装备" not in book.render_state()


def test_history_line_keeps_summary():
    from cwagent.orchestrator.loop import _tail

    ops = "买商店1号;" * 200
    assert len(_tail(ops, 100)) == 100 and _tail("短", 100) == "短"
    assert MAX_LINE >= 600


def test_close_episode_keeps_summary_with_long_ops(tmp_path):
    """Codex 2026-11-16 复现:操作和阵容很长时,整条截到 600 字把末尾的回合小结截没了。"""
    from cwagent.brain.screen_agent import Episode
    from cwagent.orchestrator.loop import Runner, Trace

    from .test_loop import FakeBrain

    r = Runner(None, FakeBrain({}), Trace(tmp_path / "run"), ocr=object(), log=lambda m: None, settle=False)
    r.episode = Episode(id=1, key=("prepare", "1-3"), screen="prepare", node="1-3", role="tactical", max_turns=4)
    r.episode.done = [f"买商店{i % 5 + 1}号:成功,花了 3 金币" for i in range(60)]
    r.episode.summary = "这一回合补前台,下回合升 5 级"
    r.ledger.node("1-3").lineup = "前台 " + "、".join(["景元"] * 30)
    r.close_episode()
    line = r.ledger.history[-1]
    assert len(line) <= MAX_LINE and line.endswith("小结:这一回合补前台,下回合升 5 级") and "操作:…" in line


def test_hack_replace_takes_old_mod_off(tmp_path):
    """改选骇入效果(「确认更换」)时银狼身上原来的骇客改件被换下,新的进装备栏(2026-11-19 那局 1-8、3-2)。"""
    from cwagent.brain.screen_agent import Episode
    from cwagent.orchestrator.loop import Runner, Trace

    from .test_loop import FakeBrain

    r = Runner(None, FakeBrain({}), Trace(tmp_path / "run"), ocr=object(), log=lambda m: None, settle=False)
    for item in ("骇客改件", "折叠小刀"):
        r.ledger.equip("银狼LV.999", item)
    r.episode = Episode(id=1, key=("hack", "1-1"), screen="hack", node="1-1", role="chooser", max_turns=4)
    r.episode.done = ["点击「数据拷贝仪LV.1」", "点击「确认选择」"]
    r.close_episode()
    assert r.ledger.equipped["银狼LV.999"] == ["骇客改件", "折叠小刀"]      # 首次选择不动
    r.episode = Episode(id=2, key=("hack", "1-8"), screen="hack", node="1-8", role="chooser", max_turns=4)
    r.episode.done = ["点击「数据拷贝仪LV.2」", "点击「确认更换」"]
    r.close_episode()
    assert r.ledger.equipped["银狼LV.999"] == ["折叠小刀"]


def test_drag_item_clears_target_equipment(tmp_path):
    from cwagent.brain.screen_agent import Action
    from cwagent.orchestrator.loop import Runner, Trace
    from cwagent.vision.board import Slot
    from cwagent.vision.items import Item

    from .test_loop import FakeBrain

    class Dev:
        def drag(self, *a, **k):
            pass

    r = Runner(Dev(), FakeBrain({}), Trace(tmp_path / "run"), ocr=object(), log=lambda m: None, settle=False)
    r._items = [Item(1, 350101, "拆装扳手", 2, 0.961, 0.152, 0.9), Item(2, 350105, "员工投影仪", 1, 0.921, 0.152, 0.9)]
    r._board = [Slot("front", 0, 0.41, 0.45, name="景元", score=0.9, stars=1),
                Slot("front", 1, 0.47, 0.45, name="白露", score=0.9, stars=1)]
    r.ledger.equip("景元", "引擎")
    r.ledger.equip("白露", "花")
    text = r._raw(Action(type="drag", x=0.961, y=0.152, x2=0.41, y2=0.45), [])
    assert text.startswith("把道具「拆装扳手」拖到 前台1号 景元") and list(r.ledger.equipped) == ["白露"]
    text = r._raw(Action(type="drag", x=0.921, y=0.152, x2=0.47, y2=0.45), [])
    assert "员工投影仪" in text and list(r.ledger.equipped) == ["白露"]   # 投影仪不动装备
