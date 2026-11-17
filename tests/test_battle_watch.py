import numpy as np
from PIL import Image

from cwagent.orchestrator import battle_watch as bw


def _frame(banner: bool, hp_bar: bool = True) -> Image.Image:
    """深紫底;首领血条掉血的橙色段(9 行,不该当成横幅);阵亡横幅在 (681~1259, 187~236)。"""
    a = np.zeros((1080, 1920, 3), np.uint8)
    a[:] = (66, 61, 114)
    if hp_bar:
        a[130:139, 300:1700] = (200, 95, 80)
    if banner:
        a[187:236, 681:1259] = (184, 89, 87)
        for x in range(760, 1180, 6):              # 白字:笔画只占一行的一半左右
            a[203:220, x:x + 3] = (248, 236, 223)
    return Image.fromarray(a)


def test_banner_rows():
    assert bw.banner_rows(_frame(False)) is None            # 只有血条那一条
    top, bottom = bw.banner_rows(_frame(True))
    assert abs(top - 187) <= 2 and abs(bottom - 236) <= 2
    half = _frame(True).resize((960, 540))                  # 分辨率不同也按比例找
    top, bottom = bw.banner_rows(half)
    assert abs(top - 93) <= 2 and abs(bottom - 118) <= 2


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += max(s, 0.25)


def test_watcher_counts_deaths_and_events(monkeypatch):
    clock = _Clock()

    def scene(t):
        """(阵亡横幅文字, 行动值, 行动值条目在不在)。"""
        if t >= 6:
            return None, None, False
        banner = None
        if 2.0 <= t < 3.0:
            banner = "受到致命攻击，战斗倒计时减少！"         # 停留约 1 秒,连续几帧都看得到,只算一次
        elif 3.5 <= t < 4.0:
            banner = "onxn819超击破"                         # 伤害数字、特效字凑出的碎字,不算敌方提示
        elif 4.0 <= t < 4.6:
            banner = "守护者之影的责罚降临了！"
        av = 300 - int(t * 5) - (35 if t >= 2.0 else 0)
        return banner, av, True

    class Frame:
        def __init__(self, t):
            self.image = scene(t)

    class Device:
        def grab(self):
            return Frame(clock.t)

    monkeypatch.setattr(bw, "banner_rows", lambda im: (187, 236) if im[0] else None)
    monkeypatch.setattr(bw, "banner_text", lambda im, ocr, rows: im[0])
    monkeypatch.setattr(bw, "read_action_value", lambda im, ocr: im[1])
    monkeypatch.setattr(bw, "has_marker", lambda im: im[2])
    monkeypatch.setattr(bw, "read_progress", lambda boxes: None)
    screens = []

    def classify(im):
        screens.append(clock.t)
        return "battle" if im[2] else "battle_end"

    watcher = bw.BattleWatcher(Device(), lambda im, region=None: [], classify=classify, log=lambda m: None,
                               clock=clock, sleep=clock.sleep)
    rep = watcher.run()
    assert len(rep.deaths) == 1
    death = rep.deaths[0]
    # 阵亡前最后一次读数在第 1 秒;阵亡后 0.6 秒补读一次(第 2.75 秒那一帧)
    assert death["t"] == 2.0 and death["av"] == 295 and death["after"] == 300 - int(2.75 * 5) - 35
    assert rep.events == ["守护者之影的责罚降临了！"]
    assert rep.end == "screen=battle_end"
    assert 8.0 <= rep.seconds <= 9.0              # 条目消失 2.5 秒后整屏核对
    assert len(screens) <= 3                       # 整屏识别很少(每 8 秒一次 + 结束时)
    text = rep.text()
    assert "认出阵亡横幅 1 次(第 2 秒,行动值 295→252)" in text and "守护者之影" in text


def test_watcher_stops():
    class Device:
        def grab(self):
            raise AssertionError("停了就不该再截图")

    rep = bw.BattleWatcher(Device(), lambda im, region=None: [], classify=lambda im: "battle",
                           stop=lambda: True).run()
    assert rep.end == "stopped"


def test_av_jumps_are_not_deaths():
    """阵亡只认横幅(维护者 2026-11-16「别猜」):行动值跳变多半是误读,不再按阵亡算,也不在文字里提。"""
    rep = bw.BattleReport()
    rep.samples = [(0.0, 145, None), (1.0, 105, None), (2.0, 88, None), (3.0, 101, None), (4.0, 55, None)]
    rep.curve = [(a, None) for _, a, _ in rep.samples]
    text = rep.text()
    assert "没认出阵亡横幅" in text and "骤降" not in text and "按阵亡算" not in text
    rep.deaths = [{"t": 1.0, "av": 145, "after": 105}]
    assert "认出阵亡横幅 1 次(第 1 秒,行动值 145→105)" in rep.text()
