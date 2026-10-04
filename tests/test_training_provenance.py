import json

import pytest
from test_training import dataset

from tools.training_data import check_dataset


@pytest.mark.parametrize(
    "missing", ["path_only", "original_video", "original_video_sha256", "timestamp_s", "sha256"]
)
def test_incomplete_provenance_is_rejected(tmp_path, missing):
    data, manifest = dataset(tmp_path)
    content = json.loads(manifest.read_text(encoding="utf-8"))
    session = content["sessions"][0]
    if missing == "path_only":
        session["images"] = [session["images"][0]["path"]]
    elif missing.startswith("original_video"):
        del session[missing]
    else:
        del session["images"][0][missing]
    manifest.write_text(json.dumps(content), encoding="utf-8")
    with pytest.raises(ValueError):
        check_dataset(data, manifest)


@pytest.mark.parametrize("identity", ["original_video", "original_video_sha256"])
def test_same_capture_cannot_hide_behind_different_session_names(tmp_path, identity):
    data, manifest = dataset(tmp_path)
    content = json.loads(manifest.read_text(encoding="utf-8"))
    content["sessions"][1][identity] = content["sessions"][0][identity]
    manifest.write_text(json.dumps(content), encoding="utf-8")
    with pytest.raises(ValueError, match="original capture"):
        check_dataset(data, manifest)


def test_changed_image_is_rejected_against_reviewed_hash(tmp_path):
    data, manifest = dataset(tmp_path)
    (tmp_path / "images/train/frame.jpg").write_bytes(b"replacement")
    with pytest.raises(ValueError, match="differs from the reviewed manifest"):
        check_dataset(data, manifest)


@pytest.mark.parametrize("timestamp", [-1, True, "1", None, float("nan"), float("inf")])
def test_invalid_frame_timestamp_is_rejected(tmp_path, timestamp):
    data, manifest = dataset(tmp_path)
    content = json.loads(manifest.read_text(encoding="utf-8"))
    content["sessions"][0]["images"][0]["timestamp_s"] = timestamp
    manifest.write_text(json.dumps(content), encoding="utf-8")
    with pytest.raises(ValueError, match="timestamp_s"):
        check_dataset(data, manifest)


@pytest.mark.parametrize("field", ["original_video_sha256", "sha256"])
@pytest.mark.parametrize("digest", ["", "0" * 63, "g" * 64, None, 12])
def test_invalid_reviewed_hash_is_rejected(tmp_path, field, digest):
    data, manifest = dataset(tmp_path)
    content = json.loads(manifest.read_text(encoding="utf-8"))
    session = content["sessions"][0]
    target = session if field == "original_video_sha256" else session["images"][0]
    target[field] = digest
    manifest.write_text(json.dumps(content), encoding="utf-8")
    with pytest.raises(ValueError):
        check_dataset(data, manifest)
