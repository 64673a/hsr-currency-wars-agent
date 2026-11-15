"""敌人词条(位面详情底部那一排,如「前台熄火」「开局不利」):官方接口没有,对局中点开一个记一个。

- 公共档案 knowledge/affixes.yaml:随仓库发布,人工核对过(还没有时为空);
- 本机档案 data/affixes.local.yaml:收集位面节点时点开词条读到的说明(不入库)。
有的词条会左右全局方向(维护者举例:「虚数熄火」就不用虚数主 C,「能量逃逸」就不用靠能量放大招的主 C),
所以开局第一次收集节点时就读一遍,写进本局经历。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import yaml

from .. import paths

_HEADER = "# 本机读到的敌人词条(收集位面节点时点开词条自动记录,不入库)。核对后可以挪到 knowledge/affixes.yaml。\n"


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [d for d in data if isinstance(d, dict) and d.get("name")]


class AffixBook:
    def __init__(self, public: Path | None = None, local: Path | None = None):
        self.public = public or paths.root() / "knowledge" / "affixes.yaml"
        self.local = local or paths.data_dir() / "affixes.local.yaml"

    def all(self) -> dict[str, str]:
        out = {d["name"]: d.get("desc") or "" for d in _read(self.public)}
        for d in _read(self.local):
            if d.get("desc") and not out.get(d["name"]):
                out[d["name"]] = d["desc"]
        return out

    def get(self, name: str) -> str:
        return self.all().get(name, "")

    def record(self, name: str, desc: str) -> None:
        items = _read(self.local)
        entry = next((d for d in items if d["name"] == name), None)
        if entry is None:
            entry = {"name": name}
            items.append(entry)
        entry["desc"] = desc
        entry["seen_count"] = int(entry.get("seen_count") or 0) + 1
        entry["last_seen"] = time.strftime("%Y-%m-%d")
        self.local.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.local.with_suffix(".yaml.tmp")
        tmp.write_text(_HEADER + yaml.safe_dump(items, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
        os.replace(tmp, self.local)
