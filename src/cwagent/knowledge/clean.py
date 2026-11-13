"""清洗官方数据描述文本中的标记。设计见 docs/TECH_DESIGN.md 3.1.1。

接口描述里出现的标记(2026-11 实测):
- <color=#f29e38ff>文字</color>               → 保留文字
- <property type=X display=all|icon>          → 属性名;display=icon(只显示图标)且后文紧接着写出了
                                                这个名字(或紧跟【名称】/另一个标记)时省略,避免重复
- <variableproperty name=X display=icon>      → 同上
- <textswitch ...>文字</textswitch>           → 保留文字(满足条件时生效的说明)
- <variable ...>…</variable>、<variable ...>  → 「(局内实时值)」
- <value> <threshold>                         → 删除
- <i>风味文本</i>                             → 删除
- 字面的 \\n                                  → 换行
"""

from __future__ import annotations

import re

DYNAMIC = "(局内实时值)"

_COLOR = re.compile(r"</?color[^>]*>")
_TEXTSWITCH = re.compile(r"</?textswitch[^>]*>")
_ITALIC = re.compile(r"<i>.*?</i>", re.S)
_VARIABLE_PAIR = re.compile(r"<variable(?=[\s>])[^>]*>.*?</variable>", re.S)
_VARIABLE = re.compile(r"<variable(?=[\s>])[^>]*>")  # 不能误匹配 <variableproperty
_PROPERTY = re.compile(r"<(property|variableproperty)\s+([^>]*)>")
_ATTR = re.compile(r'(\w+)=("[^"]*"|\S+)')
_LEFTOVER = re.compile(r"</?(value|threshold|b|u|size|unbreak)[^>]*>")


# 接口的 role_property_list 里缺少的属性名
_FALLBACK_PROPERTIES = {"StanceBreakAddedRatio": "击破效率"}


def _named_after(name: str, following: str) -> bool:
    """只显示图标的属性,后文是否紧接着写出了它的名字。"""
    if not following:
        return False
    if following[0] in "【<":
        return True
    window = following[: len(name) + 6]
    return bool(name) and (name in window or (len(name) >= 2 and name[-2:] in window))


def _attrs(s: str) -> dict[str, str]:
    return {k: v.strip('"') for k, v in _ATTR.findall(s)}


def clean(text: str | None, properties: dict[str, str] | None = None) -> str:
    """把描述文本转成纯文本。properties 是属性类型到中文名的映射。"""
    if not text:
        return ""
    properties = properties or {}
    t = text.replace("\\n", "\n").replace("\xa0", " ")
    t = _ITALIC.sub("", t)
    t = _VARIABLE_PAIR.sub(DYNAMIC, t)
    t = _VARIABLE.sub(DYNAMIC, t)

    def prop(m: re.Match[str]) -> str:
        a = _attrs(m.group(2))
        key = a.get("type") or a.get("name") or ""
        name = properties.get(key) or _FALLBACK_PROPERTIES.get(key) or key
        if a.get("display") == "icon" and _named_after(name, m.string[m.end():m.end() + 12]):
            return ""
        return name

    t = _PROPERTY.sub(prop, t)
    t = _COLOR.sub("", t)
    t = _TEXTSWITCH.sub("", t)
    t = _LEFTOVER.sub("", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()
