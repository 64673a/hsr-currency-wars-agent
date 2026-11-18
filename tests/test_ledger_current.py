"""开场只发当前相关的条目说明和当前适用的经验,档案里留全(2026-11-18,设计文档第 20.4 节)。"""

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
    assert "抓猪要带基础伤害" not in first and not recalled          # 经验由模型自己查(2026-11-18),开场不附
    book.environment = "持续伤害概念股"                 # 选完环境

    # 商店里有托帕,卡面上带着「千星商会」标签
    second, recalled = brain.opening(episode(2, "prepare"), boxes("托帕&账账", "千星商会"), ledger=book, names=["桂乃芬"])
    assert "开局获得持续伤害角色" in second                                  # 选的环境一直在
    assert "开局获得能量角色" not in second and "超级次元扑满" not in second    # 没选的环境退出
    assert "深红荆棘" in second and "星槎" in second                          # 场上角色带羁绊
    assert "1费 前后台" in second and "KO 值" not in second and "开启宝库" not in second   # 商店角色在,羁绊不带

    book.set_direction("后期等希露瓦")
    third, _ = brain.opening(episode(3, "prepare"), boxes(), ledger=book, names=["桂乃芬"])
    assert "托帕&账账" not in third                                           # 商店里没买的不再带着
    assert "后期主 C" in third                                               # 笔记里的计划涉及的条目
    assert {"经济严重过热", "能量概念股", "托帕&账账"} <= set(book.entries)       # 档案里都还在


def test_api_observation_redescribes_entry_that_left_the_current_set():
    """API 每个回合是新对话:之前回合给过、这次开场没带的条目,回合中途又出现时要重新附上说明。"""
    brain = ScreenAgent(ModelEndpoint(name="m", format="openai"), Index(), [], "")
    book = RunLedger()
    brain.opening(episode(1, "prepare"), boxes("托帕&账账"), ledger=book)
    ep = episode(2, "prepare")
    brain.opening(ep, boxes(), ledger=book)
    text = brain.observation(ep, ["刷新商店"], boxes("托帕&账账"), screen="prepare")
    assert "## 新出现的条目" in text and "托帕&账账" in text
