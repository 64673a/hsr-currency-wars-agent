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


GROUPS = {"黑铁": 0, "青铜": 1, "翠钢": 2, "钴银": 3, "冰钛": 4, "紫金": 5, "投资大师": 6, "资本帝王": 7, "财富造物主": 8}
GROUP_AREA = (0.0, 0.25, 0.25, 0.09)   # 左侧当前选中行所在的职级组,如「A8财富造物主」


def read(boxes: list[TextBox]) -> tuple[int | None, int | None]:
    """(职级组, 敌人难度数值)。组名按名字认(OCR 有时把「A7」读成「A」)。"""
    group = None
    x, y, w, h = GROUP_AREA
    for b in boxes:
        if x <= b.x <= x + w and y <= b.y <= y + h:
            group = next((g for name, g in GROUPS.items() if name in b.text), None)
            if group is None and re.match(r"^A\d", b.text):
                group = int(b.text[1])
            if group is not None:
                break
    label = find(boxes, r"^敌人难度$", region=(0.2, 0.85, 0.2, 0.15))
    value = None
    if label is not None:
        above = [b for b in boxes if re.fullmatch(r"\d{1,3}", b.text) and abs(b.x - label.x) < 0.04
                 and label.y - 0.07 < b.y < label.y]
        value = int(above[0].text) if above else None
    return group, value


def select(device, ocr: Ocr, target: str, log: Callable[[str], None], sleep=time.sleep) -> bool:
    """选好返回 True(之后由调用方点「开始对局」);做不到返回 False。"""
    parsed = parse_target(target)
    if parsed is None:
        log(f"难度:看不懂目标难度 {target}(格式应为 A8-1)")
        return False
    want, layer = parsed

    def look() -> tuple[int | None, int | None, list[TextBox]]:
        boxes = ocr(device.grab().image)
        return (*read(boxes), boxes)

    state = {"g": None, "v": None}

    def scroll(notches: int) -> tuple[int | None, int | None]:
        device.scroll(*LIST_POINT, notches)
        sleep(0.6)
        g, v, _ = look()
        state["g"] = g if g is not None else state["g"]
        state["v"] = v if v is not None else state["v"]
        return g, v

    g, v, boxes = look()
    top = find(boxes, "^返回最高职级$")
    if top is not None:
        device.click(top.x, top.y)
        sleep(1.0)
        g, v, _ = look()
    state.update(g=g, v=v)
    if g is None or v is None:
        log("难度:读不出职级组或敌人难度")
        return False
    if g < want:
        log(f"难度:最高只到 A{g},选不了 {target}")
        return False
    # 1. 粗调:往下滚到组名低于目标(或滚到底)
    for _ in range(MAX_STEPS):
        before = (state["g"], state["v"])
        scroll(-3 * STEP)
        if state["g"] < want or (state["g"], state["v"]) == before:
            break
    # 2. 回到目标组:往上一层一层滚,直到组名回到目标
    for _ in range(MAX_STEPS):
        if state["g"] == want:
            break
        scroll(STEP)
    if state["g"] != want:
        log(f"难度:没回到 A{want}(现在 A{state['g']},敌人难度 {state['v']})")
        return False
    # 3. 确认在目标组最低一层:再往下一层,组名变了就退回来
    for _ in range(MAX_STEPS):
        g0, v0 = state["g"], state["v"]
        scroll(-STEP)
        if state["g"] != want:
            scroll(STEP)
            break
        if state["v"] == v0:          # 滚到底了
            break
    if state["g"] != want:
        log(f"难度:没找到 A{want} 的第 1 层(现在 A{state['g']},敌人难度 {state['v']})")
        return False
    first = state["v"]
    goal = first + layer - 1
    # 4. 往上调到目标层(每层敌人难度 +1)
    for _ in range(MAX_STEPS):
        if state["v"] == goal:
            break
        scroll(STEP if state["v"] < goal else -STEP)
        if state["g"] != want:
            log(f"难度:滚出了 A{want}(现在 A{state['g']},敌人难度 {state['v']}),A{want} 可能没有第 {layer} 层")
            return False
    if state["v"] != goal:
        log(f"难度:没调到 {target}(目标敌人难度 {goal},现在 {state['v']})")
        return False
    log(f"难度:{target}(A{want} 第 1 层敌人难度 {first},现在 {state['v']})")
    return True
