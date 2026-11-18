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
from cwagent.vision.ocr import TextBox


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

    def drag(self, x1, y1, x2, y2, *, duration=0.4, hold=0.0):
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
    assert "合成了 反重力皮靴(现在是 1 号);装备栏现在是 1号 反重力皮靴" in out.results[0]
    # 合成后装备栏会重新排序,后面的装备操作先不做,让模型按新装备栏重新给编号
    assert "后面的合成 / 装备先不做" in out.results[1] and len(out.results) == 2 and out.observe
    out = ex.run([Action(type="equip", item=1, zone="front", index=1)], [], board)
    assert out.results == ["装备 1号 → 前台1号:反重力皮靴 给了 阿格莱雅"]
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


PANEL = [("简易武装箱", 0.535, 0.056), ("请选择1个", 0.535, 0.091), ("以太钻头", 0.308, 0.309), ("折叠小刀", 0.460, 0.310),
         ("量产型装甲", 0.611, 0.310), ("幸运星", 0.762, 0.310), ("查看详情", 0.314, 0.363), ("开启", 0.228, 0.870)]


def tb(items):
    return [TextBox(t, x, y, 0.05, 0.02, 0.9) for t, x, y in items]


def test_item_slots_and_panel():
    from cwagent.vision.boxes import item_slots, read_panel

    assert item_slots(tb(PANEL)) == [1]
    title, options = read_panel(tb(PANEL))
    assert title == "简易武装箱" and [o.text for o in options] == ["以太钻头", "折叠小刀", "量产型装甲", "幸运星"]
    assert read_panel(tb([("开启", 0.228, 0.87)])) is None


class BoxGame:
    """假游戏:点道具弹出选择面板,点空白处收起,点选项拿走。"""

    def __init__(self):
        self.panel, self.taken, self.actions = False, None, []

    def grab(self):
        return Frame(image=Image.new("RGB", (8, 8)), timestamp=0)

    def click(self, x, y, *, button="left"):
        self.actions.append((round(x, 3), round(y, 3)))
        if not self.panel and abs(x - 0.2285) < 0.01:
            self.panel = True
        elif self.panel and (x, y) == (0.75, 0.5):
            self.panel = False
        elif self.panel and y < 0.3:
            self.taken = next(t for t, px, _ in PANEL[2:6] if abs(px - x) < 0.01)
            self.panel = False

    def ocr(self, image):
        return tb(PANEL if self.panel else [p for p in PANEL if p[0] == "开启"] if not self.taken else [])


def test_peek_and_take_items():
    from cwagent.vision.board import Slot

    game = BoxGame()
    ex = PrepareExecutor(game, game.ocr, raw=lambda a, b: "", sleep=lambda s: None)
    board = [Slot("bench", 0, 0.2285, 0.845, "道具(可开启)", None, 0.66), Slot("bench", 1, 0.2935, 0.845, "花火", None, 0.9, 1)]
    out = ex.run([Action(type="move", zone="bench", index=1, to_zone="back", to_index=1),
                  Action(type="open", zone="bench", index=1), Action(type="take", zone="bench", index=2, text="幸运星"),
                  Action(type="take", zone="bench", index=1, text="幸运星")], [], board)
    assert "是道具,不是角色" in out.results[0]
    assert out.results[1].endswith("简易武装箱,可选 以太钻头、折叠小刀、量产型装甲、幸运星(没选,道具留着)")
    assert out.seen == {1: ("简易武装箱", ["以太钻头", "折叠小刀", "量产型装甲", "幸运星"])}
    assert "不是可开启的道具" in out.results[2]
    assert out.results[3].endswith("拿到 幸运星(来自简易武装箱)") and out.taken == [1] and game.taken == "幸运星"


def test_with_boxes_example(db):
    """维护者的例子:手里有折叠小刀,箱子里有幸运星 → 能凑成高周波电锯。"""
    con = sqlite3.connect(db)
    for i, n, c in (("6", "幸运星", "简易装备"), ("7", "折叠小刀", "简易装备"), ("8", "高周波电锯", "进阶装备")):
        con.execute("INSERT INTO equipment VALUES (?, ?, ?)", (i, n, json.dumps({"category": c})))
    con.execute("INSERT INTO recipes VALUES ('8', '6', '7', NULL)")
    con.commit()
    con.close()
    book = EquipmentBook(db)
    items = [Item(1, 0, 0, "7", "折叠小刀")]
    boxes = {1: ("简易武装箱", ["以太钻头", "折叠小刀", "量产型装甲", "幸运星"])}
    lines = book.with_boxes(items, boxes)
    assert "高周波电锯(进阶装备) = 折叠小刀(装备栏1号)+ 幸运星(备战席1号的简易武装箱)" in lines
    text, names = book.describe(items, [], boxes)
    assert "先留着" in text and "高周波电锯" in names


def test_compose_confirm_retry_then_cancel(monkeypatch):
    """确认框点不下去时重试,最后点 ✗ 取消,不能把它留在屏幕上。"""
    bag = Bag(["轮滑鞋", "轮滑鞋"])

    class Stuck(Dev):
        def click(self, x, y, *, button="left"):
            self.actions.append(("click", round(x, 3), round(y, 3)))
            if abs(x - (0.948 - 115 / 1920)) < 0.001:   # 点了 ✗
                self.bag.confirm = None

    dev = Stuck(bag)
    monkeypatch.setattr(prep, "find_compose_confirm", lambda image: (0.948, 0.301) if bag.confirm else None)
    ex = PrepareExecutor(dev, lambda image: [], raw=lambda a, b: "", sleep=lambda s: None, equipment=bag)
    out = ex.run([Action(type="compose", item=2, onto=1), Action(type="ready")], [], None)
    clicks = [a for a in dev.actions if a[0] == "click"]
    assert len(clicks) == 4 and clicks[-1][1] == round(0.948 - 115 / 1920, 3)   # 3 次 ✓ + 1 次 ✗
    assert out.results == ["合成装备 2号 + 1号:合成确认点不下去,已取消"] and bag.confirm is None


def test_role_description_has_position_and_carry_stats(tmp_path):
    from cwagent.knowledge.index import NameIndex

    path = tmp_path / "k.sqlite"
    con = sqlite3.connect(path)
    for kind in ("portals", "augments", "spotlights", "companions", "equipment", "traits"):
        con.execute(f"CREATE TABLE {kind} (name TEXT, data TEXT)")
    con.execute("CREATE TABLE roles (name TEXT, data TEXT)")
    con.execute("CREATE TABLE guides (data TEXT)")
    role = {"cost": 3, "position": "前后台", "tags": ["辅助"], "traits": [{"name": "开拓银轨"}],
            "skills": [{"position": "前台", "name": "人气主播", "tags": ["辅助"], "stars": [{"desc": "让队友再放一次终结技"}]}]}
    con.execute("INSERT INTO roles VALUES ('开拓者', ?)", (json.dumps(role),))
    board = {"front": [{"name": "开拓者", "carry": False}], "back": []}
    con.execute("INSERT INTO guides VALUES (?)", (json.dumps({"carries": ["阿格莱雅"], "final_board": board}),))
    con.commit()
    con.close()
    text = NameIndex(path).link(["开拓者"])[0].text
    assert "定位 辅助" in text and "「人气主播」〔辅助〕" in text and "出现在 1 篇的最终阵容,其中 0 篇是主 C" in text


def test_privilege_detected_by_v_shape_not_bright_pixels():
    """特权判定按 card-border-5-b 里那个白色 V 的形状做掩码:边框的渐变色不算(维护者 2026-11-16 发现
    特权只是套了一层带 V 的边框,图标本体和普通版同一张)。"""
    import numpy as np

    from cwagent.vision.equipment import EquipmentReader

    cell = np.zeros((70, 70, 3), np.uint8)
    cell[EquipmentReader._v_mask((70, 70))] = 255          # 画一个白 V
    assert EquipmentReader.has_v(cell)
    assert not EquipmentReader.has_v(np.zeros((70, 70, 3), np.uint8))
    for border in ((0x34, 0xEA, 0xE5), (0xFE, 0xE1, 0x8C)):   # 边框的青蓝、金色渐变
        tinted = np.zeros((70, 70, 3), np.uint8)
        tinted[EquipmentReader._v_mask((70, 70))] = border
        assert not EquipmentReader.has_v(tinted)


def test_compose_confirm_multiscale():
    """合成确认的 ✓ 用多尺度匹配:界面缩放一点也要认得出(2026-11-16 两次合成都没弹出确认,误报「不能合成」)。"""
    from PIL import Image

    from cwagent.vision.equipment import CONFIRM, find_compose_confirm

    icon = Image.open(CONFIRM).convert("RGB")
    for scale in (1.0, 0.85, 1.15):
        w, h = int(icon.width * scale), int(icon.height * scale)
        screen = Image.new("RGB", (1920, 1080), (40, 40, 60))
        screen.paste(icon.resize((w, h)), (1800, 700))
        at = find_compose_confirm(screen)
        assert at is not None and at[0] > 0.9 and at[1] > 0.6
    assert find_compose_confirm(Image.new("RGB", (1920, 1080), (40, 40, 60))) is None
