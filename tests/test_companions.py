from cwagent.knowledge.build import build
from cwagent.knowledge.companions import CompanionBook
from tests.fixtures.official_sample import config

PUBLIC = """# 公共档案说明

- name: 三月兔
  type: ""
  desc: ""
  flow_effects:
    - 奖励节点变为「幕后茶歇」
  screens: [tea_break]
  verified: false
  source: 维护者告知
"""


def test_record_merge_and_export(tmp_path):
    public = tmp_path / "companions.yaml"
    public.write_text(PUBLIC, encoding="utf-8")
    book = CompanionBook(public, tmp_path / "data" / "companions.local.yaml")

    entry, first = book.record("三月兔", desc="奖励节点变为幕后茶歇……", screenshot="runs/1/shots/0001.png")
    assert not first and entry["seen_count"] == 1  # 公共档案里已有
    entry, first = book.record("新伙伴", desc="旧描述", screens=["commission"])
    assert first
    entry, _ = book.record("新伙伴", desc="新描述", flow_effects=["局内出现委托"])
    assert entry["seen_count"] == 2 and entry["desc"] == "新描述" and entry["desc_history"] == ["旧描述"]

    rabbit = book.get("三月兔")
    assert rabbit["origin"] == "公共" and rabbit["desc"] == "奖励节点变为幕后茶歇……"
    assert rabbit["flow_effects"] == ["奖励节点变为「幕后茶歇」"] and rabbit["seen_count"] == 1
    assert book.get("新伙伴")["origin"] == "本机"
    assert [c["name"] for c in book.all()] == ["三月兔", "新伙伴"]

    book.export("新伙伴")
    text = public.read_text(encoding="utf-8")
    assert text.startswith("# 公共档案说明\n\n") and "新伙伴" in text and "seen_count" not in text
    assert {c["name"] for c in CompanionBook(public, tmp_path / "other.yaml").all()} == {"三月兔", "新伙伴"}


def test_companions_flow_into_knowledge(tmp_path):
    book = CompanionBook(tmp_path / "none.yaml", tmp_path / "local.yaml")
    book.record("不死狼", flow_effects=["局内出现委托"], screens=["commission"])
    k = build(config(), [], companions=book.all())
    wolf = k.entries["companions"][0].data
    assert wolf["name"] == "不死狼" and wolf["flow"] and wolf["origin"] == "本机" and wolf["seen_count"] == 1
