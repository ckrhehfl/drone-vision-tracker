import json
import sys
import zipfile

import pytest
from test_training import dataset

from tools import prepare_dataset
from tools.prepare_dataset import main
from tools.training_data import check_dataset, check_labels, sha256


def test_pinned_archives_prepare_draft_keep_test_and_split_identical_bytes_together(
    tmp_path, monkeypatch
):
    source, output = tmp_path / "cache", tmp_path / "prepared"
    archives = {}
    for split, count in (("train", 30), ("test", 3)):
        for kind in ("images", "labels"):
            path = source / split / kind / "batch_001.zip"
            path.parent.mkdir(parents=True)
            with zipfile.ZipFile(path, "w") as archive:
                for index in range(count):
                    name = f"{index}.jpg" if kind == "images" else f"{index}.txt"
                    # Two identical train images must receive the same split.
                    content = (
                        f"{split}-{0 if index == 1 else index}".encode()
                        if kind == "images"
                        else b"0 .5 .5 .2 .2\n"
                    )
                    if kind == "labels" and index == 2:
                        content = b"0 .4 .4 .6 .4 .6 .6 .4 .6\n"
                    archive.writestr(name, content)
            archives[path.relative_to(source).as_posix()] = sha256(path)
    monkeypatch.setattr(prepare_dataset, "ARCHIVES", archives)
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setitem(sys.modules, "ultralytics", None)
    args = ["seraphim", "--source", str(source), "--output", str(output), "--version", "v1"]
    assert main(args + ["--val-fraction", ".5"]) == 0
    manifest = output / "manifest.json"
    content = json.loads(manifest.read_text(encoding="utf-8"))
    assert content["status"] == "needs_review"
    assert content["label_preparation"]["polygon_to_box"] == {
        "train/labels/2.txt": 1,
        "test/labels/2.txt": 1,
    }
    with pytest.raises(ValueError, match="status=ready"):
        check_dataset(output / "dataset.yaml", manifest)
    content["status"] = "ready"  # Test fixture review; the tool never does this itself.
    manifest.write_text(json.dumps(content), encoding="utf-8")
    spec, summary = check_dataset(output / "dataset.yaml", manifest)
    assert "test" not in spec
    assert summary["splits"]["test"]["images"] == 3
    assert summary["public_split_independence_verified"] is False
    assert all("timestamp_s" not in e for s in content["sessions"] for e in s["images"])
    assert main(args) == 1  # Preserve existing prepared data.
    (source / "train/images/batch_001.zip").write_bytes(b"changed")
    assert main(args[:-3] + [str(tmp_path / "other"), "--version", "v2"]) == 1
    assert not (tmp_path / "other").exists()


def test_polygon_conversion_preserves_boxes_and_rejects_invalid_vertices(tmp_path):
    original = b"0 .5 .5 .2 .2\n"
    assert prepare_dataset.detection_labels(original) == (original, 0)
    converted, count = prepare_dataset.detection_labels(b"0 .2 .3 .6 .3 .4 .7\n")
    assert count == 1
    label = tmp_path / "polygon.txt"
    label.write_bytes(converted)
    assert check_labels(label) == 1
    assert list(map(float, converted.split()[1:])) == pytest.approx([0.4, 0.5, 0.4, 0.4])
    for content in (b"0 .2 .3 .6 nan .4 .7", b"0 .2 .3 .6 .3 .4 1.1", b"0 .2 .3 .6 .3 .4"):
        with pytest.raises(ValueError):
            prepare_dataset.detection_labels(content)


def test_real_photos_keep_capture_groups_and_generate_reviewable_hashes(tmp_path):
    dataset(tmp_path)
    for split in ("train", "val", "test"):
        for kind, suffix in (("images", ".jpg"), ("labels", ".txt")):
            old = tmp_path / kind / split / f"frame{suffix}"
            new = old.parent / f"capture-{split}" / old.name
            new.parent.mkdir()
            old.rename(new)
    (tmp_path / "manifest.json").unlink()  # Remove the fixture's video metadata.
    assert main(["photos", "--output", str(tmp_path), "--version", "real-v1"]) == 0
    content = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert {s["source_type"] for s in content["sessions"]} == {"photos"}
    content["status"] = "ready"
    (tmp_path / "manifest.json").write_text(json.dumps(content), encoding="utf-8")
    check_dataset(tmp_path / "dataset.yaml", tmp_path / "manifest.json")
    content["sessions"][1]["capture_id"] = content["sessions"][0]["capture_id"]
    (tmp_path / "manifest.json").write_text(json.dumps(content), encoding="utf-8")
    with pytest.raises(ValueError, match="original capture"):
        check_dataset(tmp_path / "dataset.yaml", tmp_path / "manifest.json")


@pytest.mark.parametrize("name", ["../escape.jpg", "/escape.jpg", "a\\escape.jpg"])
def test_archive_paths_are_never_extracted_outside_output(tmp_path, name):
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, b"image")
    with zipfile.ZipFile(path) as archive, pytest.raises(ValueError, match="flat"):
        prepare_dataset.flat_members(archive, {".jpg"})
