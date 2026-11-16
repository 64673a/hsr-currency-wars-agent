"""只读统计历史决策输入中的分块、重复说明和历史增长,不调用模型或游戏。"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import statistics


def sections(text: str, level: int = 2) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"^" + "#" * level + r" (.+)\n", text, re.M))
    return [(m[1], text[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)].strip())
            for i, m in enumerate(matches)]


def sizes(values: list[int]) -> dict:
    return ({"count": len(values), "total": sum(values), "mean": round(statistics.mean(values), 1), "max": max(values)}
            if values else {"count": 0, "total": 0, "mean": None, "max": None})


def analyze(run: Path) -> dict:
    source = run / "steps.jsonl" if run.is_dir() else run
    records = []
    malformed = 0
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                records.append(value)
        except ValueError:
            malformed += 1
    decisions = [r for r in records if isinstance(r.get("decision"), dict) and r["decision"].get("prompt")]
    openings = [r for r in decisions if r.get("screen") == "prepare" and r["decision"].get("turn") == 1]
    blocks = defaultdict(list)
    entries: Counter = Counter()
    histories = []
    for row in openings:
        for title, text in sections(row["decision"]["prompt"]):
            name = title.split("(", 1)[0].split("（", 1)[0]
            blocks[name].append(len(text))
            if name in ("界面上出现的条目", "条目说明", "新条目说明"):
                entries.update(line for line in text.splitlines() if line.startswith("- ["))
            if name == "本局经历":
                histories.append(text)
    cumulative = sum(len(text) * count for text, count in entries.items())
    unique = sum(map(len, entries))
    usage_present = any(isinstance(r["decision"].get("usage"), dict) for r in decisions)
    return {
        "run": source.parent.name,
        "units": "Python 字符数；不是 token；不含系统提示词、工具定义、图片及回合内重发的完整请求",
        "records": len(records), "malformed_lines": malformed, "decisions": len(decisions),
        "prep_openings": len(openings),
        "all_new_prompt_chars": sizes([len(r["decision"]["prompt"]) for r in decisions]),
        "prepare_opening_chars": sizes([len(r["decision"]["prompt"]) for r in openings]),
        "opening_section_mean_chars": {k: round(sum(v) / len(openings), 1) for k, v in blocks.items()},
        "entity_line_chars": {"cumulative": cumulative, "unique": unique,
                              "exact_repeat_share": round(1 - unique / cumulative, 4) if cumulative else None},
        "history_first_last_chars": [len(histories[0]), len(histories[-1])] if histories else [],
        "history_coordinate_occurrences": sum(len(re.findall(r"\(0\.\d+,0\.\d+\)", h)) for h in histories),
        "run_notes_updates": sum(bool(r["decision"].get("run_notes")) for r in decisions),
        "usage_recorded": usage_present,
        "reported_input_tokens": sum((r["decision"].get("usage") or {}).get("input_tokens", 0) or 0
                                     for r in decisions) if usage_present else None,
        "reported_cache_read_tokens": sum((r["decision"].get("usage") or {}).get("cache_read_tokens", 0) or 0
                                          for r in decisions) if usage_present else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path, nargs="+")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = [analyze(run) for run in args.runs]
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
