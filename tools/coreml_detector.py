"""Prepare and run a local Core ML version of a trusted YOLO detector."""

import argparse
import json
import platform
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from tools.training_data import sha256
from tools.validate_config import strict_json

INPUT_SIZE = 640
STRIDES = (8, 16, 32)
SIZES = (
    *((side, INPUT_SIZE) for side in range(STRIDES[-1], INPUT_SIZE + 1, STRIDES[-1])),
    *((INPUT_SIZE, side) for side in range(STRIDES[-1], INPUT_SIZE, STRIDES[-1])),
)
CACHE_DIR = Path(__file__).resolve().parents[1] / "models/local/coreml"
PREPARE_COMMAND = ".venv/bin/python -m tools.coreml_detector"
INSTALL_COMMAND = ".venv/bin/python -m pip install -r requirements-coreml-macos.txt"


def supports_coreml():
    version = platform.mac_ver()[0].split(".", 1)[0]
    return (
        sys.platform == "darwin"
        and platform.machine() == "arm64"
        and version.isdecimal()
        and int(version) >= 15
    )


def load_coreml():
    if not supports_coreml():
        raise ValueError("Core ML 자동 검출은 Apple Silicon과 macOS 15 이상이 필요합니다.")
    try:
        import coremltools
    except ImportError as exc:
        raise ValueError(f"Core ML 패키지를 설치하세요. {INSTALL_COMMAND}") from exc
    return coremltools


def function_name(height, width):
    return f"input_{height}x{width}"


def cache_path(weights_digest):
    return CACHE_DIR / f"v1-{weights_digest}"


def read_bundle(weights, digest):
    bundle = cache_path(digest)
    try:
        record = strict_json((bundle / "source.json").read_text(encoding="utf-8"))
        labels = record["labels"]
        if (
            record["source_sha256"] != digest
            or not isinstance(labels, list)
            or not all(isinstance(label, str) for label in labels)
            or not any(label.lower() == "drone" for label in labels)
            or not (bundle / "detector.mlmodelc").is_dir()
        ):
            raise ValueError("invalid Core ML bundle")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(
            f'이 모델의 Core ML 변환본을 준비하세요. {PREPARE_COMMAND} --model "{weights}"'
        ) from exc
    return bundle, labels


class CoreMLDetector:
    task = "detect"

    def __init__(self, weights):
        self.ct = load_coreml()
        self.bundle, labels = read_bundle(weights, sha256(Path(weights)))
        self.names = dict(enumerate(labels))
        self.models = {}

        import torch
        from ultralytics.models.yolo.detect import DetectionPredictor

        self.torch = torch
        self.predictor = DetectionPredictor(overrides={"task": "detect", "rect": True})
        self.predictor.imgsz = [INPUT_SIZE, INPUT_SIZE]
        self.predictor.device = torch.device("cpu")
        self.predictor.model = SimpleNamespace(
            pt=True, fp16=False, stride=STRIDES[-1], names=self.names
        )
        self.predictor.batch = (["camera"],)

    def predict(self, frame, *, conf, classes, device, imgsz, verbose):
        import numpy as np

        if imgsz != INPUT_SIZE:
            raise ValueError(f"Core ML YOLO 입력 크기는 {INPUT_SIZE}입니다.")
        predictor = self.predictor
        predictor.args.conf = conf
        predictor.args.classes = classes
        with self.torch.inference_mode():
            pixels = predictor.preprocess([frame])
            height, width = pixels.shape[-2:]
            size = (height, width)
            if size not in self.models:
                self.models[size] = self.ct.models.CompiledMLModel(
                    str(self.bundle / "detector.mlmodelc"),
                    compute_units=self.ct.ComputeUnit.ALL,
                    function_name=function_name(*size),
                )
            proposals = self.models[size].predict({"pixels": pixels.numpy()})["proposals"]
            anchors = sum(height // stride * (width // stride) for stride in STRIDES)
            if (
                proposals.shape != (1, 4 + len(self.names), anchors)
                or not np.isfinite(proposals).all()
            ):
                raise ValueError("Core ML의 검출 결과 형식이 예상과 다릅니다.")
            return predictor.postprocess(self.torch.from_numpy(proposals.copy()), pixels, [frame])


def prepare(weights):
    ct = load_coreml()
    import numpy as np
    import torch
    from ultralytics import YOLO
    from ultralytics.nn.modules.head import Detect

    weights = Path(weights).expanduser().resolve()
    if weights.suffix.lower() != ".pt" or not weights.is_file():
        raise ValueError("신뢰하는 로컬 드론 학습 .pt 파일을 선택하세요.")
    digest = sha256(weights)
    output = cache_path(digest)
    if output.exists():
        return read_bundle(weights, digest)[0]

    model = YOLO(str(weights))
    if model.task != "detect" or not any(
        label.lower() == "drone" for label in model.names.values()
    ):
        raise ValueError("drone 클래스를 포함한 YOLO 검출 .pt 모델을 선택하세요.")
    net = model.model.float().cpu().eval().fuse(verbose=False)
    head = net.model[-1]
    if (
        type(head) is not Detect
        or head.end2end
        or tuple(int(stride) for stride in head.stride) != STRIDES
    ):
        raise ValueError("이 YOLO 검출 헤드는 Core ML 변환을 지원하지 않습니다.")
    head.export, head.dynamic, head.format = True, False, "coreml"
    labels = [model.names[index] for index in range(head.nc)]

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".prepare-", dir=CACHE_DIR) as work:
        work = Path(work)
        bundle = work / "ready"
        bundle.mkdir()
        descriptor = ct.utils.MultiFunctionDescriptor()
        descriptor.default_function_name = function_name(384, INPUT_SIZE)
        # Static functions preserve YOLO's rectangular letterbox without a flexible Metal graph.
        for height, width in SIZES:
            name = function_name(height, width)
            path = work / f"{name}.mlpackage"
            pixels = torch.zeros((1, 3, height, width))
            with torch.inference_mode():
                net(pixels)  # Cache the grid for exactly this static shape before tracing.
                traced = torch.jit.trace(net, pixels, strict=False, check_trace=False)
            program = ct.convert(
                traced,
                convert_to="mlprogram",
                minimum_deployment_target=ct.target.macOS15,
                compute_precision=ct.precision.FLOAT16,
                inputs=[ct.TensorType(name="pixels", shape=pixels.shape, dtype=np.float32)],
                outputs=[ct.TensorType(name="proposals", dtype=np.float32)],
                skip_model_load=True,
            )
            program.save(str(path))
            descriptor.add_function(str(path), "main", name)
        package = work / "detector.mlpackage"
        ct.utils.save_multifunction(descriptor, str(package))
        ct.utils.compile_model(str(package), destination_path=str(bundle / "detector.mlmodelc"))
        if sha256(weights) != digest:
            raise ValueError("변환 중 원본 .pt 모델이 변경됐습니다. 다시 변환하세요.")
        (bundle / "source.json").write_text(
            json.dumps({"source_sha256": digest, "labels": labels}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        try:
            bundle.rename(output)
        except OSError:
            if not output.exists():
                raise
            return read_bundle(weights, digest)[0]
    return output


def main():
    from tools.pretrained_drone import installed_field_model, installed_model

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, help="신뢰하는 로컬 드론 .pt 모델")
    args = parser.parse_args()
    weights = args.model or installed_field_model() or installed_model()
    if weights is None:
        parser.error("먼저 로컬 드론 .pt 모델을 설치하거나 --model로 지정하세요.")
    try:
        print(f"Core ML 준비 완료 · {prepare(weights)}")
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
