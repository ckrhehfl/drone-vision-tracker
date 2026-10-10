"""Local YOLO training on CUDA or Apple MPS, with hardware-free preflight."""

import argparse
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from tools.training_data import check_dataset, clear_label_caches, sha256

ROOT = Path(__file__).resolve().parents[1]


def select_device(requested: str, torch) -> str:
    if requested == "auto":
        return (
            "0"
            if torch.cuda.is_available()
            else "mps"
            if torch.backends.mps.is_available()
            else "cpu"
        )
    if requested == "cuda":
        if not torch.cuda.is_available():
            raise ValueError(
                "CUDA is unavailable. Install a CUDA build of PyTorch on an NVIDIA PC."
            )
        return "0"
    if requested == "mps" and not torch.backends.mps.is_available():
        raise ValueError("MPS is unavailable. Check Apple Silicon and the PyTorch/macOS versions.")
    return requested


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--data", type=Path, required=True, help="Local dataset YAML")
    result.add_argument(
        "--manifest", type=Path, required=True, help="Reviewed session manifest JSON"
    )
    result.add_argument(
        "--check-only", action="store_true", help="Validate data without importing AI"
    )
    result.add_argument(
        "--model", type=Path, help="Trusted local detection .pt weights; no download"
    )
    result.add_argument("--device", choices=("auto", "cuda", "mps", "cpu"), default="auto")
    result.add_argument("--epochs", type=int, default=100)
    result.add_argument("--imgsz", type=int, default=640)
    result.add_argument("--batch", type=int, default=4)
    result.add_argument("--seed", type=int, default=0)
    result.add_argument("--output", type=Path, default=Path("runs/train/drone"))
    return result


def check_loaded_data(trainer, summary: dict) -> None:
    """Fail before the first epoch if vendor ingestion differs from checked files."""
    for split, loader in (("train", trainer.train_loader), ("val", trainer.test_loader)):
        actual = {
            "images": len(loader.dataset),
            "objects": sum(len(label["cls"]) for label in loader.dataset.labels),
        }
        if actual != summary["splits"][split]:
            raise RuntimeError(f"YOLO loaded different {split} data: {actual}; reject this run.")


def prepare_runtime():
    # Keep library settings/caches inside the ignored local artifacts directory.
    os.environ.setdefault("YOLO_CONFIG_DIR", str(ROOT / "artifacts" / "ultralytics"))
    os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "artifacts" / "matplotlib"))
    Path(os.environ["YOLO_CONFIG_DIR"]).mkdir(parents=True, exist_ok=True)
    os.environ["YOLO_OFFLINE"] = "true"
    os.environ["YOLO_AUTOINSTALL"] = "false"
    import torch
    from ultralytics import YOLO, settings

    settings.update(
        dict.fromkeys(
            ("sync", "hub", "wandb", "mlflow", "clearml", "comet", "dvc", "neptune", "raytune"),
            False,
        )
    )
    return torch, YOLO


def train(args, spec: dict, summary: dict) -> dict:
    if not args.model or not args.model.is_file() or args.model.suffix != ".pt":
        raise ValueError(
            "--model must be trusted local .pt weights. Automatic download is disabled."
        )
    if args.epochs < 1 or args.batch < 1 or args.imgsz < 32 or args.imgsz % 32 or args.seed < 0:
        raise ValueError(
            "epochs/batch must be positive; imgsz a positive multiple of 32; seed >= 0."
        )
    output = args.output.resolve()
    if output.exists():
        raise ValueError(f"Output already exists; choose a new --output: {output}")
    torch, YOLO = prepare_runtime()
    device = select_device(args.device, torch)
    trainer = None
    if device == "mps":
        from tools.mps_training import MPSDetectionTrainer

        trainer = MPSDetectionTrainer
    model = YOLO(str(args.model.resolve()), task="detect")
    if model.task != "detect":
        raise ValueError("Only detection models are supported.")
    output.mkdir(parents=True, exist_ok=False)
    data_path = output / "dataset.yaml"
    data_path.write_text(yaml.safe_dump(spec), encoding="utf-8")
    options = {
        "data": str(data_path),
        "device": device,
        "epochs": args.epochs,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "seed": args.seed,
        "workers": 0,
        "amp": False,
        "plots": False,
        "project": str(output.parent),
        "name": output.name,
        "exist_ok": True,
        "val": True,
    }
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    record = {
        "status": "running",
        "started_utc": datetime.now(UTC).isoformat(),
        "dataset": summary,
        "model_sha256": sha256(args.model),
        "git_commit": revision.stdout.strip() if revision.returncode == 0 else None,
        "git_dirty": bool(dirty.stdout) if dirty.returncode == 0 else None,
        "training_tool_sha256": sha256(Path(__file__)),
        "data_tool_sha256": sha256(ROOT / "tools/training_data.py"),
        "mps_tool_sha256": sha256(ROOT / "tools/mps_training.py") if device == "mps" else None,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": {
            p: importlib.metadata.version(p) for p in ("torch", "torchvision", "ultralytics")
        },
        "installed_packages": {
            d.metadata["Name"]: d.version for d in importlib.metadata.distributions()
        },
        "mps_blocking_copies": device == "mps",
        "options": options,
        "test_evaluated": False,
        "accuracy_verified": False,
    }
    record_path = output / "training_record.json"
    record_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        clear_label_caches(spec)
        model.add_callback(
            "on_pretrain_routine_end", lambda trainer: check_loaded_data(trainer, summary)
        )
        model.train(trainer=trainer, **options)
        if model.names != {0: "drone"}:
            raise RuntimeError("Trained model does not contain exactly class 0: drone.")
        val_objects = int(sum(model.trainer.validator.metrics.nt_per_class))
        if val_objects != summary["splits"]["val"]["objects"]:
            raise RuntimeError("Validation label count differs from preflight; reject this run.")
        best = output / "weights/best.pt"
        record.update(
            status="completed", best_weights_sha256=sha256(best), validation_objects=val_objects
        )
    except BaseException as exc:
        record.update(status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed")
        raise
    finally:
        record["finished_utc"] = datetime.now(UTC).isoformat()
        record_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return record


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        spec, summary = check_dataset(args.data, args.manifest)
        if args.check_only:
            print(json.dumps(summary, indent=2, ensure_ascii=False))
        else:
            record = train(args, spec, summary)
            print(f"Training completed: {args.output.resolve()} (test evaluation still pending)")
            print(f"Device: {record['options']['device']}")
    except (OSError, ValueError, RuntimeError, ImportError, yaml.YAMLError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
