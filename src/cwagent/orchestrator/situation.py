"""局面要点:程序从画面读到的数字和格子算出来的事实(设计文档第 20.4 节)。只给事实,不给建议。

2026-11-16 复盘发现模型要的这些事实都得自己从坐标列表里数:同名有几张、羁绊到了哪一档、前台站了几个、
多出来没有利息的金币。数错、没数是「钱不花、前台只站一人、全是 1 星」的原因之一。
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

INTEREST_STEP, INTEREST_CAP = 10, 5        # 每 10 金币 1 利息,最多 5(规则卡,游戏内「金币说明」)
EXP_PER_BUY = 4                            # 买一次经验 +4(规则卡;有的投资策略会改)


@dataclass
class RoleFacts:
    """角色 → 羁绊,羁绊 → 各档人数。从知识库读一次。"""

    traits_of: dict[str, list[str]] = field(default_factory=dict)
    layers: dict[str, list[int]] = field(default_factory=dict)

    @classmethod
    def load(cls, db: Path) -> "RoleFacts":
        out = cls()
        if not db.exists():
            return out
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            for name, summary in con.execute("SELECT name, summary FROM roles"):
                out.traits_of[_key(name)] = list(json.loads(summary).get("traits") or [])
            for name, data in con.execute("SELECT name, data FROM traits"):
                counts = sorted({int(l["count"]) for l in json.loads(data).get("layers") or [] if l.get("count")})
                if counts:
                    out.layers[name] = counts
        except (sqlite3.Error, ValueError, KeyError):
            pass
        finally:
            con.close()
        return out


def _key(name: str) -> str:
    return re.sub(r"[·•・]", "·", name or "")


def _deployed(text: str | None) -> tuple[int, int] | None:
    m = re.fullmatch(r"(\d+)/(\d+)", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def economy(gold: int | None, exp: str | None) -> str:
    """利息、闲置金币、升级还差几次经验。"""
    if gold is None:
        return ""
    parts = [f"金币 {gold}:下个节点利息 {min(gold // INTEREST_STEP, INTEREST_CAP)}"]
    if gold > INTEREST_STEP * INTEREST_CAP:
        parts.append(f"超过 {INTEREST_STEP * INTEREST_CAP} 的 {gold - INTEREST_STEP * INTEREST_CAP} 金币不产生利息")
    else:
        to_next = INTEREST_STEP - gold % INTEREST_STEP
        if gold // INTEREST_STEP < INTEREST_CAP:
            parts.append(f"再攒 {to_next} 金币利息 +1")
    m = re.fullmatch(r"(\d+)/(\d+)", exp or "")
    if m and int(m.group(2)) > int(m.group(1)):
        need = int(m.group(2)) - int(m.group(1))
        times = math.ceil(need / EXP_PER_BUY)
        parts.append(f"升下一级还差 {need} 经验(买 {times} 次,{times * EXP_PER_BUY} 金币)")
    return ";".join(parts)


def seats(board: list, deployed: str | None, front_slots: int = 4) -> str:
    """前台站了几个、上场名额用了多少。"""
    front = sum(1 for s in board if s.zone == "front" and not s.empty)
    back = sum(1 for s in board if s.zone == "back" and not s.empty)
    text = f"前台 {front}/{front_slots}、后台 {back}"
    d = _deployed(deployed)
    if d:
        text += f";上场 {d[0]}/{d[1]}" + (f",还空 {d[1] - d[0]} 个名额" if d[1] > d[0] else "")
    return text


def copies(board: list) -> tuple[Counter, list[str]]:
    """场上 + 备战席每个角色各星级有几张(只算认出名字的角色)。返回 (Counter[(名字, 星级)], 说明行)。"""
    count: Counter = Counter()
    for s in board:
        if s.name and not s.name.startswith("道具") and s.stars:
            count[(s.name, s.stars)] += 1
    lines = []
    for name in sorted({n for n, _ in count}):
        by_star = {st: count[(name, st)] for st in (1, 2, 3) if count[(name, st)]}
        text = "、".join(f"{st}星×{n}" for st, n in sorted(by_star.items()))
        if by_star.get(1) == 2:
            text += "(再 1 张 1 星升 2 星)"
        if by_star.get(2) == 2:
            text += "(再 1 张 2 星升 3 星)"
        lines.append(f"{name} {text}")
    return count, lines


def shop_upgrades(shop: list[tuple[int, str | None, int | None]], count: Counter, two_star: set[int]) -> list[str]:
    """商店里买了就能升星的格子。two_star:价格是费用 3 倍的二星卡所在的格子。"""
    out = []
    for slot, name, _ in shop:
        if not name:
            continue
        star = 2 if slot in two_star else 1
        if count[(name, star)] == 2:
            out.append(f"商店 {slot} 号 {name}:买了就升 {star + 1} 星")
    return out


def traits(board: list, facts: RoleFacts) -> list[str]:
    """场上(前台 + 后台)的羁绊:当前人数和档位,`*` 是已激活的档。同名只算一次;星徽等装备带来的羁绊没算进去。"""
    names = {_key(s.name) for s in board if s.zone in ("front", "back") and s.name}
    count: Counter = Counter()
    for n in names:
        for t in facts.traits_of.get(n, []):
            count[t] += 1
    lines = []
    for t, c in sorted(count.items(), key=lambda kv: (-kv[1], kv[0])):
        layers = facts.layers.get(t) or []
        marks = ",".join(f"{l}*" if c >= l else str(l) for l in layers)
        nxt = next((l for l in layers if l > c), None)
        lines.append(f"{t} {c} 人" + (f"(档位 {marks})" if marks else "") + (f",下一档差 {nxt - c} 人" if nxt else ""))
    return lines


def direction(guide, board: list, shop: list[tuple[int, str | None, int | None]]) -> list[str]:
    """商店卡、备战席角色相对当前方向(参考攻略)的位置:主 C / 最终阵容 / 前中期过渡 / 不在方向里。"""
    if guide is None:
        return []
    final = guide.final_names
    carries = set(guide.carries)
    early = guide.early | guide.middle

    def kind(name: str) -> str:
        if name in carries:
            return "方向的主 C"
        if name in final:
            return "方向的最终阵容"
        if name in early:
            return "方向的前中期过渡"
        return "不在方向里"

    lines = []
    cards = [f"{slot} 号 {name}({kind(name)})" for slot, name, _ in shop if name]
    if cards:
        lines.append("商店:" + ";".join(cards))
    bench = [f"{s.index + 1} 号 {s.name}({kind(s.name)})" for s in board
             if s.zone == "bench" and s.name and not s.name.startswith("道具")]
    if bench:
        lines.append("备战席:" + ";".join(bench))
    return lines


def render(*, board: list | None, hud, shop: list | None, two_star: set[int], facts: RoleFacts | None,
           guide=None, front_slots: int = 4) -> str:
    """「局面要点」整块。各项读不出时省略。"""
    parts = []
    eco = economy(hud.gold, hud.exp)
    if eco:
        parts.append("- 经济:" + eco)
    if board:
        parts.append("- 站位:" + seats(board, hud.deployed, front_slots))
        count, lines = copies(board)
        if lines:
            parts.append("- 同名张数(场上 + 备战席):" + ";".join(lines))
        ups = shop_upgrades(shop or [], count, two_star)
        if ups:
            parts.append("- " + ";".join(ups))
        if facts is not None:
            tl = traits(board, facts)
            if tl:
                parts.append("- 羁绊(按场上角色算,星徽不计):" + ";".join(tl))
        dl = direction(guide, board, shop or [])
        if dl:
            parts.append("- 相对当前方向:" + ";".join(dl))
    return ("## 局面要点(程序算的)\n" + "\n".join(parts)) if parts else ""


def at_ready(board: list | None, hud, front_slots: int = 4, deployed: str | None = None) -> str:
    """出战时的客观数字,战斗结束后跟在本局经历的战斗那一行后面(设计文档第 20.4 节「战后」):前台人数、场上星级、
    剩余金币、上场名额。不下结论,输了以后模型自己对照。"""
    parts = []
    if board:
        field_ = [s for s in board if s.zone in ("front", "back") and not s.empty]
        front = sum(1 for s in field_ if s.zone == "front")
        stars = sum(1 for s in field_ if s.stars >= 2)
        parts.append(f"出战时前台 {front}/{front_slots},场上 {len(field_)} 人其中 2 星及以上 {stars} 个")
    if hud is not None and hud.gold is not None:
        idle = hud.gold - INTEREST_STEP * INTEREST_CAP
        parts.append(f"剩 {hud.gold} 金币" + (f"(其中 {idle} 不产生利息)" if idle > 0 else ""))
    d = _deployed(deployed or (hud.deployed if hud is not None else None))
    if d and d[1] > d[0]:
        parts.append(f"上场 {d[0]}/{d[1]},空 {d[1] - d[0]} 个名额")
    return ",".join(parts)
