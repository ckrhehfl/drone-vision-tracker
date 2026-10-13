"""MPS routing and ViT output decoding without importing any ML runtime."""

import sys
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from tools.manual_vit import MPSVitTracker


def test_mps_unavailable_is_explicit_and_does_not_run_cpu(monkeypatch):
    torch = SimpleNamespace(
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False))
    )
    monkeypatch.setitem(sys.modules, "torch", torch)
    with pytest.raises(ValueError, match="장치를 cpu로 선택"):
        MPSVitTracker("unused.onnx", 0.65)


def test_mps_restarts_reuse_network_but_keep_target_state_separate(monkeypatch):
    import tools.manual_vit as vit

    conversions, traces, warmups = [], [], []

    class Runtime:
        def eval(self):
            return self

        def to(self, device):
            assert device == "mps"
            return self

        def __call__(self, *samples):
            assert samples == ("mps sample", "mps sample")
            warmups.append(samples)

    sample = SimpleNamespace(to=lambda device: f"{device} sample")
    tensor = SimpleNamespace(view=lambda *shape: "constant")
    torch = SimpleNamespace(
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: True)),
        inference_mode=nullcontext,
        zeros=lambda *shape: sample,
        tensor=lambda *args, **kwargs: tensor,
        jit=SimpleNamespace(
            TracerWarning=UserWarning,
            trace=lambda model, samples: traces.append(samples) or model,
            freeze=lambda model: model,
        ),
        mps=SimpleNamespace(synchronize=lambda: None),
    )
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(
        sys.modules,
        "onnx2torch",
        SimpleNamespace(convert=lambda path: conversions.append(path) or Runtime()),
    )
    vit._mps_model.cache_clear()
    try:
        first, second = MPSVitTracker("pinned.onnx", 0.65), MPSVitTracker("pinned.onnx", 0.8)
        assert conversions == ["pinned.onnx"] and len(traces) == 1
        assert len(warmups) == 3
        assert first.model is second.model
        first._crop = lambda *args: ("first template", 128, (0, 0))
        second._crop = lambda *args: ("second template", 128, (0, 0))
        first.init("frame", (10, 20, 30, 40))
        second.init("frame", (50, 60, 70, 80))
        assert (first.rect, first.template, first.threshold) == (
            (10, 20, 30, 40),
            "first template",
            0.65,
        )
        assert (second.rect, second.template, second.threshold) == (
            (50, 60, 70, 80),
            "second template",
            0.8,
        )
    finally:
        vit._mps_model.cache_clear()


@pytest.fixture
def vit():
    tracker = MPSVitTracker.__new__(MPSVitTracker)
    tracker.rect, tracker.threshold, tracker.score = (3, 4, 20, 20), 0.65, 0.0
    tracker.window = [1.0] * 256
    return tracker


def maps():
    values, index = [0.0] * 1280, 6 * 16 + 8
    values[index] = 0.9
    values[256 + index], values[512 + index] = 0.25, 0.125
    values[768 + index] = values[1024 + index] = 0.5
    return values, index


def test_vit_decodes_windowed_peak_to_camera_pixels(vit):
    values, _ = maps()
    values[0], vit.window[0] = 0.98, 0.1
    assert vit._decode(values, 128, (100, 200)) == (True, (152, 244, 32, 16))
    assert vit.rect == (152, 244, 32, 16) and vit.getTrackingScore() == 0.9


@pytest.mark.parametrize("invalid", ["low_score", "nan", "infinite", "small", "negative", "length"])
def test_vit_rejects_invalid_observations_without_moving(vit, invalid):
    values, index = maps()
    if invalid == "low_score":
        values[index] = 0.64
    elif invalid == "nan":
        values[index] = float("nan")
    elif invalid == "infinite":
        values[768 + index] = float("inf")
    elif invalid == "small":
        values[256 + index] = 0.01
    elif invalid == "negative":
        values[512 + index] = -0.2
    else:
        values.pop()
    assert vit._decode(values, 128, (100, 200)) == (False, (3, 4, 20, 20))
    assert vit.rect == (3, 4, 20, 20)


def test_vit_rejects_unexpected_model_output_shape(monkeypatch, vit):
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(inference_mode=nullcontext))
    vit.template = "template"
    vit._crop = lambda *args: ("search", 128, (100, 200))
    vit.model = lambda *args: [SimpleNamespace(shape=(1, 1, 8, 8))]
    with pytest.raises(ValueError, match="출력 크기"):
        vit.update("frame")
