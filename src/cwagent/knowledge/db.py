"""知识库 sqlite 的写入与查询。设计见 docs/TECH_DESIGN.md 5.1。

每类条目一张表,结构相同:id、name、sort、search、summary(JSON)、data(JSON)。
另有 trait_layers(羁绊档位)、recipes(装备配方)、meta、raw_snapshots。
记忆(经验)不在这里,见 cwagent.memory;本库每次同步都整库重建。
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from .build import KINDS, Knowledge

SCHEMA_VERSION = "1"

_ENTRY_TABLE = """CREATE TABLE {name} (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, sort REAL NOT NULL DEFAULT 0,
    search TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '{{}}', data TEXT NOT NULL)"""

_OTHER_TABLES = [
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE trait_layers (trait_id TEXT NOT NULL, count INTEGER NOT NULL, quality INTEGER NOT NULL,"
    " desc TEXT NOT NULL, PRIMARY KEY (trait_id, count))",
    "CREATE TABLE recipes (product_id TEXT NOT NULL, a_id TEXT NOT NULL, b_id TEXT NOT NULL, craft_id TEXT)",
    "CREATE INDEX recipes_a ON recipes (a_id)",
    "CREATE INDEX recipes_b ON recipes (b_id)",
    "CREATE TABLE raw_snapshots (name TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, json TEXT NOT NULL)",
]


def write(path: Path, knowledge: Knowledge, raw: dict[str, Any]) -> None:
    """整库重建:先写临时文件,成功后替换,失败时旧库不受影响。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".sqlite.tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        for kind in KINDS:
            con.execute(_ENTRY_TABLE.format(name=kind))
        for sql in _OTHER_TABLES:
            con.execute(sql)
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        meta = {**knowledge.meta, "synced_at": now, "schema_version": SCHEMA_VERSION,
                "counts": json.dumps(knowledge.counts(), ensure_ascii=False),
                "warnings": json.dumps(knowledge.warnings, ensure_ascii=False)}
        con.executemany("INSERT INTO meta VALUES (?, ?)", meta.items())
        for kind, entries in knowledge.entries.items():
            con.executemany(
                f"INSERT INTO {kind} (id, name, sort, search, summary, data) VALUES (?, ?, ?, ?, ?, ?)",
                [(e.id, e.name, e.sort, e.search.lower(), json.dumps(e.summary, ensure_ascii=False),
                  json.dumps(e.data, ensure_ascii=False)) for e in entries])
        con.executemany("INSERT INTO trait_layers VALUES (?, ?, ?, ?)", knowledge.trait_layers)
        con.executemany("INSERT INTO recipes VALUES (?, ?, ?, ?)", knowledge.recipes)
        con.executemany("INSERT INTO raw_snapshots VALUES (?, ?, ?)",
                        [(name, now, json.dumps(value, ensure_ascii=False)) for name, value in raw.items()])
        con.commit()
    finally:
        con.close()
    os.replace(tmp, path)


def replace_entries(path: Path, kind: str, entries) -> None:
    """只换掉一类条目(对局中读到新的奇遇伙伴、敌人词条时用),其他表不动;meta 里的条数跟着改。"""
    if kind not in KINDS or not path.exists():
        return
    con = sqlite3.connect(path)
    try:
        with con:
            con.execute(f"DELETE FROM {kind}")
            con.executemany(
                f"INSERT INTO {kind} (id, name, sort, search, summary, data) VALUES (?, ?, ?, ?, ?, ?)",
                [(e.id, e.name, e.sort, e.search.lower(), json.dumps(e.summary, ensure_ascii=False),
                  json.dumps(e.data, ensure_ascii=False)) for e in entries])
            row = con.execute("SELECT value FROM meta WHERE key = 'counts'").fetchone()
            if row:
                counts = json.loads(row[0])
                counts[kind] = len(entries)
                con.execute("UPDATE meta SET value = ? WHERE key = 'counts'", (json.dumps(counts, ensure_ascii=False),))
    finally:
        con.close()


class KnowledgeDB:
    """只读查询。每次调用打开新连接,可在多线程中使用。"""

    def __init__(self, path: Path):
        self.path = path

    def exists(self) -> bool:
        return self.path.exists()

    def _connect(self) -> sqlite3.Connection:
        if not self.path.exists():
            raise FileNotFoundError(f"知识库不存在:{self.path},请先运行 cwagent knowledge sync")
        con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con

    def meta(self) -> dict[str, Any]:
        if not self.exists():
            return {}
        with self._connect() as con:
            meta: dict[str, Any] = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM meta")}
        for key in ("counts", "warnings"):
            if key in meta:
                meta[key] = json.loads(meta[key])
        return meta

    def list(self, kind: str, *, q: str = "", limit: int = 100, offset: int = 0) -> tuple[int, list[dict[str, Any]]]:
        if kind not in KINDS:
            raise KeyError(kind)
        where, args = "", []
        if q.strip():
            terms = q.lower().split()
            where = "WHERE " + " AND ".join("search LIKE ?" for _ in terms)
            args = [f"%{t}%" for t in terms]
        with self._connect() as con:
            total = con.execute(f"SELECT COUNT(*) FROM {kind} {where}", args).fetchone()[0]
            rows = con.execute(f"SELECT id, name, summary FROM {kind} {where} ORDER BY sort, id LIMIT ? OFFSET ?",
                               [*args, limit, offset]).fetchall()
        return total, [{"id": r["id"], "name": r["name"], **json.loads(r["summary"])} for r in rows]

    def get(self, kind: str, entry_id: str) -> dict[str, Any] | None:
        if kind not in KINDS:
            raise KeyError(kind)
        with self._connect() as con:
            row = con.execute(f"SELECT data FROM {kind} WHERE id = ?", (entry_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def find_by_name(self, kind: str, name: str) -> list[dict[str, Any]]:
        if kind not in KINDS:
            raise KeyError(kind)
        with self._connect() as con:
            rows = con.execute(f"SELECT data FROM {kind} WHERE name = ? ORDER BY sort", (name,)).fetchall()
        return [json.loads(r["data"]) for r in rows]
