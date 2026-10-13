import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import tools.retrain as retrain
from tools.train import main as train_main
from tools.training_data import check_dataset, sha256


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def learning_plan(tmp_path, monkeypatch):
    class Image:
        size = (100, 100)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def verify(self):
            pass

    monkeypatch.setitem(
        sys.modules, "PIL", SimpleNamespace(Image=SimpleNamespace(open=lambda p: Image()))
    )
    sources = []
    for split in ("train", "val", "test"):
        root = tmp_path / split
        (root / "frames").mkdir(parents=True)
        frames, samples, labels = [], [], []
        for index, target in enumerate(([10, 20, 30, 60], None), 1):
            image = root / f"frames/frame-{index:06d}.png"
            image.write_bytes(f"{split}-{index}".encode())
            sample = dict(frame_id=index, timestamp_s=index, width=100, height=100)
            frames.append(
                {**sample, "processed": True, "box": [50, 50, 60, 60], "completed_s": index + 0.1}
            )
            samples.append(
                {
                    **sample,
                    "image_path": image.relative_to(root).as_posix(),
                    "sha256": sha256(image),
                }
            )
            labels.append({"frame_id": index, "box": target})
        write_json(
            root / "record.json",
            {
                "schema_version": 1,
                "session_id": split,
                "status": "completed",
                "performance": {"truncated": False, "counts": {"capture": 2}},
                "frames": frames,
            },
        )
        write_json(
            root / "samples.json",
            {
                "schema_version": 1,
                "session_id": split,
                "source_type": "camera_stills",
                "status": "completed",
                "samples": samples,
            },
        )
        write_json(
            root / "annotations.json",
            {
                "schema_version": 1,
                "session_id": split,
                "status": "reviewed",
                "target_class": "drone",
                "frames": labels,
            },
        )
        sources.append(
            {
                "split": split,
                "capture_id": split,
                **{
                    key: f"{split}/{name}.json"
                    for key, name in (
                        ("record", "record"),
                        ("samples", "samples"),
                        ("annotations", "annotations"),
                    )
                },
            }
        )
    plan = tmp_path / "plan.json"
    write_json(
        plan,
        {
            "schema_version": 1,
            "status": "reviewed",
            "split_rule": "capture_group_before_case_selection",
            "sources": sources,
        },
    )
    return plan, tmp_path / "dataset"


def test_preparation_uses_human_boxes_and_preserves_holdout(learning_plan, monkeypatch):
    plan, output = learning_plan
    monkeypatch.setitem(sys.modules, "torch", None)
    summary = retrain.prepare(plan, output, "v2")
    assert summary["splits"] == {s: {"images": 2, "objects": 1} for s in ("train", "val", "test")}
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["status"] == "needs_review"
    assert manifest["retrain_plan_sha256"] == sha256(plan)
    assert all(v["source_type"] == "photos" for v in manifest["sessions"])
    assert all("original_video" not in v for v in manifest["sessions"])
    for origin in manifest["learning_sources"]:
        assert origin["record_sha256"] == sha256(plan.parent / origin["split"] / "record.json")
    assert (output / "labels/train/source-001/000001.txt").read_text().split() == [
        "0",
        "0.200000000000",
        "0.400000000000",
        "0.200000000000",
        "0.400000000000",
    ]
    assert (output / "labels/train/source-001/000002.txt").read_text() == ""
    cases = json.loads((output / "training_cases.json").read_text())
    assert [v["case"] for v in cases] == ["mislocalized", "background_false_box"]
    assert all(v["path"].startswith("images/train/") for v in cases)
    with pytest.raises(ValueError, match="ready"):
        check_dataset(output / "dataset.yaml", manifest_path)
    manifest["status"] = "ready"
    write_json(manifest_path, manifest)
    assert (
        train_main(
            [
                "--data",
                str(output / "dataset.yaml"),
                "--manifest",
                str(manifest_path),
                "--check-only",
            ]
        )
        == 0
    )
    with pytest.raises(ValueError, match="이미"):
        retrain.prepare(plan, output, "v3")


@pytest.mark.parametrize(
    "file,mutate",
    [
        ("plan.json", lambda v: v.update(status="needs_review")),
        ("plan.json", lambda v: v["sources"][1].update(capture_id="train")),
        ("plan.json", lambda v: v["sources"].pop()),
        ("train/record.json", lambda v: v["performance"].update(truncated=True)),
        ("train/record.json", lambda v: v.update(error="camera input failed")),
        ("train/record.json", lambda v: v["frames"][0].update(completed_s=0)),
        ("train/samples.json", lambda v: v.update(session_id="other")),
        ("train/samples.json", lambda v: v.update(status="failed")),
        ("train/samples.json", lambda v: v["samples"][0].update(sha256="0" * 64)),
        ("train/samples.json", lambda v: v["samples"][0].update(image_path="../escape.png")),
        ("train/samples.json", lambda v: v["samples"][0].update(width=200)),
        ("train/samples.json", lambda v: v["samples"][0].update(timestamp_s=True)),
        ("train/annotations.json", lambda v: v.update(status="needs_review")),
        ("train/annotations.json", lambda v: v.update(target_class="person")),
        ("train/annotations.json", lambda v: v["frames"].pop()),
        ("train/annotations.json", lambda v: v["frames"].append(v["frames"][0])),
        ("train/annotations.json", lambda v: v["frames"][0].update(box="needs_label")),
        ("train/annotations.json", lambda v: v["frames"][0].update(box=[-1, 0, 10, 10])),
    ],
)
def test_invalid_sources_fail_before_creating_dataset(learning_plan, file, mutate):
    plan, output = learning_plan
    path = plan.parent / file
    value = json.loads(path.read_text())
    mutate(value)
    write_json(path, value)
    with pytest.raises(ValueError):
        retrain.prepare(plan, output, "v2")
    assert not output.exists()


@pytest.mark.parametrize("device", ["cpu", "mps"])
@pytest.mark.parametrize(
    "problem", [None, "class", "labels", "images", "nan", "weight_changed", "data_changed"]
)
def test_compare_same_validation_only_with_failure_record(
    learning_plan, monkeypatch, problem, device
):
    plan, dataset = learning_plan
    retrain.prepare(plan, dataset, "v2")
    manifest = dataset / "manifest.json"
    value = json.loads(manifest.read_text())
    value["status"] = "ready"
    write_json(manifest, value)
    baseline, candidate = plan.parent / "old.pt", plan.parent / "new.pt"
    baseline.write_bytes(b"old")
    candidate.write_bytes(b"new")
    calls = []

    class Loaded:
        labels = [{"cls": [0]}, {"cls": []}]

        def __len__(self):
            return 1 if problem == "images" else 2

    class Model:
        task = "detect"
        names = {0: "person" if problem == "class" else "drone"}

        def __init__(self, path, task):
            self.path = Path(path)

        def add_callback(self, name, callback):
            assert name == "on_val_start"
            self.callback = callback

        def val(self, **options):
            self.callback(SimpleNamespace(dataloader=SimpleNamespace(dataset=Loaded())))
            calls.append(options)
            spec = yaml.safe_load(Path(options["data"]).read_text())
            assert "test" not in spec and options["split"] == "val"
            if problem == "weight_changed":
                self.path.write_bytes(b"changed")
            if problem == "data_changed":
                value = json.loads(manifest.read_text())
                value["dataset_version"] = "changed"
                write_json(manifest, value)
            score = 0.7 if self.path == candidate else 0.5
            return SimpleNamespace(
                nt_per_class=[2 if problem == "labels" else 1],
                box=SimpleNamespace(
                    mp=float("nan") if problem == "nan" else score, mr=score, map50=score, map=score
                ),
            )

    torch = SimpleNamespace(
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: True))
    )
    mps_validator = object()
    monkeypatch.setitem(
        sys.modules, "tools.mps_training", SimpleNamespace(MPSDetectionValidator=mps_validator)
    )
    monkeypatch.setattr(retrain, "prepare_runtime", lambda: (torch, Model))
    args = SimpleNamespace(
        data=dataset / "dataset.yaml",
        manifest=manifest,
        baseline=baseline,
        candidate=candidate,
        output=plan.parent / "compare",
        device=device,
        imgsz=64,
        batch=1,
    )
    if problem:
        with pytest.raises(ValueError):
            retrain.compare(args)
        assert json.loads((args.output / "comparison.json").read_text())["status"] == "failed"
    else:
        report = retrain.compare(args)
        assert report["status"] == "completed" and report["test_evaluated"] is False
        assert report["delta"]["map50"] == pytest.approx(0.2)
        assert report["models"]["baseline"]["sha256"] == sha256(baseline)
        assert len(calls) == 2 and calls[0]["data"] == calls[1]["data"]
        assert calls[0]["validator"] is (mps_validator if device == "mps" else None)
        with pytest.raises(ValueError, match="이미"):
            retrain.compare(args)


def test_cli_compare_defaults_to_mps_and_invalid_json_fails(learning_plan, monkeypatch):
    seen = []
    monkeypatch.setattr(retrain, "compare", lambda args: seen.append(args.device) or {})
    args = ["compare"]
    for name in ("data", "manifest", "baseline", "candidate", "output"):
        args += [f"--{name}", name]
    assert retrain.main(args) == 0 and seen == ["mps"]
    plan, output = learning_plan
    write_json(plan, [])
    assert (
        retrain.main(["prepare", "--plan", str(plan), "--output", str(output), "--version", "v"])
        == 1
    )
    assert not output.exists()


def test_annotations_changed_during_prepare_are_rejected(learning_plan, monkeypatch):
    plan, output = learning_plan
    original = retrain.read_json

    def read_then_change(path):
        value = original(path)
        if path.name == "annotations.json":
            path.write_text(path.read_text() + "\n")
        return value

    monkeypatch.setattr(retrain, "read_json", read_then_change)
    with pytest.raises(ValueError, match="변경"):
        retrain.prepare(plan, output, "v2")
    assert not output.exists()


@pytest.mark.parametrize("name", ["record", "samples", "annotations"])
def test_source_json_changed_during_copy_is_rejected(learning_plan, monkeypatch, name):
    plan, output = learning_plan
    source = plan.parent / "train" / f"{name}.json"
    original = retrain.shutil.copyfile

    def copy_then_change(before, after):
        original(before, after)
        source.write_text(source.read_text() + "\n")

    monkeypatch.setattr(retrain.shutil, "copyfile", copy_then_change)
    with pytest.raises(ValueError, match="변경"):
        retrain.prepare(plan, output, "v2")
    assert output.exists() and not (output / "manifest.json").exists()


@pytest.mark.parametrize("failure", ["runtime", "mps"])
def test_cli_compare_records_setup_failure_and_preserves_output(
    learning_plan, monkeypatch, capsys, failure
):
    plan, dataset = learning_plan
    retrain.prepare(plan, dataset, "v2")
    manifest = dataset / "manifest.json"
    value = json.loads(manifest.read_text())
    value["status"] = "ready"
    write_json(manifest, value)
    baseline = plan.parent / "baseline.pt"
    baseline.write_bytes(b"trusted fixture")

    def runtime():
        if failure == "runtime":
            raise ImportError("training runtime missing")
        torch = SimpleNamespace(
            backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False))
        )
        return torch, None

    monkeypatch.setattr(retrain, "prepare_runtime", runtime)
    output = plan.parent / "comparison"
    args = ["compare"]
    for key, path in {
        "data": dataset / "dataset.yaml",
        "manifest": manifest,
        "baseline": baseline,
        "candidate": baseline,
        "output": output,
    }.items():
        args += [f"--{key}", str(path)]
    assert retrain.main(args) == 1
    report_path = output / "comparison.json"
    report = json.loads(report_path.read_text())
    assert report["status"] == "failed"
    assert report["requested_device"] == "mps" and report["device"] is None
    expected = "training runtime missing" if failure == "runtime" else "MPS is unavailable"
    assert expected in report["error"] and expected in capsys.readouterr().out
    before = report_path.read_bytes()
    assert retrain.main(args) == 1
    assert report_path.read_bytes() == before
    assert "이미" in capsys.readouterr().out
