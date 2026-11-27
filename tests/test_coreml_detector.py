"""Exercise Core ML selection and cache contracts without Apple hardware."""

import json
import shlex
from types import SimpleNamespace

import pytest

from tools import coreml_detector as detector
from tools.training_data import sha256


def install_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(detector, "CACHE_DIR", tmp_path / "cache")
    weights = tmp_path / "drone.pt"
    weights.write_bytes(b"original")
    digest = sha256(weights)
    bundle = detector.cache_path(digest)
    (bundle / "detector.mlmodelc").mkdir(parents=True)
    (bundle / "source.json").write_text(
        json.dumps({"source_sha256": digest, "labels": ["drone"]}), encoding="utf-8"
    )
    return weights, bundle


def test_bundle_must_match_selected_checkpoint(monkeypatch, tmp_path):
    weights, bundle = install_bundle(monkeypatch, tmp_path)
    assert detector.read_bundle(weights, sha256(weights)) == (bundle, ["drone"])
    weights.write_bytes(b"updated")
    with pytest.raises(ValueError, match="변환본을 준비"):
        detector.read_bundle(weights, sha256(weights))


def test_preparation_command_keeps_model_path_as_one_argument(monkeypatch, tmp_path):
    monkeypatch.setattr(detector, "CACHE_DIR", tmp_path / "cache")
    weights = tmp_path / """field model $(sample) '"drone".pt"""
    with pytest.raises(ValueError, match="변환본을 준비") as error:
        detector.read_bundle(weights, "0" * 64)
    command = str(error.value).split(detector.PREPARE_COMMAND, 1)[1]
    assert shlex.split(command) == ["--model", str(weights)]


def test_damaged_bundle_reports_recovery_and_preserves_files(monkeypatch, tmp_path):
    weights, bundle = install_bundle(monkeypatch, tmp_path)
    receipt = bundle / "source.json"
    receipt.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="폴더를 옮긴 뒤") as error:
        detector.read_bundle(weights, sha256(weights))
    assert str(bundle) in str(error.value)
    assert receipt.read_text(encoding="utf-8") == "{}"


@pytest.mark.parametrize(
    ("platform", "arch", "version", "supported"),
    [
        ("darwin", "arm64", "15.0", True),
        ("darwin", "arm64", "27.2.1", True),
        ("darwin", "arm64", "14.9", False),
        ("darwin", "x86_64", "15.0", False),
        ("linux", "arm64", "", False),
    ],
)
def test_native_platform_gate(monkeypatch, platform, arch, version, supported):
    monkeypatch.setattr(detector, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(
        detector, "platform", SimpleNamespace(mac_ver=lambda: (version,), machine=lambda: arch)
    )
    assert detector.supports_coreml() == supported


def test_coreml_uses_matching_fixed_shape_and_original_coordinates(monkeypatch, tmp_path):
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    pytest.importorskip("ultralytics")
    weights, bundle = install_bundle(monkeypatch, tmp_path)
    load_calls = []

    def compiled(path, *, compute_units, function_name):
        assert path == str(bundle / "detector.mlmodelc") and compute_units == "ALL"
        load_calls.append(function_name)

        def predict(data):
            x = data["pixels"]
            height, width = x.shape[2:]
            assert detector.function_name(height, width) == function_name
            assert x.dtype == np.float32 and x[0, 0, -40, 33] == 1.0
            anchors = sum(height // step * (width // step) for step in detector.STRIDES)
            result = np.zeros((1, 5, anchors), dtype=np.float32)
            result[0, :, 0] = (320, 300, 40, 20, 0.9)
            return {"proposals": result}

        return SimpleNamespace(predict=predict)

    ct = SimpleNamespace(
        ComputeUnit=SimpleNamespace(ALL="ALL"), models=SimpleNamespace(CompiledMLModel=compiled)
    )
    monkeypatch.setattr(detector, "load_coreml", lambda: ct)
    model = detector.CoreMLDetector(weights)
    settings = dict(conf=0.25, classes=[0], device="coreml", imgsz=640, verbose=False)
    for shape, expected in (
        ((2160, 3840), [[1800, 1668, 2040, 1788]]),
        ((640, 640), [[300, 290, 340, 310]]),
        ((2160, 3840), [[1800, 1668, 2040, 1788]]),
    ):
        frame = np.zeros((*shape, 3), dtype=np.uint8)
        frame[:, :, 2] = 255
        np.testing.assert_allclose(model.predict(frame, **settings)[0].boxes.xyxy, expected)
    assert load_calls == ["input_384x640", "input_640x640"]
