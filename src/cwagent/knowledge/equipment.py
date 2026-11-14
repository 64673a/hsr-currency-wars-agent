"""装备相关的知识:合成配方、类别,以及攻略里各角色常配的装备。

给模型的是参考信息,不是指令:合成什么、给谁由模型结合装备效果、当前阵容和攻略判断(维护者要求,
不完全依赖攻略推荐)。装备效果描述走实体链接(index.py),这里只给配方和攻略统计。
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from ..vision.equipment import EquipmentReader, Item, family


class EquipmentBook:
    def __init__(self, db_path: Path):
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = con.execute("SELECT id, name, summary FROM equipment").fetchall()
            self.names = {i: n for i, n, _ in rows}
            self.categories = {i: json.loads(s).get("category", "") for i, _, s in rows}
            self.recipes: dict[tuple[str, str], str] = {}
            for product, a, b, _ in con.execute("SELECT product_id, a_id, b_id, craft_id FROM recipes"):
                if a in self.names and b in self.names and product in self.names:
                    self.recipes[tuple(sorted((self.names[a], self.names[b])))] = self.names[product]
            self.by_name = {n: i for i, n in self.names.items() if not n.endswith("·特权")}
            self._advice = self._guide_stats(con)
        finally:
            con.close()

    @staticmethod
    def _guide_stats(con) -> dict[str, tuple[Counter, int]]:
        """每个角色在攻略最终阵容里配的装备(按攻略使用次数加权)和出现的攻略篇数。"""
        stats: dict[str, Counter] = defaultdict(Counter)
        guides: Counter = Counter()
        for (data,) in con.execute("SELECT data FROM guides"):
            g = json.loads(data)
            weight = max(1, int(g.get("use_count") or 0))
            for side in ("front", "back"):
                for r in (g.get("final_board") or {}).get(side) or []:
                    if not isinstance(r, dict) or not r.get("name"):
                        continue
                    guides[r["name"]] += 1
                    for e in r.get("equipment") or []:
                        stats[r["name"]][e] += weight
        return {name: (c, guides[name]) for name, c in stats.items()}

    def reader(self, templates_dir: Path) -> EquipmentReader:
        return EquipmentReader(templates_dir, self.names, self.categories)

    def category(self, name: str) -> str:
        return self.categories.get(self.by_name.get(family(name), ""), "")

    def combos(self, items: list[Item]) -> list[tuple[int, int, str]]:
        """装备栏里两两能合成的:(编号, 编号, 成品名),同样的组合只列一次。"""
        out, seen = [], set()
        known = [i for i in items if i.name]
        for n, a in enumerate(known):
            for b in known[n + 1:]:
                pair = tuple(sorted((a.name, b.name)))
                product = self.recipes.get(pair)
                if product and pair not in seen:
                    seen.add(pair)
                    out.append((a.index, b.index, product))
        return out

    def advice(self, roles: list[str], top: int = 3) -> list[str]:
        lines = []
        for role in dict.fromkeys(roles):
            if role in self._advice:
                counter, n = self._advice[role]
                picks = "、".join(e for e, _ in counter.most_common(top))
                if picks:
                    lines.append(f"- {role}:{picks}(出现在 {n} 篇攻略的最终阵容里)")
        return lines

    def with_boxes(self, items: list[Item], boxes: dict[int, tuple[str, list[str]]]) -> list[str]:
        """缺的那一件在备战席道具里有的配方:装备栏的一件 + 道具里选一件,或两个道具各选一件。"""
        out, seen = [], set()
        have = [(f"装备栏{i.index}号", i.name) for i in items if i.name]
        offers = [(f"备战席{k}号的{title}", o) for k, (title, opts) in sorted(boxes.items()) for o in opts]
        for (wa, a), (wb, b) in [(x, y) for x in have for y in offers] + [
                (x, y) for n, x in enumerate(offers) for y in offers[n + 1:] if x[0] != y[0]]:
            product = self.recipes.get(tuple(sorted((a, b))))
            if product and (product, wa, wb) not in seen:
                seen.add((product, wa, wb))
                cat = self.category(product)
                out.append(f"{product}{f'({cat})' if cat else ''} = {a}({wa})+ {b}({wb})")
        return out

    def describe(self, items: list[Item], roles: list[str],
                 boxes: dict[int, tuple[str, list[str]]] | None = None) -> tuple[str, list[str]]:
        """给模型的装备信息,以及要附上效果描述的装备名。"""
        from ..vision.equipment import format_items

        lines = ["装备栏(右上角,编号从 1 开始):" + format_items(items)]
        combos = self.combos(items)
        if combos:
            lines.append("现在能合成:" + ";".join(
                f"{a}号+{b}号 → {p}" + (f"({self.category(p)})" if self.category(p) else "") for a, b, p in combos))
        partial = self.with_boxes(items, boxes or {})
        if partial:
            lines.append("用备战席道具里的选项能凑成的(道具可以先留着,等别的渠道拿到其中一半再开箱选另一半):\n"
                         + "\n".join(f"- {x}" for x in partial[:12]))
        advice = self.advice(roles)
        if advice:
            lines.append("攻略里这些角色常配的装备(只作参考,要结合装备效果和当前阵容判断):\n" + "\n".join(advice))
        names = [i.name for i in items if i.name] + [p for _, _, p in combos] + [x.split("(")[0].split(" =")[0]
                                                                                for x in partial[:12]]
        return "\n".join(lines), names
