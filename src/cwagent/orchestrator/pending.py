"""模型决策中的这一步:开始问模型时就把这一步写进对局目录的 pending.json,流式收到的思考和判断边收边更新,
决策结束(整条记录写进 steps.jsonl)后删掉。网页据此在模型还没答完时就显示这一步(维护者 2026-11-18 要求)。

用文件而不是进程内的状态:网页服务启动的对局和命令行启动的对局都能这样读到。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

WRITE_GAP = 0.25        # 流式片段最多隔这么久写一次文件
MAX_REASONING = 12000   # 文件里只留思考的最后这么多字(完整的在决策结束后的记录里)

_ESCAPES = {"n": "\n", "t": "\t", "r": "", "b": "", "f": "", '"': '"', "\\": "\\", "/": "/"}


def partial_string(src: str, key: str) -> str | None:
    """还没写完的 JSON 里某个字符串字段已经写出来的部分(流式时从工具参数里取模型的判断)。没出现返回 None。"""
    m = re.search(rf'"{key}"\s*:\s*"', src)
    if not m:
        return None
    out, i = [], m.end()
    while i < len(src):
        c = src[i]
        if c == '"':
            break
        if c != "\\":
            out.append(c)
            i += 1
            continue
        if i + 1 >= len(src):
            break
        nxt = src[i + 1]
        if nxt == "u":
            digits = src[i + 2:i + 6]
            if len(digits) < 4:
                break
            try:
                out.append(chr(int(digits, 16)))
            except ValueError:
                pass
            i += 6
            continue
        out.append(_ESCAPES.get(nxt, nxt))
        i += 2
    # 😀 这样的代理对拼回一个字符;只收到一半的丢掉
    return "".join(out).encode("utf-16", "surrogatepass").decode("utf-16", "ignore")


class PendingDecision:
    """一步决策的进度。feed 收流式片段(和 llm.complete 的 on_delta 一样:reasoning / text / tool / reset)。"""

    def __init__(self, path: Path, info: dict):
        self.path = path
        self.info = {**info, "started": time.time()}
        self.reasoning = self.text = self.args = ""
        self.retries = 0
        self._last = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._write(force=True)

    def feed(self, kind: str, text: str) -> None:
        with self._lock:
            if kind == "reset":
                if self.reasoning or self.text or self.args:   # 重试或输出不合法重问:从头再来
                    self.retries += 1
                self.reasoning = self.text = self.args = ""
            elif kind == "reasoning":
                self.reasoning += text
            elif kind == "text":
                self.text += text
            elif kind == "tool":
                self.args += text
        self._write()

    def snapshot(self) -> dict:
        src = self.args or self.text
        text = self.text.strip()
        reasoning = self.reasoning
        return {**self.info, "updated": time.time(), "retries": self.retries,
                "reasoning": reasoning[-MAX_REASONING:], "reasoning_cut": len(reasoning) > MAX_REASONING,
                "analysis": partial_string(src, "analysis"),
                "acting": '"actions"' in src,
                # 原生工具调用时正文多半是空的;JSON 模式下正文就是参数本身,不重复给
                "text": "" if text.startswith(("{", "```")) else text}

    def _write(self, force: bool = False) -> None:
        with self._lock:
            now = time.monotonic()
            if self._stop.is_set() or (not force and now - self._last < WRITE_GAP):
                return
            self._last = now
            data = json.dumps(self.snapshot(), ensure_ascii=False)
            tmp = self.path.with_name(self.path.name + ".tmp")
            try:
                tmp.write_text(data, encoding="utf-8")
                os.replace(tmp, self.path)
            except OSError:
                pass   # Windows 上网页正好在读时替换会失败,下一次再写

    def close(self) -> None:
        with self._lock:
            self._stop.set()
        for p in (self.path, self.path.with_name(self.path.name + ".tmp")):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass


def read(path: Path) -> dict | None:
    """网页服务读 pending.json;没有、正在被替换或者内容不完整都当作没有。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
