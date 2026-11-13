"""记忆条目。设计见 docs/TECH_DESIGN.md 5.4。"""

from __future__ import annotations

import secrets
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Kind = Literal["机制", "打法", "教训"]
Status = Literal["draft", "approved", "rejected", "archived"]
Confidence = Literal["high", "medium", "low"]
Layer = Literal["战略", "战术", "选择", "事件"]

STATUS_NAMES = {"draft": "待审核", "approved": "已采纳", "rejected": "驳回", "archived": "已归档"}
CONFIDENCE_NAMES = {"high": "已核实", "medium": "有依据未核实", "low": "推测"}


class Triggers(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entities: list[str] = Field(default_factory=list)  # 局面里出现这些名字时想起
    nodes: list[str] = Field(default_factory=list)     # 当前或下一个节点类型,如 reward、battle、boss
    screens: list[str] = Field(default_factory=list)   # 当前界面,如 prepare、environment_choice

    def empty(self) -> bool:
        return not (self.entities or self.nodes or self.screens)


class Stats(BaseModel):
    injected: int = 0
    helped: int = 0
    hurt: int = 0


def new_id() -> str:
    return f"m-{time.strftime('%Y%m%d')}-{secrets.token_hex(3)}"


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


class Memory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=new_id)
    kind: Kind
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    triggers: Triggers = Field(default_factory=Triggers)
    scope: list[Layer] = Field(default_factory=list)  # 空表示所有决策层
    status: Status = "draft"
    confidence: Confidence = "low"
    source: str = ""
    evidence: list[str] = Field(default_factory=list)
    stats: Stats = Field(default_factory=Stats)
    created: str = Field(default_factory=now)
    updated: str = Field(default_factory=now)

    def label(self) -> str:
        return (f"{self.kind}|{STATUS_NAMES[self.status]}|{CONFIDENCE_NAMES[self.confidence]}"
                + (f"|来源:{self.source}" if self.source else ""))
