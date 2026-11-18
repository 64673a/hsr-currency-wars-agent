"""构造的官方接口样本(结构与 2026-11 实测一致,内容是编的),用于知识库测试。"""

ICON = "https://cdn.example.com/icons/{}.png"


def config():
    return {
        "season_id": "2", "sub_season_id": "1", "rpg_game_big_version": "4.7",
        "role_property_list": [
            {"property_type": "ExtraLuckChance", "name": "幸运一击率"},
            {"property_type": "ExtraAttackAddedRatio1", "name": ""},
        ],
        "role_list": [
            {"id": "1001", "name": "甲", "rarity": "1", "front_back_type": "Front", "is_hide": False,
             "is_expert": False, "icon": ICON.format("r1001"), "big_icon": ICON.format("rb1001"),
             "trait_details": [{"id": "200402", "name": "连击", "icon": ICON.format("t200402"),
                                "desc": "【连击】队员的<property type=ExtraLuckChance display=icon>提高。"}],
             "skills": [{"front_back_type": "Front", "name": "斩", "category_tags": ["输出"],
                         "skill_stars": [{"star": 1, "desc": "造成<color=#f29e38ff>100%</color>伤害。",
                                          "skill_types": [{"desc": "战技"}]}]}]},
            {"id": "1002", "name": "乙", "rarity": "3", "front_back_type": "Common", "is_hide": False,
             "is_expert": False, "icon": ICON.format("r1002"), "big_icon": "",
             "trait_details": [{"id": "200402", "name": "连击", "icon": "", "desc": "x"},
                               {"id": "100602", "name": "千星商会", "icon": "", "desc": "经济。"}],
             "skills": []},
        ],
        "trait_info_list": [
            {"trait_id": "200402", "trait_name": "连击", "trait_type": 1, "layers": [], "role_ids": []},
            {"trait_id": "100602", "trait_name": "千星商会", "trait_type": 0, "layers": [], "role_ids": []},
            {"trait_id": "300999", "trait_name": "无人羁绊", "trait_type": 3, "layers": [], "role_ids": []},
        ],
        "equipment_list": [
            {"id": "4701", "name": "反重力皮靴", "category_name": "进阶装备", "priority": "2",
             "desc": "速度提高。\\n\\n<color=#b4b4b4><i>「风味文本」</i></color>", "icon": ICON.format("e4701"),
             "properties": [{"name": "速度增幅", "num": "25%"}],
             "compose_list": [{"craft_id": "92", "childrens": [
                 {"id": "3502", "name": "轮滑鞋", "category_name": "简易装备", "priority": "1", "desc": "",
                  "icon": ICON.format("e3502"), "properties": [], "compose_list": []},
                 {"id": "3502", "name": "轮滑鞋", "category_name": "简易装备", "priority": "1", "desc": "",
                  "icon": ICON.format("e3502"), "properties": [], "compose_list": []}]}]},
            {"id": "3513", "name": "翻飞之币", "category_name": "固有装备", "priority": "5", "desc": "币。",
             "icon": "", "properties": [], "compose_list": []},
        ],
        "intrinsic_equip_role_map": {"3513": "1002"},
        "fight_augment_list": [
            {"id": "100101", "name": "特战资金", "desc": "进入首领节点时,获得7枚金币。", "quality": 1, "icon": ""},
            {"id": "300101", "name": "利息上调", "desc": "获得25金币。", "quality": 3, "icon": ""},
        ],
        "portal_list": [{"id": "101", "title": "火药味", "desc": "开局时获得2个简易武装箱。", "icon": ""}],
        "spot_light_list": [{"id": "100000", "name": "蚁群效应", "desc": "1费更强。",
                             "tag_list": [{"name": "1费"}], "suitable_role_list": ["1001"], "icon": ""}],
    }


def lineup(lid="L1", *, use=100, season="2", expired=False, favour=0, description="前期甲,后期乙。", extra=None,
           emblem=False, sub_expired=False):
    """一套官方阵容。extra:最终阵容后台再加一个这个名字的角色(让阵容各不相同,同样阵容的会去重)。"""
    trait = {"trait_id": "200402", "trait_name": "连击", "trait_type": 1, "current_role_count": 2,
             "layers": [{"layer": 2, "quality": 0, "is_activated": True,
                         "trait_desc": "15%<property type=ExtraLuckChance display=all>。"},
                        {"layer": 4, "quality": 1, "is_activated": False, "trait_desc": "30%。"}],
             "remarks": [{"remark": "【连击率】:额外回合。"}]}
    equips = [{"name": "反重力皮靴"}] + ([{"name": "连击星徽", "category": "3", "category_name": "星徽"}] if emblem else [])
    role = {"id": "1001", "name": "甲", "rarity": "1", "star": 2, "is_carry": True,
            "first_equipments": equips, "first_spot_lights": [{"name": "蚁群效应"}]}
    back = [{"id": "1002", "name": "乙", "rarity": "3"}] + ([{"id": "1099", "name": extra, "rarity": "1"}] if extra else [])
    return {
        "id": lid, "title": f"阵容{lid}", "description": description, "nickname": "作者",
        "show_time": "1794343407", "last_edit": "1793255588",
        "game_data": {"interact": {"like": "10", "favour": str(favour)}, "recent_interact": {"use": str(use)}},
        "tourn_detail": {
            "season_id": season, "sub_season_id": "1", "is_expired": expired, "is_sub_expired": sub_expired,
            "sub_season_expired_item_list": [{"id": "1", "name": "旧装备"}] if sub_expired else [],
            "has_change_equip": emblem, "rpg_game_big_version": "4.7", "share_code": "CODE",
            "support_hard": True, "lineup_level": "LineupLevelAdvanced",
            "labels": [{"text": "速升8级"}], "portals": [{"title": "火药味"}],
            "first_fight_augments": [{"name": "特战资金"}], "second_fight_augments": [],
            "order_basic": [{"name": "轮滑鞋"}], "order_compose": [{"name": "反重力皮靴"}],
            "role_stages": [
                {"stage": "Early", "front_roles": [role], "back_roles": [], "carry_list": [], "traits": []},
                {"stage": "Final", "front_roles": [role], "back_roles": back,
                 "carry_list": ["1001"], "traits": [trait]},
            ],
        },
    }
