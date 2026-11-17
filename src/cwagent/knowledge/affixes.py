"""敌人词条(位面详情底部那一排,如「前台熄火」「开局不利」):官方接口没有,对局中点开一个记一个。

- 公共档案 knowledge/affixes.yaml:随仓库发布,人工核对过(还没有时为空);
- 本机档案 data/affixes.local.yaml:收集位面节点时点开词条读到的说明(不入库)。
有的词条会左右全局方向(维护者举例:「虚数熄火」就不用虚数主 C,「能量逃逸」就不用靠能量放大招的主 C),
所以开局第一次收集节点时就读一遍,写进本局经历。
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import yaml

from .. import paths

_HEADER = "# 本机读到的敌人词条(收集位面节点时点开词条自动记录,不入库)。核对后可以挪到 knowledge/affixes.yaml。\n"


# 位面详情下方首领节点的说明;原来点开词条后把新出现的文字都当说明,这句常混进来(2026-11-17 本机档案里 3 条)
_NODE_NOTE = re.compile(r"进入本节点时[,，]可为你的角色选择主角光环[,，]为其提供独特强化[,，]使其成为主角[!！]?")
_TRUNCATED_NOTE = re.compile(r"进入本节点时[,，]\s*可为你的(?:角色选择主角光环|角色选择|角色|角)?[.……]*"
                             r"(?=我方|敌人|敌方|首领|普通|精英|商店|刷新|战斗|每|当|第|在|所有|全体|【|$)")


def valid_name(name: str) -> bool:
    """词条名至少两个汉字(OCR 残字如「al」不算)。"""
    return len(re.findall(r"[\u4e00-\u9fff]", name or "")) >= 2


def clean_desc(desc: str) -> str:
    """去掉混进来的节点说明,和句首图标被认成的字(「图」「回」「0一」「_」……):说明从第一个正文字开始。"""
    text = _TRUNCATED_NOTE.sub("", _NODE_NOTE.sub("", desc or "")).strip()
    m = re.search(r"(我方|敌方|敌人|首领|普通|精英|商店|刷新|战斗|进入|每|当|第|在|所有|全体|【)", text)
    if m and m.start() <= 3:
        text = text[m.start():]
    return text.strip()


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    out = []
    for d in data:
        if isinstance(d, dict) and valid_name(d.get("name")):
            out.append({**d, "desc": clean_desc(d.get("desc") or "")})
    return out


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

    def list(self) -> list[dict[str, object]]:
        """公共档案 + 本机档案(按名字去重,公共的说明优先),带来源、遇到次数,给知识库和网页看。"""
        out: dict[str, dict[str, object]] = {}
        for d in _read(self.public):
            out[d["name"]] = {"name": d["name"], "desc": d.get("desc") or "", "origin": "公共",
                              "source": d.get("source") or ""}
        for d in _read(self.local):
            item = out.setdefault(d["name"], {"name": d["name"], "desc": d.get("desc") or "", "origin": "本机",
                                              "source": ""})
            if not item.get("desc"):
                item["desc"] = d.get("desc") or ""
                item["origin"] = "本机"
            item["seen_count"] = int(d.get("seen_count") or 0)
            item["last_seen"] = d.get("last_seen") or ""
        return list(out.values())

    def get(self, name: str) -> str:
        return self.all().get(name, "")

    def export(self) -> Path:
        """把本机读到的词条并进公共档案(同名替换),公共档案里已有的说明优先保留。"""
        items = _read(self.public)
        have = {d["name"] for d in items}
        for d in _read(self.local):
            if d["name"] not in have and d.get("desc"):
                items.append({"name": d["name"], "desc": d["desc"], "source": f"对局中读取({d.get('last_seen') or ''})"})
        self.public.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.public.with_suffix(".yaml.tmp")
        keep = "".join(line + "\n" for line in (self.public.read_text(encoding="utf-8").splitlines()
                                                if self.public.exists() else []) if line.startswith("#"))
        tmp.write_text(keep + ("\n" if keep else "") + yaml.safe_dump(items, allow_unicode=True, sort_keys=False, width=120),
                       encoding="utf-8")
        os.replace(tmp, self.public)
        return self.public

    def record(self, name: str, desc: str) -> None:
        if not valid_name(name):
            return
        desc = clean_desc(desc)
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
