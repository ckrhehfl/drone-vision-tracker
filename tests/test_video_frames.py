import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import tools.video_frames as video_frames
from tools.training_data import sha256


@pytest.fixture
def video_input(tmp_path, monkeypatch):
    source = tmp_path / "외부 동영상.mp4"
    source.write_bytes(b"source-video")
    state = SimpleNamespace(
        times=[0, 100, 500, 900, 1000], index=0, opened=True, count=5, released=False
    )

    def read():
        if state.index == len(state.times):
            return False, None
        state.index += 1
        return True, SimpleNamespace(shape=(100, 200, 3), index=state.index)

    def get(key):
        return state.count if key == 7 else state.times[state.index - 1]

    def capture(path):
        assert path == str(source.resolve())  # Never open a numbered camera.
        return SimpleNamespace(
            isOpened=lambda: state.opened,
            read=read,
            get=get,
            release=lambda: setattr(state, "released", True),
        )

    cv = SimpleNamespace(
        CAP_PROP_FRAME_COUNT=7,
        CAP_PROP_POS_MSEC=0,
        VideoCapture=capture,
        imencode=lambda extension, frame: (
            True,
            SimpleNamespace(tobytes=lambda: f"pixels-{frame.index}".encode()),
        ),
    )
    monkeypatch.setitem(sys.modules, "cv2", cv)
    return source, tmp_path / "한글 추출", state, cv


def test_video_sampling_preserves_source_pts_pixels_and_needs_review(video_input):
    source, output, state, _ = video_input
    original = source.read_bytes()
    result = video_frames.extract_video(source, output)
    assert result["status"] == "completed" and state.released
    assert result["frames_decoded"] == 5
    assert result["original_video_sha256"] == sha256(source)
    assert [s["frame_id"] for s in result["samples"]] == [1, 3, 5]
    assert [s["timestamp_s"] for s in result["samples"]] == [0, 0.5, 1]
    for row in result["samples"]:
        image = output / row["image_path"]
        assert image.read_bytes() == f"pixels-{row['frame_id']}".encode()
        assert row["sha256"] == sha256(image) and row["width"] == 200
    annotations = json.loads((output / "annotations.example.json").read_text(encoding="utf-8"))
    assert annotations["session_id"] == result["session_id"]
    assert annotations["status"] == "needs_review"
    assert all(row["box"] == "needs_label" for row in annotations["frames"])
    with pytest.raises(ValueError, match="이미"):
        video_frames.extract_video(source, output)
    assert source.read_bytes() == original


@pytest.mark.parametrize(
    "failure",
    [
        "open",
        "empty",
        "timestamps",
        "nonfinite",
        "truncated",
        "encoding",
        "disk",
        "mutated",
        "limit",
        "cancel",
    ],
)
def test_video_failure_never_claims_completed(video_input, monkeypatch, failure):
    source, output, state, cv = video_input
    cancel = threading.Event()
    if failure == "open":
        state.opened = False
    elif failure == "empty":
        state.times = []
    elif failure == "timestamps":
        state.times[1] = 0
    elif failure == "nonfinite":
        state.times[1] = float("nan")
    elif failure == "truncated":
        state.count = 100
    elif failure == "encoding":
        cv.imencode = lambda *args: (False, None)
    elif failure == "disk":

        def full_disk(*args):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_bytes", full_disk)
    elif failure == "mutated":

        def encode(extension, frame):
            source.write_bytes(b"changed-source")
            return True, SimpleNamespace(tobytes=lambda: b"image")

        cv.imencode = encode
    elif failure == "limit":
        monkeypatch.setattr(video_frames, "MAX_SAMPLES", 1)
    elif failure == "cancel":
        cancel.set()
    with pytest.raises((OSError, ValueError)):
        video_frames.extract_video(source, output, cancel=cancel)
    report = json.loads((output / "samples.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed" and report["error"] and state.released
    assert (output / "annotations.example.json").is_file()


@pytest.mark.parametrize("interval", [0, -1, float("nan"), float("inf"), True])
def test_invalid_sampling_does_not_create_output(video_input, interval):
    source, output, _, _ = video_input
    with pytest.raises(ValueError, match="간격"):
        video_frames.extract_video(source, output, interval)
    assert not output.exists()


def test_video_cli_reports_success_and_missing_input(video_input):
    source, output, _, _ = video_input
    assert video_frames.main(["--video", str(source), "--output", str(output)]) == 0
    assert (
        video_frames.main(["--video", str(source.with_suffix(".missing")), "--output", str(output)])
        == 1
    )
