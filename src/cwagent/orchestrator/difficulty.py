"""标准博弈的难度选择:不靠模型,按界面上的职级组名和「敌人难度」数值确定性地调。

界面(2026-11-14 记录):左侧是当前选中行所在的职级组(如「A8财富造物主」),下方「敌人难度」标签上面是当前的
敌人难度数值;列表用滚轮滚动,负数向下(难度降低),约 3 格变一级;底部有「返回最高职级」「开始对局」。
每级敌人难度 +1(维护者告知),跨职级组时会多加几点。

做法(参考 trail-cli 的精确职级选择,但不用它 S1 时期的「职级 → 数值」表):先回到最高职级,往下滚到组名变成
下一个职级(或滚到底),再往上回到目标组的第 1 层,记下它的数值 v1;目标第 N 层 = v1 + N - 1,往上滚到数值对上。
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

from ..vision.ocr import Ocr, TextBox, find

LIST_POINT = (0.5, 0.55)
STEP = 3              # 滚几格变一级
MAX_STEPS = 80


def parse_target(target: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"A(\d)-(\d{1,2})", target.strip().upper())
    return (int(m.group(1)), int(m.group(2))) if m else None


def read(boxes: list[TextBox]) -> tuple[int | None, int | None]:
    """(职级组, 敌人难度数值)。"""
    group = find(boxes, r"^A\d", region=(0.0, 0.22, 0.25, 0.15))
    label = find(boxes, r"^敌人难度$", region=(0.2, 0.85, 0.2, 0.15))
    value = None
    if label is not None:
        above = [b for b in boxes if re.fullmatch(r"\d{1,3}", b.text) and abs(b.x - label.x) < 0.04
                 and label.y - 0.07 < b.y < label.y]
        value = int(above[0].text) if above else None
    return (int(group.text[1]) if group else None), value


def select(device, ocr: Ocr, target: str, log: Callable[[str], None], sleep=time.sleep) -> bool:
    """选好返回 True(之后由调用方点「开始对局」);做不到返回 False,交给模型。"""
    parsed = parse_target(target)
    if parsed is None:
        log(f"难度:看不懂目标难度 {target}(格式应为 A8-1)")
        return False
    want_group, want_layer = parsed

    def look() -> tuple[int | None, int | None, list[TextBox]]:
        boxes = ocr(device.grab().image)
        return (*read(boxes), boxes)

    def scroll(notches: int) -> tuple[int | None, int | None]:
        device.scroll(*LIST_POINT, notches)
        sleep(0.5)
        g, v, _ = look()
        return g, v

    group, value, boxes = look()
    top = find(boxes, "^返回最高职级$")
    if top is not None:
        device.click(top.x, top.y)
        sleep(1.0)
        group, value, _ = look()
    if group is None or value is None:
        log("难度:读不出职级组或敌人难度")
        return False
    if group < want_group:
        log(f"难度:最高只到 A{group},选不了 {target}")
        return False
    # 往下滚到组名低于目标(或到底)
    for _ in range(MAX_STEPS):
        if group < want_group:
            break
        g, v = scroll(-STEP)
        if v == value and g == group:   # 到底了
            break
        group, value = g if g is not None else group, v if v is not None else value
    # 往上回到目标组的第 1 层
    for _ in range(4):
        if group == want_group:
            break
        g, v = scroll(1)
        group, value = g if g is not None else group, v if v is not None else value
    if group != want_group or value is None:
        log(f"难度:没找到 A{want_group} 的第 1 层(现在 A{group},敌人难度 {value})")
        return False
    first = value
    goal = first + want_layer - 1
    for _ in range(MAX_STEPS):
        if value == goal:
            break
        notches = STEP if value < goal else -1
        if 0 < goal - value < 2:
            notches = 1
        g, v = scroll(notches)
        if v is None:
            continue
        if g != want_group:
            log(f"难度:滚过了 A{want_group}(现在 A{g},敌人难度 {v}),A{want_group} 可能没有第 {want_layer} 层")
            return False
        group, value = g, v
    if value != goal:
        log(f"难度:没调到 {target}(目标敌人难度 {goal},现在 {value})")
        return False
    log(f"难度:{target}(A{want_group} 第 1 层敌人难度 {first},现在 {value})")
    return True
