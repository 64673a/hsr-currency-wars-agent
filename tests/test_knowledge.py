import json
import sqlite3

import httpx
import pytest

from cwagent.knowledge.build import build
from cwagent.knowledge.clean import clean
from cwagent.knowledge.db import KnowledgeDB, write
from cwagent.knowledge.official_api import OfficialApi, OfficialApiError
from cwagent.knowledge.sync import M7A_NAMES, download_avatars, download_templates, sync
from tests.fixtures.official_sample import config, lineup

PROPS = {"ExtraLuckChance": "幸运一击率", "ExtraComboRate": "连击率", "ExtraAllDamageTypeAddedRatio1": "伤害增幅",
         "ExtraFrontPowerAddedRatio1": "前台强度", "ExtraBackPowerAddedRatio1": "后台强度"}


@pytest.mark.parametrize("raw, expected", [
    ("造成<color=#f29e38ff>100%</color>伤害。\\n下一行", "造成100%伤害。\n下一行"),
    ("30%<property type=ExtraLuckChance display=icon>。", "30%幸运一击率。"),
    ("15%<property type=ExtraLuckChance display=all>和", "15%幸运一击率和"),
    ("10%<property type=ExtraComboRate display=icon>【连击率】", "10%【连击率】"),
    ("在消耗<property type=ExtraComboRate display=icon>获得的额外回合", "在消耗连击率获得的额外回合"),
    ("获得0%<property type=ExtraAllDamageTypeAddedRatio1 display=icon>普攻/战技伤害增幅。", "获得0%普攻/战技伤害增幅。"),
    ("队员的<property type=ExtraFrontPowerAddedRatio1 display=icon><property type=ExtraBackPowerAddedRatio1 "
     "display=icon>前/后台强度提高", "队员的前/后台强度提高"),
    ("根据<variableproperty name=ExtraSummonPower display=icon>【召唤力】增强", "根据【召唤力】增强"),
    ("<textswitch cond=traitlayer param=1,4>(4)大饭店:翻倍。</textswitch>", "(4)大饭店:翻倍。"),
    ("星级:<variable name=X format=i>", "星级:(局内实时值)"),
    ('当前装备数:\xa0<variable name="">\xa0<value>/<threshold>\xa0</variable>', "当前装备数: (局内实时值)"),
    ("效果。\\n\\n<color=#b4b4b4><i>「风味」</i></color>", "效果。"),
    ("<property type=StanceBreakAddedRatio display=all>提高", "击破效率提高"),
    (None, ""),
])
def test_clean(raw, expected):
    assert clean(raw, PROPS) == expected


def test_build():
    k = build(config(), [lineup("L1", use=5), lineup("L2", use=50), lineup("old", season="1"),
                         lineup("exp", expired=True)],
              companions=[{"name": "同行者", "type": "资源", "desc": "多给金币"}])
    assert k.meta == {"season_id": "2", "sub_season_id": "1", "game_version": "4.7"}

    roles = {e.name: e.data for e in k.entries["roles"]}
    assert roles["甲"]["cost"] == 1 and roles["甲"]["position"] == "前台"
    assert roles["甲"]["skills"][0]["stars"][0]["desc"] == "造成100%伤害。"
    assert roles["乙"]["position"] == "前后台" and roles["乙"]["traits"][1]["name"] == "千星商会"

    traits = {e.name: e.data for e in k.entries["traits"]}
    combo = traits["连击"]
    assert combo["type"] == "流派" and [m["name"] for m in combo["members"]] == ["甲", "乙"]
    assert combo["layers"] == [{"count": 2, "quality": 0, "desc": "15%幸运一击率。"},
                               {"count": 4, "quality": 1, "desc": "30%。"}]
    assert combo["remarks"] == ["【连击率】:额外回合。"] and combo["desc"].startswith("【连击】队员的")
    assert traits["无人羁绊"]["unused"]
    assert k.warnings == ["羁绊「千星商会」没有档位数据(阵容接口未覆盖),需要从游戏内图鉴补充"]
    assert ("200402", 2, 0, "15%幸运一击率。") in k.trait_layers

    equip = {e.name: e.data for e in k.entries["equipment"]}
    assert equip["反重力皮靴"]["desc"] == "速度提高。"
    assert equip["反重力皮靴"]["recipes"] == [{"a_id": "3502", "a_name": "轮滑鞋", "b_id": "3502", "b_name": "轮滑鞋"}]
    assert equip["轮滑鞋"]["category"] == "简易装备" and not equip["轮滑鞋"]["listed"]
    assert equip["轮滑鞋"]["used_in"] == [{"id": "4701", "name": "反重力皮靴", "with_id": "3502", "with_name": "轮滑鞋"}]
    assert equip["翻飞之币"]["intrinsic_role"] == "乙"
    assert k.recipes == [("4701", "3502", "3502", "92")]

    aug = {e.name: e.data for e in k.entries["augments"]}
    assert aug["特战资金"]["quality_name"] == "银色" and aug["利息上调"]["quality_name"] == "棱彩"
    assert k.entries["spotlights"][0].data["suitable_roles"] == [{"id": "1001", "name": "甲"}]
    assert k.entries["companions"][0].data["name"] == "同行者"

    guides = sorted(k.entries["guides"], key=lambda e: e.sort)
    assert [g.id for g in guides] == ["L2", "L1"]  # 只留当前赛季未过期的,按使用数排序
    g = guides[0].data
    assert g["carries"] == ["甲"] and g["tempo_labels"] == ["速升8级"] and g["environments"] == ["火药味"]
    assert g["final_board"]["front"][0] == {"name": "甲", "cost": 1, "star": 2, "carry": True,
                                            "equipment": ["反重力皮靴"], "equipment2": [], "spotlights": ["蚁群效应"]}
    assert g["key_traits"] == [{"name": "连击", "count": 2, "layer": 2}]
    assert [r["name"] for r in g["example_boards"]["early"]["front"]] == ["甲"]   # 前期阵容带星级和前后台
    assert g["example_boards"]["early"]["back"] == [] and "early" in g["stage_traits"]
    assert k.icons["roles"]["1001"].endswith("r1001.png") and "1002" not in k.icons["roles_big"]


def test_db_roundtrip(tmp_path):
    path = tmp_path / "k.sqlite"
    k = build(config(), [lineup()])
    write(path, k, {"config": {"a": 1}})
    write(path, k, {"config": {"a": 2}})  # 重新同步整库替换

    db = KnowledgeDB(path)
    meta = db.meta()
    assert meta["season_id"] == "2" and meta["counts"]["roles"] == 2 and "synced_at" in meta
    total, rows = db.list("roles")
    assert total == 2 and rows[0] == {"id": "1001", "name": "甲", "cost": 1, "position": "前台",
                                      "traits": ["连击"], "tags": ["输出"], "hidden": False}
    assert db.list("roles", q="千星")[0] == 1
    assert db.get("equipment", "4701")["name"] == "反重力皮靴" and db.get("roles", "nope") is None
    assert db.find_by_name("traits", "连击")[0]["id"] == "200402"
    with sqlite3.connect(path) as con:
        assert json.loads(con.execute("SELECT json FROM raw_snapshots WHERE name='config'").fetchone()[0]) == {"a": 2}
    with pytest.raises(KeyError):
        db.list("nope")


def _api_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/config"):
        return httpx.Response(200, json={"retcode": 0, "data": config()})
    body = json.loads(request.content)
    page = int(body["page"])
    items = [lineup(f"P{page}-{i}", use=100 - page * 10 - i) for i in range(10)]
    return httpx.Response(200, json={"retcode": 0, "data": {"list": items,
                                                            "next_page_token": "t" if page < 3 else ""}})


def test_official_api_paging_and_errors():
    sleeps = []
    api = OfficialApi(http=httpx.Client(transport=httpx.MockTransport(_api_handler)), sleep=sleeps.append)
    assert len(api.lineups(max_count=25)) == 25
    assert len(api.lineups(max_count=100)) == 30  # 第 3 页没有下一页
    assert len(sleeps) >= 5 and all(s <= 1.0 for s in sleeps)  # 请求间隔不低于 1 秒

    bad = OfficialApi(http=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"retcode": -1, "message": "no"}))), sleep=lambda s: None)
    with pytest.raises(OfficialApiError, match="retcode=-1"):
        bad.config()
    changed = OfficialApi(http=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"retcode": 0, "data": {"role_list": []}}))), sleep=lambda s: None)
    with pytest.raises(OfficialApiError, match="结构变了"):
        changed.config()


def test_sync_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr("cwagent.knowledge.sync.load_companions", lambda: [])
    api = OfficialApi(http=httpx.Client(transport=httpx.MockTransport(_api_handler)), sleep=lambda s: None)
    stages = []
    result = sync(max_lineups=20, api=api, templates=False, progress=lambda s, m: stages.append(s),
                  db_path=tmp_path / "k.sqlite", export_dir=tmp_path / "export", templates_dir=tmp_path / "tpl")
    assert result.lineups_fetched == 20 and result.counts["guides"] == 20
    assert stages[0] == "config" and stages[-1] == "done"
    assert (tmp_path / "export" / "roles.yaml").read_text(encoding="utf-8").count("name: 甲") == 1
    assert KnowledgeDB(tmp_path / "k.sqlite").meta()["counts"]["guides"] == 20


def test_download_templates(tmp_path):
    def handler(request):
        if "bad" in request.url.path:
            return httpx.Response(404)
        return httpx.Response(200, content=b"PNG" + request.url.path.encode())

    http = httpx.Client(transport=httpx.MockTransport(handler))
    icons = {"roles": {"1": "https://cdn.example.com/a.png", "2": "https://cdn.example.com/bad.png"},
             "equipment": {"9": "https://cdn.example.com/x.webp"}}
    done, errors = download_templates(icons, tmp_path, http=http)
    assert done == {"roles": 1, "equipment": 1} and len(errors) == 1
    assert (tmp_path / "equipment" / "9.webp").read_bytes() == b"PNG/x.webp"
    done, _ = download_templates(icons, tmp_path, http=http)  # 已存在的跳过
    assert done == {"roles": 0, "equipment": 0}


def test_download_avatars(tmp_path):
    def handler(request):
        if str(request.url) == M7A_NAMES:
            return httpx.Response(200, json={"Sparxie": "火花", "SparxieCandyCoatedWaves": "火花·甜梦电波"})
        return httpx.Response(200, content=b"PNG" + request.url.path.encode())

    http = httpx.Client(transport=httpx.MockTransport(handler))
    done, errors = download_avatars(tmp_path, http=http)
    assert done == 2 and not errors
    assert json.loads((tmp_path / "avatars" / "names.json").read_text(encoding="utf-8"))["Sparxie"] == "火花"
    assert (tmp_path / "avatars" / "SparxieCandyCoatedWaves.png").read_bytes().endswith(b"SparxieCandyCoatedWaves.png")

    offline = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    done, errors = download_avatars(tmp_path / "x", http=offline)
    assert done == 0 and len(errors) == 1


@pytest.mark.parametrize("desc, expected", [
    ("第三位面的所有战斗节点都替换为遭遇节点。", {"改变节点"}),
    ("备战阶段开始时,使一个随机费用的刷新概率翻倍,每个备战阶段重新随机。", {"商店概率", "商店刷新"}),
    ("进入战斗节点时,直接跳过战斗并进入下一个节点,可生效2次。获得30点经验。", {"改变节点", "经验"}),
    ("最大等级变为7级,获得【财富宝钻】和32金币。", {"人口上限", "经济", "装备"}),
    ("每次进入新节点时,处在备战席最右侧3个位置的角色变为随机的同费用角色。获得2金币。", {"随机变化", "经济"}),
    ("进入首领节点时,获得7枚金币。", {"经济"}),  # 只是提到节点,不改变节点结构
])
def test_effect_tags(desc, expected):
    from cwagent.knowledge.build import FLOW_EFFECTS, effect_tags

    tags = set(effect_tags(desc))
    assert tags == expected
    assert bool(tags & FLOW_EFFECTS) == bool(expected & FLOW_EFFECTS)


def test_companions_and_flow_flags():
    k = build(config(), [], companions=[{"name": "三月兔", "flow_effects": ["奖励节点变为「幕后茶歇」"],
                                         "screens": ["tea_break"]}])
    rabbit = k.entries["companions"][0].data
    assert rabbit["flow"] and rabbit["screens"] == ["tea_break"] and not rabbit["verified"]
    aug = {e.name: e.data for e in k.entries["augments"]}
    assert aug["特战资金"]["effects"] == ["经济"] and not aug["特战资金"]["flow"]
