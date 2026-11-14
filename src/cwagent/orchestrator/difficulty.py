"""标准博弈的难度选择:不靠模型,按界面底部的「敌人难度」「词缀数量」确定性地调。

界面(2026-11-14 实机):列表每行左边是组内层号,中间一行高亮是当前选中的;底部两个六边形里是当前选中的
敌人难度和词缀数量(全屏 OCR 时常读不出,单独截出来放大再认)。左侧「A8 财富造物主」是当前职级的标题行,
不跟着选中行变,不能用来判断选中的是哪个职级组。滚轮负数向下(难度降低),约 3 格变一层;
底部有「返回最高职级」「开始对局」。

A8 起有 4 个怪物词条(维护者告知),A7 是 3 个(实机读到过):从最高职级往下滚到词缀数量变成 3,再往上回到
变回 4 的第一层就是 A8-1;A8 每层敌人难度 +1(维护者告知),往上调到第 N 层并核对数值。只支持 A8,
其他职级交给模型。
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

from PIL import Image

from ..vision.ocr import Ocr, TextBox, find

LIST_POINT = (0.5, 0.55)
STEP = 3              # 滚几格变一级
MAX_STEPS = 80


def parse_target(target: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"A(\d)-(\d{1,2})", target.strip().upper())
    return (int(m.group(1)), int(m.group(2))) if m else None


A8_AFFIXES = 4
HEXES = {"difficulty": (0.283, 0.893), "affixes": (0.351, 0.893)}   # 底部两个六边形的中心


def read_values(image: Image.Image, ocr: Ocr) -> tuple[int | None, int | None]:
    """(敌人难度, 词缀数量):各自截出六边形一带、放大 3 倍再识别,数字可能被拆成几段,按横坐标拼起来。"""
    im = image.convert("RGB")
    w, h = im.size
    out = []
    for cx, cy in HEXES.values():
        crop = im.crop((int((cx - 0.03) * w), int((cy - 0.045) * h), int((cx + 0.03) * w), int((cy + 0.03) * h)))
        boxes = ocr(crop.resize((crop.width * 3, crop.height * 3)))
        digits = "".join(b.text for b in sorted(boxes, key=lambda b: b.x) if re.fullmatch(r"\d+", b.text))
        out.append(int(digits) if digits else None)
    return out[0], out[1]


def select(device, ocr: Ocr, target: str, log: Callable[[str], None], sleep=time.sleep,
           read: Callable[[Image.Image, Ocr], tuple[int | None, int | None]] = read_values) -> bool:
    """选好返回 True(之后由调用方点「开始对局」);做不到返回 False,交给模型。"""
    parsed = parse_target(target)
    if parsed is None:
        log(f"难度:看不懂目标难度 {target}(格式应为 A8-1)")
        return False
    want, layer = parsed
    if want != 8:
        log(f"难度:自动选择只支持 A8(按词缀数量 {A8_AFFIXES} 判断),{target} 交给模型")
        return False
    state = {"d": None, "a": None}

    def look() -> list[TextBox]:
        img = device.grab().image
        d, a = read(img, ocr)
        if d is None or a is None:   # 读不出就再截一次
            sleep(0.3)
            img = device.grab().image
            d, a = read(img, ocr)
        state["d"] = d if d is not None else state["d"]
        state["a"] = a if a is not None else state["a"]
        return ocr(img)

    def scroll(notches: int) -> None:
        device.scroll(*LIST_POINT, notches)
        sleep(0.6)
        look()

    boxes = look()
    top = find(boxes, "^返回最高职级$")
    if top is not None:
        device.click(top.x, top.y)
        sleep(1.0)
        look()
    if state["d"] is None or state["a"] is None:
        log("难度:读不出敌人难度或词缀数量")
        return False
    if state["a"] < A8_AFFIXES:
        log(f"难度:最高职级的词缀数量是 {state['a']},还没到 A8")
        return False
    # 1. 粗调:往下滚到词缀数量变少(进了 A7)或滚到底
    for _ in range(MAX_STEPS):
        before = (state["d"], state["a"])
        scroll(-3 * STEP)
        if state["a"] < A8_AFFIXES or (state["d"], state["a"]) == before:
            break
    # 2. 回到 A8:往上一层一层滚,直到词缀数量变回 4
    for _ in range(MAX_STEPS):
        if state["a"] >= A8_AFFIXES:
            break
        scroll(STEP)
    # 3. 确认在 A8 最低一层:再往下一层,词缀变少就退回来
    for _ in range(MAX_STEPS):
        d0 = state["d"]
        scroll(-STEP)
        if state["a"] < A8_AFFIXES:
            scroll(STEP)
            break
        if state["d"] == d0:   # 滚到底了
            break
    if state["a"] < A8_AFFIXES:
        log(f"难度:没找到 A8 的第 1 层(词缀数量 {state['a']},敌人难度 {state['d']})")
        return False
    first = state["d"]
    goal = first + layer - 1
    # 4. 往上调到目标层(每层敌人难度 +1)
    for _ in range(MAX_STEPS):
        if state["d"] == goal:
            break
        scroll(STEP if state["d"] < goal else -STEP)
        if state["a"] < A8_AFFIXES:
            log(f"难度:滚出了 A8(敌人难度 {state['d']})")
            return False
    if state["d"] != goal:
        log(f"难度:没调到 {target}(目标敌人难度 {goal},现在 {state['d']})")
        return False
    log(f"难度:{target}(A8 第 1 层敌人难度 {first},现在 {state['d']})")
    return True
