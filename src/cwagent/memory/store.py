"""记忆的存放:公共记忆在 knowledge/memories.yaml(随仓库发布),本机学到的在 data/memory.sqlite(不入库)。
同一 id 两处都有时以本机为准。"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

import yaml

from .. import paths
from .models import Memory, Status, now


class MemoryError(ValueError):
    pass


class MemoryStore:
    def __init__(self, public_path: Path | None = None, local_path: Path | None = None):
        self.public_path = public_path or paths.root() / "knowledge" / "memories.yaml"
        self.local_path = local_path or paths.data_dir() / "memory.sqlite"
        self._lock = threading.RLock()

    # ---- 读取

    def public(self) -> list[Memory]:
        if not self.public_path.exists():
            return []
        raw = yaml.safe_load(self.public_path.read_text(encoding="utf-8")) or []
        if not isinstance(raw, list):
            raise MemoryError(f"{self.public_path} 应是记忆条目的列表")
        return [Memory.model_validate(item) for item in raw]

    def _connect(self) -> sqlite3.Connection:
        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.local_path)
        con.execute("CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        return con

    def local(self) -> list[Memory]:
        if not self.local_path.exists():
            return []
        with self._lock, self._connect() as con:
            rows = con.execute("SELECT data FROM memories").fetchall()
        return [Memory.model_validate_json(r[0]) for r in rows]

    def all(self) -> list[tuple[Memory, str]]:
        """全部记忆及其来处("公共" / "本机"),本机覆盖同 id 的公共记忆。"""
        merged: dict[str, tuple[Memory, str]] = {m.id: (m, "公共") for m in self.public()}
        for m in self.local():
            merged[m.id] = (m, "本机")
        return sorted(merged.values(), key=lambda x: x[0].created)

    def get(self, memory_id: str) -> Memory | None:
        return next((m for m, _ in self.all() if m.id == memory_id), None)

    # ---- 写入(都写到本机库)

    def save(self, memory: Memory) -> Memory:
        memory = memory.model_copy(update={"updated": now()})
        with self._lock, self._connect() as con:
            con.execute("INSERT OR REPLACE INTO memories (id, data) VALUES (?, ?)",
                        (memory.id, memory.model_dump_json()))
        return memory

    def add(self, memory: Memory) -> Memory:
        if self.get(memory.id) is not None:
            raise MemoryError(f"记忆 {memory.id} 已存在")
        return self.save(memory)

    def _require(self, memory_id: str) -> Memory:
        memory = self.get(memory_id)
        if memory is None:
            raise MemoryError(f"记忆 {memory_id} 不存在")
        return memory

    def set_status(self, memory_id: str, status: Status) -> Memory:
        return self.save(self._require(memory_id).model_copy(update={"status": status}))

    def vote(self, memory_id: str, *, helped: bool, evidence: str = "") -> Memory:
        memory = self._require(memory_id)
        stats = memory.stats.model_copy(update={"helped": memory.stats.helped + int(helped),
                                                "hurt": memory.stats.hurt + int(not helped)})
        ev = memory.evidence + ([evidence] if evidence and evidence not in memory.evidence else [])
        return self.save(memory.model_copy(update={"stats": stats, "evidence": ev}))

    def mark_injected(self, memory_ids: list[str]) -> None:
        for mid in memory_ids:
            memory = self.get(mid)
            if memory is not None:
                self.save(memory.model_copy(update={"stats": memory.stats.model_copy(
                    update={"injected": memory.stats.injected + 1})}))

    def delete_local(self, memory_id: str) -> bool:
        with self._lock, self._connect() as con:
            return con.execute("DELETE FROM memories WHERE id = ?", (memory_id,)).rowcount > 0

    def export(self, memory_id: str) -> Path:
        """把一条记忆写进公共文件(同 id 则替换),之后可以提交到仓库。注入次数和投票不导出。"""
        memory = self._require(memory_id).model_copy(update={"stats": {"injected": 0, "helped": 0, "hurt": 0}})
        with self._lock:
            items = [m for m in self.public() if m.id != memory_id] + [memory]
            data = [m.model_dump(mode="json", exclude_defaults=True, exclude={"stats"}) for m in items]
            header = self._public_header()
            tmp = self.public_path.with_suffix(".yaml.tmp")
            tmp.write_text(header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100),
                           encoding="utf-8")
            os.replace(tmp, self.public_path)
        return self.public_path

    def _public_header(self) -> str:
        if not self.public_path.exists():
            return ""
        lines = []
        for line in self.public_path.read_text(encoding="utf-8").splitlines():
            if not line.startswith("#"):
                break
            lines.append(line)
        return "\n".join(lines) + ("\n\n" if lines else "")


def dumps(memory: Memory) -> str:
    return json.dumps(memory.model_dump(mode="json"), ensure_ascii=False, indent=2)
