"""标准博弈的难度选择:不靠模型,按界面底部的「敌人难度」「词缀数量」确定性地调。

界面(2026-11-14 实机):列表每行左边是组内层号,中间一行高亮是当前选中的;底部两个六边形里是当前选中的
敌人难度和词缀数量(全屏 OCR 时常读不出,单独截出来放大再认)。左侧「A8 财富造物主」是当前职级的标题行,
不跟着选中行变,不能用来判断选中的是哪个职级组。列表上下各有一个箭头,点一下换一层(M7A S2 版的做法,
比滚轮可控:滚轮约 3 格才变一层,还会受滚动惯性影响);底部有「返回最高职级」「开始对局」。

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

UP_ARROW = (0.5, 0.139)     # 列表上方的「∧」:往上一层(敌人难度更高);坐标取自 M7A,2026-11-14 截图核对
DOWN_ARROW = (0.5, 0.782)   # 列表下方的「∨」:往下一层
COARSE = 3                  # 粗调时一次往下几层
MAX_STEPS = 80


def parse_target(target: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"A(\d)-(\d{1,2})", target.strip().upper())
    return (int(m.group(1)), int(m.group(2))) if m else None


A8_AFFIXES = 4
A8_LAYERS = 20             # A8 组有 20 层(维护者:账号最高职级 A8「财富造物主 20」)
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

    def step(layers: int) -> None:
        """正数往上(更难)、负数往下,每点一次箭头换一层。"""
        for _ in range(abs(layers)):
            device.click(*(UP_ARROW if layers > 0 else DOWN_ARROW))
            sleep(0.25)
        sleep(0.4)
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
    # 0. 先走直路:刚点过「返回最高职级」,此刻就站在 A8 最高层,目标层按「A8 共 20 层」直接算,
    #    不用先下到最低层再爬回来(选 A8-20 时那样要白跑 38 次点击,而且容易被滚动惯性带偏;
    #    M7A 也是直接用敌人难度的差值调,只有选「最低」才一路往下)
    highest = state["d"]
    goal = highest - (A8_LAYERS - layer)
    if goal >= 1:
        for _ in range(MAX_STEPS):
            if state["d"] == goal:
                break
            # 一次一层、每层都读:敌人难度可能一档跳多点(M7A 也提到过),连点容易越过目标
            step(1 if state["d"] < goal else -1)
            if state["a"] < A8_AFFIXES:
                break
        if state["d"] == goal and state["a"] >= A8_AFFIXES:
            log(f"难度:{target}(A8 最高层敌人难度 {highest},直接调到 {state['d']})")
            return True
        log(f"难度:直路没对(现在敌人难度 {state['d']},词缀 {state['a']}),改用探底")
    # 1. 粗调:往下到词缀数量变少(进了 A7)或到底
    for _ in range(MAX_STEPS):
        before = (state["d"], state["a"])
        step(-COARSE)
        if state["a"] < A8_AFFIXES or (state["d"], state["a"]) == before:
            break
    # 2. 回到 A8:往上一层一层调,直到词缀数量变回 4
    for _ in range(MAX_STEPS):
        if state["a"] >= A8_AFFIXES:
            break
        step(1)
    # 3. 确认在 A8 最低一层:再往下一层,词缀变少就退回来
    for _ in range(MAX_STEPS):
        d0 = state["d"]
        step(-1)
        if state["a"] < A8_AFFIXES:
            step(1)
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
        step(1 if state["d"] < goal else -1)
        if state["a"] < A8_AFFIXES:
            log(f"难度:滚出了 A8(敌人难度 {state['d']})")
            return False
    if state["d"] != goal:
        log(f"难度:没调到 {target}(目标敌人难度 {goal},现在 {state['d']})")
        return False
    log(f"难度:{target}(A8 第 1 层敌人难度 {first},现在 {state['d']})")
    return True
