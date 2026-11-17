import pytest

from cwagent.memory import (
    Cues,
    Memory,
    MemoryError,
    MemoryStore,
    Triggers,
    format_for_prompt,
    recall,
)
from cwagent.memory.retrieve import score


def mem(title, *, entities=(), nodes=(), screens=(), status="approved", confidence="medium", scope=(), **kw):
    return Memory(kind="打法", title=title, content=title + "的说明", status=status, confidence=confidence,
                  triggers=Triggers(entities=list(entities), nodes=list(nodes), screens=list(screens)),
                  scope=list(scope), **kw)


def test_recall_ranking_and_filters():
    piggy = mem("抓猪", entities=["经济严重过热", "次元扑满"], nodes=["reward"])
    feiying = mem("绯英叠层", entities=["绯英"], nodes=["reward"], scope=["战术"])
    reward = mem("奖励节点规则", nodes=["reward"], confidence="high")
    generic = mem("通用原则")
    draft = mem("草稿", entities=["绯英"], status="draft")
    rejected = mem("驳回的", entities=["绯英"], status="rejected")
    pool = [piggy, feiying, reward, generic, draft, rejected]

    cues = Cues(entities={"绯英", "经济严重过热"}, nodes={"reward"}, layer="战术")
    got = recall(pool, cues)
    assert [r.memory.title for r in got] == ["抓猪", "绯英叠层", "奖励节点规则", "通用原则"]
    assert got[0].reasons == ["经济严重过热", "节点:reward"]

    # 选择类决策:绯英那条只限战术层
    got = recall(pool, Cues(entities={"绯英", "经济严重过热"}, layer="选择"))
    assert [r.memory.title for r in got] == ["抓猪", "通用原则"]
    # 子串匹配自由文本(节点名、事件文字)
    got = recall(pool, Cues(texts=["超级次元扑满来袭"], layer="战术"))
    assert got[0].memory.title == "抓猪"
    # 草稿默认不注入,打开后分数靠后
    got = recall(pool, Cues(entities={"绯英"}, layer="事件"), include_draft=True)
    assert [r.memory.title for r in got] == ["草稿", "通用原则"]


def test_recall_votes_limit_and_budget():
    good = mem("被证明有用", entities=["X"], stats={"helped": 5, "hurt": 0})
    bad = mem("被证明有害", entities=["X"], stats={"helped": 0, "hurt": 5})
    assert score(good, Cues(entities={"X"})).score > score(bad, Cues(entities={"X"})).score
    many = [mem(f"m{i}", entities=["X"]) for i in range(10)]
    assert len(recall(many, Cues(entities={"X"}), limit=3)) == 3
    long = [mem("长" * 900, entities=["X"]), mem("长" * 900, entities=["X"])]
    assert len(recall(long, Cues(entities={"X"}), max_chars=1500)) == 1  # 第一条总会放进去


def test_format_for_prompt():
    text = format_for_prompt(recall([mem("抓猪", entities=["扑满"], source="维护者")], Cues(entities={"扑满"})))
    assert "[打法|已采纳|有依据未核实|来源:维护者] 抓猪" in text
    assert format_for_prompt([]) == ""


def test_store_public_local_and_export(tmp_path):
    public = tmp_path / "memories.yaml"
    public.write_text("# 公共记忆\n# 第二行\n\n- id: m-pub\n  kind: 机制\n  title: 公共\n  content: 公共内容\n"
                      "  status: approved\n", encoding="utf-8")
    store = MemoryStore(public, tmp_path / "memory.sqlite")
    assert [m.id for m, where in store.all()] == ["m-pub"]

    local = store.add(mem("本机学到的", entities=["绯英"], status="draft", id="m-local"))
    with pytest.raises(MemoryError):
        store.add(local)
    store.set_status("m-local", "approved")
    store.vote("m-local", helped=True, evidence="run-1/step-3")
    store.vote("m-local", helped=False)
    store.mark_injected(["m-local", "m-local", "nope"])
    m = store.get("m-local")
    assert m.status == "approved" and m.stats.helped == 1 and m.stats.hurt == 1 and m.stats.injected == 2
    assert m.evidence == ["run-1/step-3"]

    # 修改公共记忆:写到本机库,覆盖公共版本
    store.set_status("m-pub", "archived")
    assert dict((x.id, where) for x, where in store.all())["m-pub"] == "本机"

    store.export("m-local")
    text = public.read_text(encoding="utf-8")
    assert text.startswith("# 公共记忆\n# 第二行\n\n") and "本机学到的" in text and "injected" not in text
    assert {x.id for x in MemoryStore(public, tmp_path / "other.sqlite").public()} == {"m-pub", "m-local"}


def test_repo_memories_file_is_valid(tmp_path):
    memories = MemoryStore(local_path=tmp_path / "memory.sqlite").public()
    assert len(memories) >= 3 and all(m.status == "approved" for m in memories)
    cues = Cues(entities={"经济严重过热", "绯英"}, nodes={"reward"}, layer="战术")
    titles = [r.memory.title for r in recall(memories, cues)]
    assert any("扑满" in t for t in titles) and any("绯英" in t for t in titles)


def test_reward_alone_does_not_recall_specific_builds(tmp_path):
    memories = MemoryStore(local_path=tmp_path / "memory.sqlite").public()
    hits = recall(memories, Cues(nodes={"reward"}, layer="战术"))
    assert any(r.memory.id == "m-20261114-reward-rules" for r in hits)
    assert not any(r.memory.id in {"m-20261114-piggy-bank", "m-20261116-piggy-chase", "m-20261114-feiying-stack"}
                   for r in hits)
