"""奇遇伙伴档案:边玩边完善。设计见 docs/TECH_DESIGN.md 2.3 原则 6。

- 公共档案 knowledge/companions.yaml:随仓库发布,人工核对过;
- 本机档案 data/companions.local.yaml:对局中遇到一个记一个(不入库)。
两处同名时合并:本机的描述、遇到次数等覆盖公共档案中的空字段,流程影响和界面取并集。
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

import yaml

from .. import paths

MAX_SCREENSHOTS = 5
_HEADER_LOCAL = "# 本机遇到的奇遇伙伴(自动记录,不入库)。核对后用 `cwagent companions export <名称>` 导出到公共档案。\n"


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if isinstance(data, dict):
        data = data.get("companions", [])
    return [d for d in data if isinstance(d, dict) and d.get("name")]


def _header(path: Path) -> str:
    if not path.exists():
        return ""
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or (lines and not line.strip()):
            lines.append(line)
        else:
            break
    return "\n".join(lines).rstrip() + "\n\n" if lines else ""


def _write(path: Path, items: list[dict[str, Any]], header: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(header + yaml.safe_dump(items, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    os.replace(tmp, path)


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in extra.items():
        if key in ("flow_effects", "screens"):
            out[key] = list(dict.fromkeys([*(out.get(key) or []), *(value or [])]))
        elif value not in (None, "", []) and out.get(key) in (None, "", []):
            out[key] = value
        elif key in ("seen_count", "last_seen", "screenshots"):
            out[key] = value
    return out


class CompanionBook:
    def __init__(self, public_path: Path | None = None, local_path: Path | None = None):
        self.public_path = public_path or paths.companions_file()
        self.local_path = local_path or paths.data_dir() / "companions.local.yaml"
        self._lock = threading.RLock()

    def all(self) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for item in _read(self.public_path):
            merged[item["name"]] = {**item, "origin": "公共"}
        for item in _read(self.local_path):
            name = item["name"]
            merged[name] = _merge(merged[name], item) if name in merged else {**item, "origin": "本机"}
        return list(merged.values())

    def get(self, name: str) -> dict[str, Any] | None:
        return next((c for c in self.all() if c["name"] == name), None)

    def record(self, name: str, *, desc: str = "", type: str = "", flow_effects: list[str] | None = None,
               screens: list[str] | None = None, screenshot: str = "") -> tuple[dict[str, Any], bool]:
        """记一次遇到某伙伴。返回 (本机条目, 是否第一次遇到)。描述与已记录的不同时保留新描述,旧的存进 desc_history。"""
        name = name.strip()
        if not name:
            raise ValueError("伙伴名称不能为空")
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        with self._lock:
            items = _read(self.local_path)
            entry = next((i for i in items if i["name"] == name), None)
            first = entry is None and not any(c["name"] == name for c in _read(self.public_path))
            if entry is None:
                entry = {"name": name, "type": type, "desc": desc, "flow_effects": [], "screens": [],
                         "verified": False, "source": "对局中自动记录", "first_seen": stamp, "seen_count": 0}
                items.append(entry)
            if desc and entry.get("desc") and desc != entry["desc"]:
                entry.setdefault("desc_history", []).append(entry["desc"])
            if desc:
                entry["desc"] = desc
            if type and not entry.get("type"):
                entry["type"] = type
            entry["flow_effects"] = list(dict.fromkeys([*(entry.get("flow_effects") or []), *(flow_effects or [])]))
            entry["screens"] = list(dict.fromkeys([*(entry.get("screens") or []), *(screens or [])]))
            entry["seen_count"] = int(entry.get("seen_count", 0)) + 1
            entry["last_seen"] = stamp
            if screenshot:
                entry["screenshots"] = ([*(entry.get("screenshots") or []), screenshot])[-MAX_SCREENSHOTS:]
            _write(self.local_path, items, _header(self.local_path) or _HEADER_LOCAL)
            return entry, first

    def export(self, name: str) -> Path:
        """把合并后的伙伴条目写进公共档案(同名替换),去掉本机才有意义的字段。"""
        with self._lock:
            merged = self.get(name)
            if merged is None:
                raise KeyError(name)
            keep = ("name", "type", "desc", "flow_effects", "screens", "verified", "source")
            entry = {k: merged.get(k) for k in keep if merged.get(k) not in (None, "")}
            entry.setdefault("flow_effects", [])
            entry.setdefault("screens", [])
            items = [i for i in _read(self.public_path) if i["name"] != name] + [entry]
            _write(self.public_path, items, _header(self.public_path))
            return self.public_path
