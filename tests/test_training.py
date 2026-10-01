import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tools.train import main, parser, select_device, train
from tools.training_data import check_dataset, check_labels, clear_label_caches


def dataset(tmp_path):
    """Small byte fixtures exercise integrity checks without installing AI packages."""
    sessions = []
    for split in ("train", "val", "test"):
        image = tmp_path / f"images/{split}/frame.jpg"
        label = tmp_path / f"labels/{split}/frame.txt"
        image.parent.mkdir(parents=True)
        label.parent.mkdir(parents=True)
        image.write_bytes(split.encode())
        label.write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
        sessions.append(
            {"session_id": split, "split": split, "images": [f"images/{split}/frame.jpg"]}
        )
    data = tmp_path / "data.yaml"
    data.write_text(
        yaml.safe_dump(
            {
                "path": ".",
                "train": "images/train",
                "val": "images/val",
                "test": "images/test",
                "names": {0: "drone"},
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {"schema_version": 1, "status": "ready", "dataset_version": "v1", "sessions": sessions}
        ),
        encoding="utf-8",
    )
    return data, manifest


def test_preflight_without_ai_and_test_is_excluded(tmp_path, monkeypatch):
    data, manifest = dataset(tmp_path)
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setitem(sys.modules, "ultralytics", None)
    assert main(["--data", str(data), "--manifest", str(manifest), "--check-only"]) == 0
    spec, summary = check_dataset(data, manifest)
    assert "test" not in spec
    assert summary["splits"]["test"] == {"images": 1, "objects": 1}
    assert len(summary["inventory_sha256"]) == 64


def test_old_label_caches_are_regenerated_without_touching_test(tmp_path):
    data, manifest = dataset(tmp_path)
    spec, _ = check_dataset(data, manifest)
    for split in ("train", "val", "test"):
        (tmp_path / f"labels/{split}.cache").write_bytes(b"stale cache")
    clear_label_caches(spec)
    assert not (tmp_path / "labels/train.cache").exists()
    assert not (tmp_path / "labels/val.cache").exists()
    assert (tmp_path / "labels/test.cache").read_bytes() == b"stale cache"


def test_nested_images_directory_cannot_silently_drop_training_labels(tmp_path):
    data, manifest = dataset(tmp_path)
    image = tmp_path / "images/train/session/images/frame.jpg"
    label = tmp_path / "labels/train/session/images/frame.txt"
    image.parent.mkdir(parents=True)
    label.parent.mkdir(parents=True)
    (tmp_path / "images/train/frame.jpg").rename(image)
    (tmp_path / "labels/train/frame.txt").rename(label)
    contents = json.loads(manifest.read_text())
    contents["sessions"][0]["images"] = ["images/train/session/images/frame.jpg"]
    manifest.write_text(json.dumps(contents))
    with pytest.raises(ValueError, match="Nested 'images'"):
        check_dataset(data, manifest)


@pytest.mark.parametrize("relative", [".session/frame.jpg", ".frame.jpg"])
def test_hidden_positive_cannot_be_replaced_by_visible_background(tmp_path, relative):
    data, manifest = dataset(tmp_path)
    image = tmp_path / "images/train" / relative
    label = tmp_path / "labels/train" / Path(relative).with_suffix(".txt")
    image.parent.mkdir(parents=True, exist_ok=True)
    label.parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "images/train/frame.jpg").rename(image)
    (tmp_path / "labels/train/frame.txt").rename(label)
    (tmp_path / "images/train/background.jpg").write_bytes(b"background")
    (tmp_path / "labels/train/background.txt").write_text("")
    contents = json.loads(manifest.read_text())
    contents["sessions"][0]["images"] = [f"images/train/{relative}", "images/train/background.jpg"]
    manifest.write_text(json.dumps(contents))
    with pytest.raises(ValueError, match="Hidden image"):
        check_dataset(data, manifest)


def test_dataset_root_with_glob_brackets_is_rejected(tmp_path):
    root = tmp_path / "session[1]"
    root.mkdir()
    data, manifest = dataset(root)
    with pytest.raises(ValueError, match="glob characters"):
        check_dataset(data, manifest)


@pytest.mark.parametrize(
    "failure", ["session", "duplicate", "missing", "outside", "label", "download"]
)
def test_dataset_rejects_leaks_and_incomplete_inputs(tmp_path, failure):
    data, manifest = dataset(tmp_path)
    contents = json.loads(manifest.read_text())
    if failure == "session":
        contents["sessions"][1]["session_id"] = "train"
    elif failure == "duplicate":
        (tmp_path / "images/val/frame.jpg").write_bytes(b"train")
    elif failure == "missing":
        contents["sessions"][0]["images"] = ["images/train/missing.jpg"]
    elif failure == "outside":
        contents["sessions"][0]["images"] = ["../frame.jpg"]
    elif failure == "label":
        (tmp_path / "labels/train/frame.txt").unlink()
    else:
        data.write_text(data.read_text() + "download: https://example.invalid/data.zip\n")
    manifest.write_text(json.dumps(contents))
    with pytest.raises((ValueError, OSError)):
        check_dataset(data, manifest)


@pytest.mark.parametrize(
    "row",
    ["1 .5 .5 .2 .2", "0 nan .5 .2 .2", "0 .5 .5 0 .2", "0 .1 .5 .8 .2", "0 .5 .5 .2"],
)
def test_invalid_labels_are_rejected(tmp_path, row):
    label = tmp_path / "label.txt"
    label.write_text(row)
    with pytest.raises(ValueError):
        check_labels(label)
    label.write_text("")
    assert check_labels(label) == 0


@pytest.mark.parametrize(
    ("cuda", "mps", "expected"), [(True, True, "0"), (False, True, "mps"), (False, False, "cpu")]
)
def test_device_selection_and_explicit_unavailable_device(cuda, mps, expected):
    torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: cuda),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps)),
    )
    assert select_device("auto", torch) == expected
    if not cuda:
        with pytest.raises(ValueError, match="CUDA"):
            select_device("cuda", torch)
    if not mps:
        with pytest.raises(ValueError, match="MPS"):
            select_device("mps", torch)


@pytest.mark.parametrize("outcome", ["completed", "training_error", "ingestion_error"])
def test_training_records_outcome_and_does_not_overwrite(tmp_path, monkeypatch, outcome):
    data, manifest = dataset(tmp_path)
    weights = tmp_path / "base.pt"
    weights.write_bytes(b"local trusted test weights")
    output = tmp_path / "output"
    args = parser().parse_args(
        [
            "--data",
            str(data),
            "--manifest",
            str(manifest),
            "--model",
            str(weights),
            "--device",
            "cpu",
            "--output",
            str(output),
        ]
    )
    spec, summary = check_dataset(data, manifest)

    class LoadedDataset(list):
        @property
        def labels(self):
            return self

    class FakeYOLO:
        task = "detect"
        names = {0: "drone"}
        trainer = SimpleNamespace(
            validator=SimpleNamespace(metrics=SimpleNamespace(nt_per_class=[1])),
            train_loader=SimpleNamespace(
                dataset=LoadedDataset([{"cls": [] if outcome == "ingestion_error" else [0]}])
            ),
            test_loader=SimpleNamespace(dataset=LoadedDataset([{"cls": [0]}])),
        )

        def __init__(self, *args, **kwargs):
            pass

        def add_callback(self, event, callback):
            assert event == "on_pretrain_routine_end"
            self.callback = callback

        def train(self, **options):
            saved_data = yaml.safe_load(Path(options["data"]).read_text())
            assert "test" not in saved_data
            assert options["device"] == "cpu"
            assert options["amp"] is False
            self.callback(self.trainer)
            if outcome == "training_error":
                raise RuntimeError("training failed")
            (output / "weights").mkdir()
            (output / "weights/best.pt").write_bytes(b"new weights")

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=FakeYOLO, settings={}))
    monkeypatch.setattr("importlib.metadata.version", lambda name: "test")
    if outcome != "completed":
        with pytest.raises(RuntimeError, match="training failed|loaded different"):
            train(args, spec, summary)
    else:
        train(args, spec, summary)
    record = json.loads((output / "training_record.json").read_text())
    assert record["status"] == ("completed" if outcome == "completed" else "failed")
    assert record["test_evaluated"] is False
    assert record["accuracy_verified"] is False
    assert "finished_utc" in record
    with pytest.raises(ValueError, match="already exists"):
        train(args, spec, summary)


@pytest.mark.skipif(
    os.environ.get("RUN_TRAINING_SMOKE") != "1",
    reason="Opt-in synthetic training needs the training dependencies and a selected device.",
)
def test_synthetic_training_smoke(tmp_path, monkeypatch):
    from PIL import Image, ImageDraw

    monkeypatch.setenv("YOLO_CONFIG_DIR", str(tmp_path / "settings"))
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    monkeypatch.setenv("YOLO_OFFLINE", "true")
    monkeypatch.setenv("YOLO_AUTOINSTALL", "false")
    (tmp_path / "settings").mkdir()
    from ultralytics import YOLO

    data, manifest = dataset(tmp_path)
    for index, split in enumerate(("train", "val", "test")):
        image = Image.new("RGB", (96, 96), color=(40 + index * 40, 30, 50))
        ImageDraw.Draw(image).rectangle((38, 38, 58, 58), fill=(255, 255, 255))
        image.save(tmp_path / f"images/{split}/frame.jpg")
    weights = tmp_path / "random.pt"
    YOLO("yolo11n.yaml").save(str(weights))  # Packaged architecture, random weights; no download.
    output = tmp_path / "smoke"
    assert (
        main(
            [
                "--data",
                str(data),
                "--manifest",
                str(manifest),
                "--model",
                str(weights),
                "--device",
                os.environ.get("TRAINING_SMOKE_DEVICE", "cpu"),
                "--epochs",
                "1",
                "--imgsz",
                "64",
                "--batch",
                "1",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    record = json.loads((output / "training_record.json").read_text())
    assert record["status"] == "completed"
    assert record["validation_objects"] == 1
    assert (output / "weights/best.pt").is_file()
    assert record["test_evaluated"] is False
