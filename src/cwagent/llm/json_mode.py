"""不支持原生工具调用的模型:在提示词里说明工具格式,从回复文本中解析 JSON。"""

from __future__ import annotations

import json
import re
from typing import Any

from .base import ToolCall, ToolSpec


def instructions(tools: list[ToolSpec]) -> str:
    lines = [
        "",
        "## 输出格式",
        "你不能直接调用工具。请在回复的最后输出且只输出一个 JSON 对象,形如",
        '{"tool": "<工具名>", "arguments": {<符合该工具参数定义的对象>}}',
        "可用工具及其参数的 JSON Schema:",
    ]
    for tool in tools:
        lines.append(f"- {tool.name}:{tool.description}")
        lines.append("  " + json.dumps(tool.parameters, ensure_ascii=False))
    return "\n".join(lines)


_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def _candidates(text: str) -> list[str]:
    """回复中所有可能是 JSON 对象的片段,越靠后的越优先。"""
    found = [m.group(1) for m in _FENCE.finditer(text)]
    # 从每个 '{' 开始找配平的 '}'(忽略字符串里的括号)
    for start in (i for i, c in enumerate(text) if c == "{"):
        depth, in_str, esc = 0, False, False
        for j in range(start, len(text)):
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    found.append(text[start:j + 1])
                    break
    return found


def parse(text: str, tools: list[ToolSpec]) -> list[ToolCall]:
    """解析回复中的工具调用。找不到合法 JSON 对象时返回空列表。"""
    names = {t.name for t in tools}
    parsed: list[tuple[str, Any]] = []
    for chunk in _candidates(text):
        try:
            obj = json.loads(chunk)
        except ValueError:
            continue
        if isinstance(obj, dict):
            parsed.append((chunk, obj))
    # 优先取最后一个带 tool 字段的对象;只有一个工具时,也接受直接给出参数对象
    for chunk, obj in reversed(parsed):
        if obj.get("tool") in names and isinstance(obj.get("arguments"), dict):
            return [ToolCall(id="json-0", name=obj["tool"], arguments=obj["arguments"], raw_arguments=chunk)]
    if len(tools) == 1:
        for chunk, obj in reversed(parsed):
            if "tool" not in obj:
                return [ToolCall(id="json-0", name=tools[0].name, arguments=obj, raw_arguments=chunk)]
    return []
