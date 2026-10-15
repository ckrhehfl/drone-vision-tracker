"""Install a pinned Seraphim-trained quadcopter detector; never train or open a camera."""

import argparse
import json
import shutil
import urllib.request
from pathlib import Path

from tools.training_data import sha256
from tools.validate_config import strict_json

REPOSITORY = "TomSmail/drone-yolo-v1"
REVISION = "a76fa3c8b60cb7861f8b3a90017340d1069afb9c"
SOURCE_SHA256 = "bf24a20e69b28896a0c7e4855c72146149fa5c25845fa5e54beda2b93cf79824"
SOURCE_URL = f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/best.pt"
ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "models/local/seraphim-yolov8n"
SOURCE = ROOT / "artifacts/seraphim-model/source.pt"


def installed_model(root=MODEL_DIR):
    """Only select a complete local install whose weights still match its receipt."""
    root = Path(root)
    try:
        receipt = strict_json((root / "source.json").read_text(encoding="utf-8"))
        weights = root / "best.pt"
        if (
            isinstance(receipt, dict)
            and receipt.get("status") == "installed"
            and receipt.get("source_sha256") == SOURCE_SHA256
            and receipt.get("revision") == REVISION
            and receipt.get("weights_sha256") == sha256(weights)
        ):
            return weights.resolve()
    except (OSError, ValueError):
        pass
    return None


def retain_quadcopter(model, torch):
    if model.task != "detect" or model.names != {0: "quadcopter", 1: "fixed-wing"}:
        raise ValueError("고정된 원본의 검출 클래스가 다릅니다.")
    head = model.model.model[-1]
    if type(head).__name__ != "Detect" or head.nc != 2 or head.end2end:
        raise ValueError("지원하는 YOLOv8 검출 헤드가 아닙니다.")
    # Keep learned class-0 scores and box regression; remove the other class channel.
    for branch in head.cv3:
        old = branch[-1]
        if old.out_channels != 2 or old.kernel_size != (1, 1) or old.bias is None:
            raise ValueError("분류 헤드 구조가 다릅니다.")
        replacement = torch.nn.Conv2d(old.in_channels, 1, 1).to(old.weight)
        with torch.no_grad():
            replacement.weight.copy_(old.weight[:1])
            replacement.bias.copy_(old.bias[:1])
        branch[-1] = replacement
    head.nc, head.no = 1, head.reg_max * 4 + 1
    model.model.yaml["nc"] = 1
    model.model.names = {0: "drone"}
    if hasattr(model.model, "criterion"):
        del model.model.criterion  # Rebuild loss for one class when fine-tuning.
    model.ckpt = {}  # The source's two-class optimizer/metrics do not describe this export.


def install(source=SOURCE, root=MODEL_DIR, download=False):
    source, root = Path(source), Path(root)
    if root.exists():
        if installed_model(root):
            return root / "best.pt"
        raise ValueError("모델 폴더가 이미 있습니다. 기존 자료를 확인하고 보존하세요.")
    if not source.exists() and download:
        source.parent.mkdir(parents=True, exist_ok=True)
        partial = source.with_suffix(".pt.part")
        with urllib.request.urlopen(SOURCE_URL, timeout=60) as response, partial.open("xb") as file:
            shutil.copyfileobj(response, file)
        if sha256(partial) != SOURCE_SHA256:
            raise ValueError("원본 가중치 SHA-256이 다릅니다. 다운로드 파일을 확인하세요.")
        partial.rename(source)
    if not source.is_file() or sha256(source) != SOURCE_SHA256:
        raise ValueError("고정 원본 가중치가 없습니다. --download로 준비하거나 해시를 확인하세요.")
    from tools.train import prepare_runtime

    torch, YOLO = prepare_runtime()
    model = YOLO(str(source.resolve()), task="detect")
    retain_quadcopter(model, torch)
    root.mkdir(parents=True, exist_ok=False)
    weights = root / "best.pt"
    model.save(str(weights))
    if YOLO(str(weights), task="detect").names != {0: "drone"}:
        raise ValueError("저장한 단일 드론 클래스 모델을 확인할 수 없습니다.")
    (root / "source.json").write_text(
        json.dumps(
            {
                "status": "installed",
                "repository": REPOSITORY,
                "revision": REVISION,
                "source_sha256": SOURCE_SHA256,
                "weights_sha256": sha256(weights),
                "license_declared_by_publisher": "CC-BY-4.0",
                "source_class": "0: quadcopter",
                "installed_class": "0: drone",
                "other_class_channel_removed": True,
                "trained_locally": False,
                "physical_accuracy_verified": False,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return weights


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="Fetch the pinned public weights")
    args = parser.parse_args(argv)
    try:
        print(f"Installed: {install(download=args.download)}")
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        print(f"모델 준비 실패: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
