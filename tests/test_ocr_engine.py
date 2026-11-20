"""文字识别引擎:能用 DirectML 就用显卡,初始化失败退回 CPU;整个进程共用一个引擎(2026-11-20)。"""

import pytest

from cwagent.vision import ocr


class FakeRapid:
    built: list[dict] = []
    fail_gpu = False

    def __init__(self, params=None):
        params = params or {}
        FakeRapid.built.append(params)
        self.gpu = bool(params.get("EngineConfig.onnxruntime.use_dml"))

    def __call__(self, img, **kw):
        if self.gpu and FakeRapid.fail_gpu:
            raise RuntimeError("no adapter")
        return type("R", (), {"boxes": None, "txts": None, "scores": None})()


@pytest.fixture(autouse=True)
def fake(monkeypatch):
    import rapidocr

    monkeypatch.setattr(rapidocr, "RapidOCR", FakeRapid)
    monkeypatch.setattr(ocr, "_ENGINES", {})
    FakeRapid.built, FakeRapid.fail_gpu = [], False


def test_uses_directml_when_available_and_shares_the_engine(monkeypatch):
    monkeypatch.setattr(ocr, "directml_available", lambda: True)
    a, b = ocr.Ocr(), ocr.Ocr()
    assert a.backend == "DirectML" and b._get() is a._get() and len(FakeRapid.built) == 1


def test_falls_back_to_cpu_when_directml_fails(monkeypatch):
    monkeypatch.setattr(ocr, "directml_available", lambda: True)
    FakeRapid.fail_gpu = True
    assert ocr.Ocr().backend == "CPU" and ocr.Ocr()(ocr._warmup_image()) == []


def test_cpu_setting_never_touches_the_gpu(monkeypatch):
    monkeypatch.setattr(ocr, "directml_available", lambda: True)
    assert ocr.Ocr(device="cpu").backend == "CPU"
    assert all(not p.get("EngineConfig.onnxruntime.use_dml") for p in FakeRapid.built)
