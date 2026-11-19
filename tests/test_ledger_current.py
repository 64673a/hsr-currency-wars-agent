"""开场只发当前相关的条目说明和当前适用的经验,档案里留全(2026-11-18,设计文档第 20.4 节);
经验按触发条件由程序附上(2026-11-19)。"""

from cwagent.brain.ledger import RunLedger
from cwagent.brain.screen_agent import Episode, ScreenAgent
from cwagent.config import ModelEndpoint
from cwagent.knowledge.index import Entity
from cwagent.memory import Memory, Triggers
from cwagent.vision.ocr import TextBox

ENTITIES = {
    "经济严重过热": Entity("portals", "经济严重过热", "奖励节点换成超级次元扑满"),
    "能量概念股": Entity("portals", "能量概念股", "开局获得能量角色"),
    "持续伤害概念股": Entity("portals", "持续伤害概念股", "开局获得持续伤害角色"),
    "桂乃芬": Entity("roles", "桂乃芬", "1费 前后台", links=("持续伤害", "仙舟舰队")),
    "托帕&账账": Entity("roles", "托帕&账账", "1费 前后台", links=("千星商会", "追击")),
    "持续伤害": Entity("traits", "持续伤害", "深红荆棘"),
    "仙舟舰队": Entity("traits", "仙舟舰队", "星槎"),
    "千星商会": Entity("traits", "千星商会", "开启宝库"),
    "追击": Entity("traits", "追击", "KO 值"),
    "希露瓦": Entity("roles", "希露瓦", "后期主 C"),
}


class Index:
    def link(self, texts):
        return [ENTITIES[t] for t in dict.fromkeys(texts) if t in ENTITIES]

    def get(self, name):
        return [ENTITIES[name]] if name in ENTITIES else []

    def mentions(self, text):
        return [e for n, e in ENTITIES.items() if n in text]


def boxes(*texts):
    return [TextBox(t, 0.5, 0.5, 0.1, 0.03, 0.9) for t in texts]


def episode(n, screen):
    return Episode(id=n, key=(screen, n), screen=screen, node=f"1-{n}", role="tactical", max_turns=4)


def piggy():
    return Memory(kind="打法", title="抓猪要带基础伤害", content="经济严重过热时上有输出的阵容", status="approved",
                  confidence="medium", triggers=Triggers(entities=["经济严重过热"]), scope=["选择", "战术"])


def test_stale_entries_leave_the_input_but_stay_in_the_ledger():
    brain = ScreenAgent(ModelEndpoint(name="m", format="openai"), Index(), [piggy()], "")
    book = RunLedger(difficulty="A8-20")
    first, recalled = brain.opening(episode(1, "environment_choice"),
                                    boxes("能量概念股", "经济严重过热", "持续伤害概念股"), ledger=book)
    assert "奖励节点换成超级次元扑满" in first
    assert "抓猪要带基础伤害" in first and recalled == ["M1 抓猪要带基础伤害"]   # 界面上出现了触发条目
    book.environment = "持续伤害概念股"                 # 选完环境

    # 商店里有托帕,卡面上带着「千星商会」标签
    second, recalled = brain.opening(episode(2, "prepare"), boxes("托帕&账账", "千星商会"), ledger=book, names=["桂乃芬"])
    assert "开局获得持续伤害角色" in second                                  # 选的环境一直在
    assert "开局获得能量角色" not in second and "超级次元扑满" not in second    # 没选的环境退出
    assert "抓猪要带基础伤害" not in second and not recalled                  # 不再适用的经验也退出
    assert "深红荆棘" in second and "星槎" in second                          # 场上角色带羁绊
    assert "1费 前后台" in second and "KO 值" not in second and "开启宝库" not in second   # 商店角色在,羁绊不带

    book.set_direction("后期等希露瓦")
    third, _ = brain.opening(episode(3, "prepare"), boxes(), ledger=book, names=["桂乃芬"])
    assert "托帕&账账" not in third                                           # 商店里没买的不再带着
    assert "后期主 C" in third                                               # 笔记里的计划涉及的条目
    assert {"经济严重过热", "能量概念股", "托帕&账账"} <= set(book.entries)       # 档案里都还在
    assert "M1" in book.lessons and book.active == []


def test_api_observation_redescribes_entry_that_left_the_current_set():
    """API 每个回合是新对话:之前回合给过、这次开场没带的条目,回合中途又出现时要重新附上说明。"""
    brain = ScreenAgent(ModelEndpoint(name="m", format="openai"), Index(), [], "")
    book = RunLedger()
    brain.opening(episode(1, "prepare"), boxes("托帕&账账"), ledger=book)
    ep = episode(2, "prepare")
    brain.opening(ep, boxes(), ledger=book)
    text, recalled = brain.observation(ep, ["刷新商店"], boxes("托帕&账账"), screen="prepare")
    assert "## 新出现的条目" in text and "托帕&账账" in text and recalled == []


def test_observation_pushes_lesson_for_new_entry():
    """回合中途新出现的条目对上经验时附上原文(2026-11-18 那局 2-1 刷新商店后才出现试用昔涟),同一回合只给一次。"""
    cyrene = Memory(kind="机制", title="试用昔涟要戴永动机", content="不戴放不出终结技", status="approved",
                    confidence="medium", triggers=Triggers(entities=["托帕&账账"]), scope=["战术"])
    brain = ScreenAgent(ModelEndpoint(name="m", format="openai"), Index(), [cyrene], "")
    book = RunLedger()
    ep = episode(1, "prepare")
    first, recalled = brain.opening(ep, boxes(), ledger=book)
    assert "永动机" not in first and recalled == []
    text, recalled = brain.observation(ep, ["D 牌"], boxes("托帕&账账"), screen="prepare")
    assert "## 新对上的经验" in text and "不戴放不出终结技" in text and recalled == ["M1 试用昔涟要戴永动机"]
    assert book.active == ["M1"]
    again, recalled = brain.observation(ep, ["买下"], boxes("托帕&账账"), screen="prepare")
    assert "永动机" not in again and recalled == []


def test_observation_sends_only_changed_lines():
    """回合内后续轮的局面块只发和上一轮不同的行,相同的只报名字;snapshot 留完整的(2026-11-19 报告 P1-7)。"""
    from cwagent.brain.screen_agent import view_delta

    brain = ScreenAgent(ModelEndpoint(name="m", format="openai"), Index(), [], "")
    book = RunLedger()
    ep = episode(1, "prepare")
    ep.annotation = "## 截图标号\n1=出战"
    state = ("## 程序识别的局面\n节点 2-6,小队生命 100,金币 {gold}\n阵容(格子编号从左到右):\n- 前台:1号 银狼\n"
             "- 备战席:1号 {bench}\n装备栏(右上角):(空)")
    brain.opening(ep, boxes(), ledger=book, hints=state.format(gold=34, bench="火花"))
    text, _ = brain.observation(ep, ["买商店第 1 格(真珠)"], boxes(), screen="prepare",
                                hints=state.format(gold=33, bench="真珠"))
    assert "节点 2-6,小队生命 100,金币 33" in text and "阵容(格子编号从左到右):\n- 备战席:1号 真珠" in text
    assert "1号 银狼" not in text and "装备栏(右上角):(空)" not in text and "1=出战" not in text
    assert "(和上一轮相同,没再列出:前台、装备栏、截图标号)" in text
    assert "1号 银狼" in ep.snapshot and "1=出战" in ep.snapshot                # 压缩后恢复要完整的
    assert view_delta("", "a\nb") == "a\nb"                                    # 回合第一轮原样
