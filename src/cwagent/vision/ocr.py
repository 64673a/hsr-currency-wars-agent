"""文字识别(RapidOCR)。坐标全部归一化到 0~1,与截图分辨率无关。

Windows 上能用 DirectML 就用显卡跑(M7A 的默认做法):整屏一次约 0.18 秒,CPU 约 0.5 秒;同一套检测参数下两者
读出的文字逐框一致(2026-11-20 Windows 实测)。显卡初始化失败时退回 CPU。整个进程共用一个引擎:加载模型、
初始化显卡都要时间,网页的只读截图不必每次重来。
"""

from __future__ import annotations

import logging
import re
import sys
import threading
import time
from dataclasses import dataclass, replace

from PIL import Image, ImageDraw


@dataclass(frozen=True)
class TextBox:
    text: str
    x: float       # 中心点
    y: float
    w: float
    h: float
    score: float

    def __str__(self) -> str:
        return f"({self.x:.3f},{self.y:.3f}) {self.text}"


_ENGINES: dict[str, tuple[object, str]] = {}   # 设置(auto / cpu)→(引擎, 实际用的:DirectML / CPU)
_BUILD = threading.Lock()
_RUN = threading.Lock()     # 同一个引擎不并发调用(DirectML 不支持同一会话并发推理)


def directml_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import onnxruntime

        return "DmlExecutionProvider" in onnxruntime.get_available_providers()
    except Exception:  # noqa: BLE001 - 装的不是带 DirectML 的 onnxruntime 就当没有
        return False


def _warmup_image() -> Image.Image:
    """预热用的图:第一次在显卡上跑要编译程序,带几个字才会把检测和识别都跑一遍。"""
    img = Image.new("RGB", (320, 96), "white")
    ImageDraw.Draw(img).text((20, 30), "0123 OCR", fill="black")
    return img.resize((640, 192))


def engine(device: str = "auto") -> tuple[object, str]:
    """进程里共用的识别引擎和它实际用的设备。device=auto 时能用 DirectML 就用显卡,否则 CPU;cpu 只用 CPU。"""
    with _BUILD:
        if device not in _ENGINES:
            logging.getLogger("RapidOCR").setLevel(logging.WARNING)
            from rapidocr import RapidOCR

            built = None
            if device == "auto" and directml_available():
                try:
                    gpu = RapidOCR(params={"EngineConfig.onnxruntime.use_dml": True})
                    gpu(_warmup_image(), use_det=True, use_cls=False, use_rec=True)
                    built = (gpu, "DirectML")
                except Exception as e:  # noqa: BLE001 - 显卡用不了就用 CPU,不影响对局
                    logging.getLogger(__name__).warning("文字识别用显卡(DirectML)初始化失败,改用 CPU:%s", e)
            _ENGINES[device] = built or (RapidOCR(), "CPU")
        return _ENGINES[device]


class Ocr:
    def __init__(self, min_score: float = 0.55, device: str = "auto"):
        self.min_score = min_score
        self.device = device       # auto / cpu
        self._engine = None

    def _get(self):
        if self._engine is None:
            self._engine = engine(self.device)[0]
        return self._engine

    @property
    def backend(self) -> str:
        """实际用的设备:DirectML / CPU(会先把引擎建好)。"""
        self._get()
        return engine(self.device)[1]

    def __call__(self, image: Image.Image, region: tuple[float, float, float, float] | None = None) -> list[TextBox]:
        """识别整张图或其中一个区域(x, y, 宽, 高,归一化)。返回的坐标仍相对整张图。"""
        img = image.convert("RGB")
        W, H = img.size
        ox = oy = 0.0
        if region:
            x, y, w, h = region
            box = (round(x * W), round(y * H), round((x + w) * W), round((y + h) * H))
            img = img.crop(box)
            ox, oy = box[0] / W, box[1] / H
        # 不做方向分类:游戏界面上没有倒着的字,分类器反而会把短数字判成倒置再翻过来认
        # (2026-11-18 那局「9/9」被认成「6/6」,3-4 到 3-7 上场人数一直报错)
        run = self._get()
        with _RUN:
            res = run(img, use_det=True, use_cls=False, use_rec=True)
        out: list[TextBox] = []
        if res.boxes is None:
            return out
        for pts, text, score in zip(res.boxes, res.txts, res.scores, strict=False):
            if score < self.min_score or not text.strip():
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            out.append(TextBox(text=text.strip(), x=ox + (min(xs) + max(xs)) / 2 / W, y=oy + (min(ys) + max(ys)) / 2 / H,
                               w=(max(xs) - min(xs)) / W, h=(max(ys) - min(ys)) / H, score=float(score)))
        out.sort(key=lambda b: (round(b.y, 2), b.x))
        return out

    def line(self, image: Image.Image, *, min_score: float = 0.9) -> str:
        """已定位的单行文字直接识别,避免检测器漏掉单个数字。"""
        run = self._get()
        with _RUN:
            res = run(image.convert("RGB"), use_det=False, use_cls=False, use_rec=True)
        return "".join(text for text, score in zip(res.txts or [], res.scores or [], strict=False)
                       if score >= max(self.min_score, min_score)).strip()


def find(boxes: list[TextBox], pattern: str, *, region: tuple[float, float, float, float] | None = None) -> TextBox | None:
    """第一个文字匹配正则 pattern(且中心落在 region 内)的框。"""
    rx = re.compile(pattern)
    for b in boxes:
        if region and not (region[0] <= b.x <= region[0] + region[2] and region[1] <= b.y <= region[1] + region[3]):
            continue
        if rx.search(b.text):
            return b
    return None


def has(boxes: list[TextBox], *patterns: str) -> bool:
    return all(find(boxes, p) for p in patterns)


class Aliased:
    """把界面文字里的别名换成知识库里的名字后再交出去,其余照原样。开拓者在商店、补给、角色详情里显示的是
    玩家自己取的名字(M7A 也要用户配置这个名字:currencywars_remembrance_trailblazer_name)。"""

    def __init__(self, ocr, aliases: dict[str, str]):
        self.ocr = ocr
        self.aliases = {k.strip(): v for k, v in aliases.items() if k.strip()}

    def __call__(self, image: Image.Image, region: tuple[float, float, float, float] | None = None) -> list[TextBox]:
        boxes = self.ocr(image, region) if region is not None else self.ocr(image)
        return [replace(b, text=self.aliases[b.text]) if b.text in self.aliases else b for b in boxes]

    def line(self, image: Image.Image, *, min_score: float = 0.9) -> str:
        text = self.ocr.line(image, min_score=min_score)
        return self.aliases.get(text, text)


class Timed:
    """记下识别了几次、一共用了多久(每一步的耗时统计用),识别本身原样转给被包的 OCR。"""

    def __init__(self, ocr):
        self.ocr = ocr
        self.calls = 0
        self.seconds = 0.0

    def _count(self, started: float) -> None:
        self.calls += 1
        self.seconds += time.perf_counter() - started

    def __call__(self, image: Image.Image, region: tuple[float, float, float, float] | None = None) -> list[TextBox]:
        started = time.perf_counter()
        try:
            return self.ocr(image, region) if region is not None else self.ocr(image)
        finally:
            self._count(started)

    def line(self, image: Image.Image, *, min_score: float = 0.9) -> str:
        started = time.perf_counter()
        try:
            return self.ocr.line(image, min_score=min_score)
        finally:
            self._count(started)
