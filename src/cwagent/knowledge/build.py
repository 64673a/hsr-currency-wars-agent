"""把官方接口的原始数据整理成知识库条目。设计见 docs/TECH_DESIGN.md 第 5 节。

每类条目都是普通 dict,写库时整体存为 JSON;列表页需要的少量字段放在 summary 里。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .clean import clean

TRAIT_TYPES = {0: "阵营", 1: "流派", 2: "专属", 3: "特殊"}
POSITIONS = {"Front": "前台", "Back": "后台", "Common": "前后台"}
AUGMENT_QUALITIES = {1: "银色", 2: "金色", 3: "棱彩"}
GUIDE_SUMMARY_CHARS = 200
# 攻略筛选(2026-11-18 维护者定,每次同步都按这个):有使用次数的(官方推荐、玩家在游戏里套用过)全留;
# 其余要收藏够多、而且写了说明、推荐投资策略和投资环境;最终阵容完全相同的只留数据最好的一篇
GUIDE_MIN_FAVOUR = 200
CN_TZ = timezone(timedelta(hours=8))

# 效果标签:按描述关键词粗略分类,只作提示,不代替描述原文。见 docs/TECH_DESIGN.md 2.3、5.5。
EFFECT_RULES: list[tuple[str, str]] = [
    ("改变节点", r"节点.{0,12}(替换为|改为)|替换为.{0,10}节点|跳过战斗|节点改为|茶歇|委托"),
    ("商店概率", r"刷新概率|出现概率|商店中|商店里|商店会|概率出现|高\d费"),
    ("人口上限", r"最大等级|团队规模上限|团队规模\+|上阵.{0,6}上限"),
    ("随机变化", r"(备战席|物品栏).{0,24}变为"),
    ("商店刷新", r"刷新"),
    ("经济", r"金币|利息|连胜"),
    ("经验", r"经验"),
    ("装备", r"装备|武装箱|宝钻|星徽|冶金炉|好运令牌"),
    ("升星", r"升至\d星|升星|\d星|复制"),
    ("主角光环", r"主角光环"),
    ("风险", r"敌人难度|受到的伤害|小队生命值"),
]
# 会改变流程或让既定计划失效的标签:拿到时应重新规划
FLOW_EFFECTS = {"改变节点", "商店概率", "人口上限", "随机变化"}


def effect_tags(desc: str) -> list[str]:
    return [tag for tag, pattern in EFFECT_RULES if re.search(pattern, desc)]


KINDS = ("roles", "traits", "equipment", "augments", "portals", "spotlights", "companions", "affixes", "guides")
KIND_NAMES = {"roles": "角色", "traits": "羁绊", "equipment": "装备", "augments": "投资策略",
              "portals": "投资环境", "spotlights": "主角光环", "companions": "奇遇", "affixes": "敌人词条",
              "guides": "指引卡"}


@dataclass
class Entry:
    id: str
    name: str
    data: dict[str, Any]
    summary: dict[str, Any] = field(default_factory=dict)
    sort: float = 0.0
    search: str = ""


@dataclass
class Knowledge:
    meta: dict[str, str]
    entries: dict[str, list[Entry]]
    trait_layers: list[tuple[str, int, int, str]]          # trait_id, 人数, 品质, 效果
    recipes: list[tuple[str, str, str, str]]               # 产物, 原料 A, 原料 B, craft_id
    icons: dict[str, dict[str, str]]                       # 模板库:类别 -> {文件名(不含扩展名): url}
    warnings: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {k: len(v) for k, v in self.entries.items()}


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def build(config: dict[str, Any], lineups: list[dict[str, Any]],
          companions: list[dict[str, Any]] | None = None,
          affixes: list[dict[str, Any]] | None = None) -> Knowledge:
    props = {p["property_type"]: p["name"] for p in config.get("role_property_list", []) if p.get("name")}

    def c(text: str | None) -> str:
        return clean(text, props)

    warnings: list[str] = []
    icons: dict[str, dict[str, str]] = {k: {} for k in
                                        ("roles", "roles_big", "equipment", "traits", "augments", "portals",
                                         "spotlights")}
    season = str(config.get("season_id", ""))

    # ---------------------------------------------------------------- 角色
    roles: list[Entry] = []
    role_names: dict[str, str] = {}
    trait_members: dict[str, list[dict[str, Any]]] = {}
    trait_info_from_roles: dict[str, dict[str, Any]] = {}
    for r in config["role_list"]:
        rid, name = str(r["id"]), r["name"]
        cost = _int(r.get("rarity"))
        role_names[rid] = name
        traits = []
        for t in r.get("trait_details") or []:
            tid = str(t["id"])
            traits.append({"id": tid, "name": t["name"]})
            trait_members.setdefault(tid, []).append({"id": rid, "name": name, "cost": cost})
            trait_info_from_roles.setdefault(tid, {"desc": c(t.get("desc")), "icon": t.get("icon", "")})
        skills = []
        for s in r.get("skills") or []:
            skills.append({
                "position": POSITIONS.get(s.get("front_back_type"), s.get("front_back_type")),
                "name": s.get("name", ""),
                "tags": s.get("category_tags") or [],
                "stars": [{"star": st.get("star"), "desc": c(st.get("desc")),
                           "types": [x.get("desc") for x in st.get("skill_types") or []]}
                          for st in s.get("skill_stars") or []],
            })
        tags = sorted({tag for s in skills for tag in s["tags"]})
        position = POSITIONS.get(r.get("front_back_type"), r.get("front_back_type", ""))
        data = {"id": rid, "name": name, "cost": cost, "position": position,
                "hidden": bool(r.get("is_hide")), "expert": bool(r.get("is_expert")),
                "traits": traits, "skills": skills, "tags": tags,
                "icon": r.get("icon", ""), "big_icon": r.get("big_icon", "")}
        roles.append(Entry(rid, name, data,
                           summary={"cost": cost, "position": position, "traits": [t["name"] for t in traits],
                                    "tags": tags, "hidden": data["hidden"]},
                           sort=cost * 1000 + len(roles),
                           search=" ".join([name, position, *[t["name"] for t in traits], *tags])))
        if r.get("icon"):
            icons["roles"][rid] = r["icon"]
        if r.get("big_icon"):
            icons["roles_big"][rid] = r["big_icon"]

    # ---------------------------------------------------------------- 羁绊档位(从阵容合并)
    layers: dict[str, dict[int, tuple[int, str]]] = {}
    remarks: dict[str, list[str]] = {}
    for lineup in lineups:
        for stage in (lineup.get("tourn_detail") or {}).get("role_stages") or []:
            for t in stage.get("traits") or []:
                tid = str(t.get("trait_id"))
                for layer in t.get("layers") or []:
                    count = _int(layer.get("layer"))
                    layers.setdefault(tid, {}).setdefault(count, (_int(layer.get("quality")),
                                                                 c(layer.get("trait_desc"))))
                for rm in t.get("remarks") or []:
                    text = c(rm.get("remark"))
                    if text and text not in remarks.setdefault(tid, []):
                        remarks[tid].append(text)

    traits: list[Entry] = []
    trait_layers: list[tuple[str, int, int, str]] = []
    for t in config["trait_info_list"]:
        tid, name = str(t["trait_id"]), t["trait_name"]
        type_code = _int(t.get("trait_type"), -1)
        members = sorted(trait_members.get(tid, []), key=lambda m: (m["cost"], m["name"]))
        tl = [{"count": n, "quality": q, "desc": d} for n, (q, d) in sorted(layers.get(tid, {}).items())]
        for layer in tl:
            trait_layers.append((tid, layer["count"], layer["quality"], layer["desc"]))
        unused = not members  # 当前赛季没有任何角色拥有该羁绊
        if not tl and not unused:
            warnings.append(f"羁绊「{name}」没有档位数据(阵容接口未覆盖),需要从游戏内图鉴补充")
        info = trait_info_from_roles.get(tid, {})
        icon = t.get("trait_icon") or info.get("icon", "")
        data = {"id": tid, "name": name, "type": TRAIT_TYPES.get(type_code, str(type_code)),
                "desc": info.get("desc", ""), "members": members, "layers": tl,
                "remarks": remarks.get(tid, []), "unused": unused, "icon": icon}
        traits.append(Entry(tid, name, data,
                            summary={"type": data["type"], "members": len(members),
                                     "thresholds": [x["count"] for x in tl], "unused": unused},
                            sort=type_code * 1000 + len(traits),
                            search=" ".join([name, data["type"], *[m["name"] for m in members]])))
        if icon:
            icons["traits"][tid] = icon

    # ---------------------------------------------------------------- 装备与配方
    all_equip: dict[str, dict[str, Any]] = {}

    def collect(e: dict[str, Any]) -> None:
        eid = str(e["id"])
        if eid not in all_equip or (e.get("compose_list") and not all_equip[eid].get("compose_list")):
            all_equip[eid] = e
        for comp in e.get("compose_list") or []:
            for child in comp.get("childrens") or []:
                if str(child["id"]) not in all_equip:
                    collect(child)

    for e in config["equipment_list"]:
        collect(e)
    top_ids = {str(e["id"]) for e in config["equipment_list"]}
    intrinsic = {str(k): str(v) for k, v in (config.get("intrinsic_equip_role_map") or {}).items()}

    recipes: list[tuple[str, str, str, str]] = []
    used_in: dict[str, list[dict[str, str]]] = {}
    for eid, e in all_equip.items():
        for comp in e.get("compose_list") or []:
            kids = [str(ch["id"]) for ch in comp.get("childrens") or []]
            if len(kids) != 2:
                warnings.append(f"装备「{e['name']}」的配方原料不是两件:{kids}")
                continue
            recipes.append((eid, kids[0], kids[1], str(comp.get("craft_id", ""))))
            for a, b in ((kids[0], kids[1]), (kids[1], kids[0])):
                entry = {"id": eid, "name": e["name"], "with_id": b, "with_name": all_equip[b]["name"]}
                if entry not in used_in.setdefault(a, []):
                    used_in[a].append(entry)

    equipment: list[Entry] = []
    for eid, e in all_equip.items():
        category = e.get("category_name") or ""
        if category in ("", "?"):
            category = "其他"
        recipe_list = [{"a_id": a, "a_name": all_equip[a]["name"], "b_id": b, "b_name": all_equip[b]["name"]}
                       for p, a, b, _ in recipes if p == eid]
        data = {"id": eid, "name": e["name"], "category": category, "desc": c(e.get("desc")),
                "ability_name": e.get("ability_name") or "", "priority": _int(e.get("priority")),
                "properties": [{"name": p.get("name", ""), "value": p.get("num", "")}
                               for p in e.get("properties") or []],
                "recipes": recipe_list, "used_in": used_in.get(eid, []),
                "listed": eid in top_ids,  # 是否出现在官方装备列表顶层(否则只是合成原料)
                "intrinsic_role": role_names.get(intrinsic.get(eid, ""), ""),
                "icon": e.get("icon", "")}
        equipment.append(Entry(eid, e["name"], data,
                               summary={"category": category,
                                        "recipes": [f"{r['a_name']}+{r['b_name']}" for r in recipe_list]},
                               sort=data["priority"] * 1000 + len(equipment),
                               search=" ".join([e["name"], category, data["desc"]])))
        if e.get("icon"):
            icons["equipment"][eid] = e["icon"]

    # ---------------------------------------------------------------- 投资策略 / 环境 / 主角光环
    augments = []
    for a in config["fight_augment_list"]:
        aid = str(a["id"])
        quality = _int(a.get("quality"))
        desc = c(a.get("desc"))
        effects = effect_tags(desc)
        data = {"id": aid, "name": a["name"], "desc": desc, "quality": quality,
                "quality_name": AUGMENT_QUALITIES.get(quality, str(quality)), "effects": effects,
                "flow": bool(FLOW_EFFECTS & set(effects)), "icon": a.get("icon", "")}
        augments.append(Entry(aid, a["name"], data,
                              summary={"quality": data["quality_name"], "desc": desc, "effects": effects,
                                       "flow": data["flow"]},
                              sort=quality * 10000 + len(augments), search=a["name"] + " " + data["desc"]))
        if a.get("icon"):
            icons["augments"][aid] = a["icon"]

    portals = []
    for p in config["portal_list"]:
        pid = str(p["id"])
        desc = c(p.get("desc"))
        effects = effect_tags(desc)
        data = {"id": pid, "name": p["title"], "desc": desc, "effects": effects,
                "flow": bool(FLOW_EFFECTS & set(effects)), "icon": p.get("icon", "")}
        portals.append(Entry(pid, p["title"], data,
                             summary={"desc": desc, "effects": effects, "flow": data["flow"]}, sort=len(portals),
                             search=p["title"] + " " + data["desc"]))
        if p.get("icon"):
            icons["portals"][pid] = p["icon"]

    spotlights = []
    for s in config.get("spot_light_list") or []:
        sid = str(s["id"])
        tags = [t.get("name", "") for t in s.get("tag_list") or []]
        suitable = [{"id": str(r), "name": role_names.get(str(r), str(r))} for r in s.get("suitable_role_list") or []]
        desc = c(s.get("desc"))
        effects = effect_tags(desc)
        data = {"id": sid, "name": s["name"], "desc": desc, "tags": tags, "effects": effects,
                "flow": bool(FLOW_EFFECTS & set(effects)), "suitable_roles": suitable, "icon": s.get("icon", "")}
        spotlights.append(Entry(sid, s["name"], data, summary={"tags": tags, "desc": data["desc"]},
                                sort=len(spotlights), search=" ".join([s["name"], data["desc"], *tags])))
        if s.get("icon"):
            icons["spotlights"][sid] = s["icon"]

    companion_list = companion_entries(companions)
    affix_list = affix_entries(affixes)

    # ---------------------------------------------------------------- 指引卡
    guides = []
    current = [g for g in (guide_card(lineup, c, role_names) for lineup in lineups)
               if g is not None and g["season_id"] == season and not g["expired"]]   # 先去掉过期的再去重
    for card in pick_guides(current):
        guides.append(Entry(card["id"], card["title"], card,
                            summary={"use_count": card["use_count"], "favour": card["favour"],
                                     "support_hard": card["support_hard"], "emblem": card["emblem"],
                                     "sub_expired": card["sub_expired"], "version": card["game_version"],
                                     "tempo": card["tempo_labels"], "carries": card["carries"],
                                     "key_traits": [f"{t['count']}{t['name']}" for t in card["key_traits"]]},
                            sort=-card["use_count"] - card["favour"] / 1e9,
                            search=" ".join([card["title"], *card["carries"],
                                             *[t["name"] for t in card["key_traits"]], card["core_idea"]])))

    meta = {"season_id": season, "sub_season_id": str(config.get("sub_season_id", "")),
            "game_version": str(config.get("rpg_game_big_version", ""))}
    return Knowledge(
        meta=meta,
        entries={"roles": roles, "traits": traits, "equipment": equipment, "augments": augments,
                 "portals": portals, "spotlights": spotlights, "companions": companion_list,
                 "affixes": affix_list, "guides": guides},
        trait_layers=trait_layers, recipes=recipes, icons=icons, warnings=warnings,
    )


def companion_entries(companions: list[dict[str, Any]] | None) -> list[Entry]:
    """奇遇伙伴(人工整理 + 本机遇到的)→ 知识库条目。对局中读到新伙伴时也用它单独刷新这张表。"""
    out = []
    for i, comp in enumerate(companions or []):
        cid = str(comp.get("id") or i + 1)
        desc = comp.get("desc") or ""
        flow_notes = comp.get("flow_effects") or []  # 人工记录的流程影响,如新节点、新界面
        effects = effect_tags(desc + " " + " ".join(flow_notes))
        data = {"id": cid, "name": comp.get("name", ""), "type": comp.get("type", ""), "desc": desc,
                "flow_effects": flow_notes, "screens": comp.get("screens") or [], "effects": effects,
                "flow": bool(flow_notes) or bool(FLOW_EFFECTS & set(effects)),
                "verified": bool(comp.get("verified")), "source": comp.get("source", "人工整理"),
                "origin": comp.get("origin", ""), "seen_count": int(comp.get("seen_count") or 0)}
        out.append(Entry(cid, data["name"], data,
                         summary={"type": data["type"], "desc": desc or "；".join(flow_notes),
                                  "effects": effects, "flow": data["flow"], "verified": data["verified"]},
                         sort=i, search=" ".join([data["name"], data["type"], desc, *flow_notes])))
    return out


def affix_entries(affixes: list[dict[str, Any]] | None) -> list[Entry]:
    """敌人词条(对局中收集位面节点时点开读到的)→ 知识库条目。"""
    out = []
    for i, a in enumerate(affixes or []):
        desc = a.get("desc") or ""
        data = {"id": str(i + 1), "name": a.get("name", ""), "desc": desc, "origin": a.get("origin", ""),
                "source": a.get("source", ""), "seen_count": int(a.get("seen_count") or 0),
                "last_seen": a.get("last_seen", "")}
        out.append(Entry(data["id"], data["name"], data, summary={"desc": desc, "origin": data["origin"]},
                         sort=i, search=" ".join([data["name"], desc])))
    return out


def pick_guides(cards) -> list[dict[str, Any]]:
    """按 GUIDE_MIN_FAVOUR 那条规则筛攻略,再按最终阵容去重(每组留使用次数、收藏最多的)。"""
    kept = [g for g in cards if (g["use_count"] > 0 or (
        g["favour"] >= GUIDE_MIN_FAVOUR and g["description"] and g["recommended_augments"] and g["environments"]))]
    kept.sort(key=lambda g: (g["use_count"], g["favour"], g["likes"]), reverse=True)
    seen: set[tuple[str, ...]] = set()
    out = []
    for g in kept:
        board = tuple(sorted(u["name"] for side in ("front", "back") for u in g["final_board"][side]))
        if board not in seen:
            seen.add(board)
            out.append(g)
    return out


def _date(ts: Any) -> str:
    n = _int(ts)
    return datetime.fromtimestamp(n, CN_TZ).strftime("%Y-%m-%d") if n > 0 else ""


def guide_card(lineup: dict[str, Any], c, role_names: dict[str, str]) -> dict[str, Any] | None:
    td = lineup.get("tourn_detail") or {}
    stages = {s.get("stage"): s for s in td.get("role_stages") or []}
    final = stages.get("Final")
    if not final:
        return None
    interact = (lineup.get("game_data") or {})
    use_count = _int((interact.get("recent_interact") or {}).get("use")) or _int(
        (interact.get("interact") or {}).get("use"))

    def board(stage: dict[str, Any] | None, detail: bool) -> dict[str, Any]:
        if not stage:
            return {"front": [], "back": []}
        out: dict[str, Any] = {}
        for side, key in (("front", "front_roles"), ("back", "back_roles")):
            items = []
            for r in stage.get(key) or []:
                if not detail:
                    items.append(r.get("name", ""))
                    continue
                items.append({
                    "name": r.get("name", ""), "cost": _int(r.get("rarity")), "star": _int(r.get("star"), 1),
                    "carry": bool(r.get("is_carry")),
                    "equipment": [e.get("name", "") for e in r.get("first_equipments") or []],
                    "equipment2": [e.get("name", "") for e in r.get("second_equipments") or []],   # 次选装备
                    "spotlights": [s.get("name", "") for s in r.get("first_spot_lights") or []],
                })
            out[side] = items
        return out

    def stage_traits(stage: dict[str, Any] | None) -> list[dict[str, Any]]:
        out = []
        for t in (stage or {}).get("traits") or []:
            active = [_int(x.get("layer")) for x in t.get("layers") or [] if x.get("is_activated")]
            if active:
                out.append({"name": t.get("trait_name", ""), "count": _int(t.get("current_role_count")),
                            "layer": max(active)})
        return sorted(out, key=lambda t: -t["count"])

    description = (lineup.get("description") or "").strip()
    # 星徽攻略:官方 has_change_equip 标的是最终阵容的首选装备里有星徽(给角色加羁绊的装备,2026-11-18 对 300 篇核对过);
    # 只在次选装备里出现的不算
    emblems = sorted({e.get("name", "") for side in ("front_roles", "back_roles") for r in final.get(side) or []
                      for e in r.get("first_equipments") or []
                      if e.get("category_name") == "星徽" or str(e.get("name", "")).endswith("星徽")})
    first = [a.get("name", "") for a in td.get("first_fight_augments") or []]
    second = [a.get("name", "") for a in td.get("second_fight_augments") or []]
    augments = first + second
    return {
        "id": str(lineup.get("id", "")),
        "title": lineup.get("title", ""),
        "use_count": use_count,
        "likes": _int(((lineup.get("game_data") or {}).get("interact") or {}).get("like")),
        "favour": _int(((lineup.get("game_data") or {}).get("interact") or {}).get("favour")),
        "support_hard": bool(td.get("support_hard")),
        "level": td.get("lineup_level", ""),
        "tempo_labels": [l.get("text", "") for l in td.get("labels") or []],
        "environments": [p.get("title", "") for p in td.get("portals") or []],
        "core_idea": description[:GUIDE_SUMMARY_CHARS] + ("…" if len(description) > GUIDE_SUMMARY_CHARS else ""),
        "description": description,
        "carries": [role_names.get(str(r), str(r)) for r in final.get("carry_list") or []],
        "final_board": board(final, detail=True),
        "key_traits": stage_traits(final),
        "equip_order": {"basic": [e.get("name", "") for e in td.get("order_basic") or []],
                        "compose": [e.get("name", "") for e in td.get("order_compose") or []]},
        "recommended_augments": augments,
        "augments": {"first": first, "second": second},          # 官方分成两组(推荐的第一、第二次投资策略)
        # 前期、中期阵容:接口里只有角色、星级和前后台(没有装备),按最终阵容同样的结构存;
        # stage_traits 是各阶段激活的羁绊人数,给模型对照「现在该凑到几个」
        "example_boards": {"early": board(stages.get("Early"), detail=True),
                           "middle": board(stages.get("Middle"), detail=True)},
        "stage_traits": {"early": stage_traits(stages.get("Early")), "middle": stage_traits(stages.get("Middle"))},
        "share_code": td.get("share_code", ""),
        "emblem": bool(td.get("has_change_equip")) or bool(emblems),
        "emblems": emblems,
        # 版本和过期:整个赛季过期的不入库;本期(子赛季)过期的照样留,标出来、列出失效的内容
        "season_id": str(td.get("season_id", "")),
        "sub_season_id": str(td.get("sub_season_id", "")),
        "game_version": str(td.get("rpg_game_big_version", "")),
        "expired": bool(td.get("is_expired")),
        "sub_expired": bool(td.get("is_sub_expired")),
        "expired_items": [str(x.get("name") or x.get("id") or "") if isinstance(x, dict) else str(x)
                          for x in td.get("sub_season_expired_item_list") or []],
        "published": _date(lineup.get("show_time") or lineup.get("created_at")),
        "edited": _date(lineup.get("last_edit")),
        "author": lineup.get("nickname", ""),
    }
