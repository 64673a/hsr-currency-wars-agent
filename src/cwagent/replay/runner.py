"""离线重发历史决策的原始消息和截图,不执行操作。"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from ..brain.screen_agent import ACT_TOOL
from ..llm import ImageInput, Message, complete


def _inside(run: Path, name: str) -> Path:
    path = (run / name).resolve()
    if run.resolve() not in path.parents:
        raise ValueError("请求路径越过对局目录")
    return path


def load_request(run: Path, name: str) -> tuple[str, list[Message]]:
    request = json.loads(_inside(run, name).read_text(encoding="utf-8"))
    messages = []
    for item in request["messages"]:
        item = dict(item)
        images = []
        for raw in item.pop("images", []):
            raw = dict(raw)
            raw["data"] = base64.b64encode(_inside(run, raw.pop("path")).read_bytes()).decode("ascii")
            images.append(ImageInput.model_validate(raw))
        messages.append(Message.model_validate({**item, "images": images}))
    return request["system"], messages


def replay(run: Path, endpoint, *, start: int = 0, end: int | None = None) -> Path:
    if Path(endpoint.name).name != endpoint.name:
        raise ValueError("模型档案名不能包含路径")
    records = [json.loads(line) for line in (run / "steps.jsonl").read_text(encoding="utf-8").splitlines()]
    selected = [r for r in records if r.get("request") and r["step"] >= start and (end is None or r["step"] <= end)]
    if not selected:
        raise ValueError("所选范围没有保存原始请求的决策,旧对局无法保证同条件回放")
    out = run / "replays" / f"{endpoint.name}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as target:
        for record in selected:
            system, messages = load_request(run, record["request"])
            response = complete(endpoint, system, messages, [ACT_TOOL])
            target.write(json.dumps({"step": record["step"], "request": record["request"],
                                     "response": response.model_dump(exclude={"raw", "message_raw"})}, ensure_ascii=False) + "\n")
            target.flush()
    return out
