"""文字识别引擎:按设置用显卡(DirectML,可选哪块)或 CPU,显卡用不了时如实退回 CPU;同样的设置共用一个引擎(2026-11-20)。"""

import pytest

from cwagent.vision import adapters as adapters_mod
from cwagent.vision import ocr
from cwagent.vision.adapters import Adapter, pick

CARDS = [Adapter(0, "NVIDIA GeForce RTX 5060 Ti", 7891), Adapter(2, "AMD Radeon(TM) Graphics", 485)]


class FakeRapid:
    built: list[dict] = []
    fail_gpu = False          # 初始化就报错
    silent_cpu = False        # onnxruntime 不报错、悄悄退回 CPU

    def __init__(self, params=None):
        params = dict(params or {})
        FakeRapid.built.append(params)
        self.gpu = bool(params.get("EngineConfig.onnxruntime.use_dml"))
        provider = "DmlExecutionProvider" if self.gpu and not FakeRapid.silent_cpu else "CPUExecutionProvider"
        session = type("S", (), {"session": type("I", (), {"get_providers": lambda _s: [provider, "CPUExecutionProvider"]})()})()
        self.text_det = self.text_rec = type("P", (), {"session": session})()

    def __call__(self, img, **kw):
        if self.gpu and FakeRapid.fail_gpu:
            raise RuntimeError("no adapter")
        return type("R", (), {"boxes": None, "txts": None, "scores": None})()


@pytest.fixture(autouse=True)
def fake(monkeypatch):
    import rapidocr

    monkeypatch.setattr(rapidocr, "RapidOCR", FakeRapid)
    monkeypatch.setattr(ocr, "_ENGINES", {})
    monkeypatch.setattr(ocr, "_SETTINGS", {"device": "gpu", "adapter": None})
    monkeypatch.setattr(ocr, "directml_available", lambda: True)
    monkeypatch.setattr(adapters_mod, "list_adapters", lambda: list(CARDS))
    FakeRapid.built, FakeRapid.fail_gpu, FakeRapid.silent_cpu = [], False, False


def device_ids():
    return [(p.get("EngineConfig.onnxruntime.dml_ep_cfg") or {}).get("device_id") for p in FakeRapid.built]


def test_auto_picks_the_card_with_most_memory_and_shares_the_engine():
    a, b = ocr.Ocr(), ocr.Ocr()
    assert a.backend == "显卡:NVIDIA GeForce RTX 5060 Ti" and b._get() is a._get()
    assert device_ids() == [0]


def test_chosen_card_is_used_and_a_missing_one_falls_back_to_auto():
    assert ocr.Ocr(device="gpu", adapter=2).backend == "显卡:AMD Radeon(TM) Graphics"
    assert ocr.Ocr(device="gpu", adapter=7).backend == "显卡:NVIDIA GeForce RTX 5060 Ti"
    assert device_ids() == [2, 0]


def test_reports_cpu_when_onnxruntime_silently_falls_back():
    """2026-11-20 实测:显卡选项格式不对时 onnxruntime 只打印错误、退回 CPU。这时不能显示成「显卡」。"""
    FakeRapid.silent_cpu = True
    assert ocr.Ocr().backend == "CPU"


def test_falls_back_to_cpu_when_directml_fails():
    FakeRapid.fail_gpu = True
    assert ocr.Ocr().backend == "CPU" and ocr.Ocr()(ocr._warmup_image()) == []


def test_cpu_setting_never_touches_the_gpu():
    assert ocr.Ocr(device="cpu").backend == "CPU"
    assert all(not p.get("EngineConfig.onnxruntime.use_dml") for p in FakeRapid.built)


def test_new_ocr_follows_the_configured_settings():
    ocr.configure("gpu", 2)
    assert ocr.Ocr().backend == "显卡:AMD Radeon(TM) Graphics"
    ocr.configure("cpu", None)
    assert ocr.Ocr().backend == "CPU"


def test_pick_prefers_the_most_memory_then_the_lower_index():
    assert pick(CARDS, None).index == 0 and pick(CARDS, 2).index == 2 and pick(CARDS, 9).index == 0
    assert pick([Adapter(3, "A", 100), Adapter(1, "B", 100)], None).index == 1 and pick([], None) is None
