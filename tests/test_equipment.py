import json
import sqlite3

import numpy as np
import pytest
from PIL import Image

from cwagent.brain.screen_agent import Action
from cwagent.capture import Frame
from cwagent.knowledge.equipment import EquipmentBook
from cwagent.orchestrator import prepare as prep
from cwagent.orchestrator.prepare import PrepareExecutor, _shift
from cwagent.vision.equipment import EquipmentReader, Item, cell_center


def icon(seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    block = rng.integers(0, 255, (8, 8, 3), dtype=np.uint8)
    return Image.fromarray(block).resize((128, 128), Image.NEAREST)


@pytest.fixture
def templates(tmp_path):
    d = tmp_path / "equipment"
    d.mkdir()
    icon(1).save(d / "1.png")
    icon(2).save(d / "2.png")
    icon(3).save(d / "3.png")
    icon(3).save(d / "4.png")          # 特权版和普通版图标一样
    return d, {"1": "轮滑鞋", "2": "红钻", "3": "反重力皮靴", "4": "反重力皮靴·特权"}


def screen(*seeds: int) -> Image.Image:
    img = Image.new("RGB", (1920, 1080), (60, 20, 40))
    for n, seed in enumerate(seeds, start=1):
        x, y = cell_center(n)
        img.paste(icon(seed).resize((58, 58)), (round(x * 1920) - 29, round(y * 1080) - 29))
    return img


def test_reader(templates):
    d, names = templates
    r = EquipmentReader(d, names)
    items = r.read(screen(1, 3, 2))
    assert [i.name for i in items] == ["轮滑鞋", "反重力皮靴", "红钻"]   # 特权版图标相同,报普通版
    assert r.read(screen()) == []


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "k.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE equipment (id TEXT, name TEXT, summary TEXT)")
    con.execute("CREATE TABLE recipes (product_id TEXT, a_id TEXT, b_id TEXT, craft_id TEXT)")
    con.execute("CREATE TABLE guides (data TEXT)")
    for i, n, c in (("1", "轮滑鞋", "简易装备"), ("2", "红钻", "钻石"), ("3", "反重力皮靴", "进阶装备"),
                    ("5", "奇兽花园星徽", "星徽")):
        con.execute("INSERT INTO equipment VALUES (?, ?, ?)", (i, n, json.dumps({"category": c})))
    con.execute("INSERT INTO recipes VALUES ('3', '1', '1', NULL)")
    con.execute("INSERT INTO recipes VALUES ('5', '2', '1', NULL)")
    board = {"front": [{"name": "阿格莱雅", "equipment": ["反重力皮靴", "反螺旋舞步"]}], "back": []}
    for uses in (100, 5):
        con.execute("INSERT INTO guides VALUES (?)", (json.dumps({"use_count": uses, "final_board": board}),))
    con.commit()
    con.close()
    return path


def test_book_combos_and_advice(db):
    book = EquipmentBook(db)
    items = [Item(1, 0, 0, "1", "轮滑鞋"), Item(2, 0, 0, "1", "轮滑鞋"), Item(3, 0, 0, "2", "红钻")]
    assert book.combos(items) == [(1, 2, "反重力皮靴"), (1, 3, "奇兽花园星徽")]
    text, names = book.describe(items, ["阿格莱雅", "银狼"])
    assert "1号+2号 → 反重力皮靴(进阶装备)" in text and "阿格莱雅:反重力皮靴、反螺旋舞步(出现在 2 篇" in text
    assert "银狼" not in text and "只作参考" in text
    assert names == ["轮滑鞋", "轮滑鞋", "红钻", "反重力皮靴", "奇兽花园星徽"]


class Bag:
    """假装备栏:executor 每次 read 都返回当前的物品;device 的拖动和点击改变它。"""

    def __init__(self, names):
        self.names, self.confirm = list(names), None

    def read(self, image):
        return [Item(n, *cell_center(n), str(n), name) for n, name in enumerate(self.names, start=1)]


class Dev:
    def __init__(self, bag):
        self.bag, self.actions = bag, []

    def grab(self):
        return Frame(image=Image.new("RGB", (1920, 1080)), timestamp=0)

    def drag(self, x1, y1, x2, y2, *, duration=0.4):
        self.actions.append(("drag", round(x2, 3), round(y2, 3)))
        idx = {cell_center(n): n for n in range(1, len(self.bag.names) + 1)}
        a, b = idx.get((x1, y1)), idx.get((x2, y2))
        if a and b:                                   # 拖到另一件上:等确认
            self.bag.confirm = (a, b)
        elif a:                                       # 拖给角色
            self.bag.names.pop(a - 1)

    def click(self, x, y, *, button="left"):
        self.actions.append(("click", x, y))
        if self.bag.confirm:
            a, b = self.bag.confirm
            keep, gone = min(a, b), max(a, b)
            self.bag.names[keep - 1] = "反重力皮靴"
            self.bag.names.pop(gone - 1)
            self.bag.confirm = None


def test_executor_compose_then_equip(monkeypatch):
    bag = Bag(["轮滑鞋", "轮滑鞋", "红钻"])
    dev = Dev(bag)
    monkeypatch.setattr(prep, "find_compose_confirm", lambda image: (0.948, 0.301) if bag.confirm else None)
    ex = PrepareExecutor(dev, lambda image: [], raw=lambda a, b: "", sleep=lambda s: None, equipment=bag)
    from cwagent.vision.board import Slot

    board = [Slot("front", 0, 0.3885, 0.372, "阿格莱雅", 1)]
    out = ex.run([Action(type="compose", item=2, onto=1), Action(type="equip", item=1, zone="front", index=1),
                  Action(type="equip", item=2, zone="front", index=1)], [], board)
    assert out.results[0].endswith("合成了 反重力皮靴,现在是 1 号")
    assert out.results[1] == "装备 1号 → 前台1号:反重力皮靴 给了 阿格莱雅"
    assert "已经在前面的操作里用掉了" in out.results[2]   # 原来的 2 号鞋子在合成时用掉了
    assert out.equipped == [("阿格莱雅", "反重力皮靴")]


def test_shift_indices():
    pending = [Action(type="equip", item=3, zone="front", index=1), Action(type="compose", item=1, onto=2)]
    _shift(pending, 2)
    assert pending[0].item == 2 and pending[1].item == 1 and pending[1].onto == 0


def test_privilege_v_mark(templates):
    from PIL import ImageDraw

    d, names = templates
    r = EquipmentReader(d, names)
    img = screen(1, 3)
    x, y = cell_center(2)
    cx, cy = round(x * 1920), round(y * 1080)
    draw = ImageDraw.Draw(img)
    draw.line((cx + 17, cy + 16, cx + 23, cy + 29, cx + 30, cy + 16), fill=(235, 245, 255), width=3)   # 右下角的 V
    items = r.read(img)
    assert [(i.name, i.privileged) for i in items] == [("轮滑鞋", False), ("反重力皮靴", True)]
