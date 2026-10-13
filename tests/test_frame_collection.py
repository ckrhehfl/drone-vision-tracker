import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.frame_collection import FrameCollector
from tools.training_data import sha256


@pytest.mark.parametrize("fail", [False, "encode", "disk"])
def test_bounded_async_collection_sampling_close_and_failure(tmp_path, monkeypatch, fail):
    entered, release = threading.Event(), threading.Event()
    copied = []

    class Frame:
        shape = (100, 200, 3)

        def copy(self):
            copied.append(True)
            return self

    def encode(extension, frame):
        assert extension == ".png"
        entered.set()
        assert release.wait(2)
        return fail != "encode", SimpleNamespace(tobytes=lambda: b"original-pixels")

    if fail == "disk":

        def full_disk(*args):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_bytes", full_disk)
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(imencode=encode))
    collector = FrameCollector(tmp_path / "원본 수집", SimpleNamespace(started=10, session_id="s"))
    try:
        collector.submit(1, 10, Frame())
        assert entered.wait(1)
        collector.submit(2, 10.1, Frame())  # Inside the sample interval.
        for frame_id, at in ((3, 10.5), (4, 11), (5, 11.5)):
            collector.submit(frame_id, at, Frame())
        assert collector.jobs.qsize() == 2 and collector.dropped == 1
        assert len(copied) == 4
    finally:
        release.set()
        collector.close()
    collector.close()
    collector.submit(6, 12, Frame())
    assert len(copied) == 4 and not collector.thread.is_alive()
    data = json.loads((collector.root / "samples.json").read_text(encoding="utf-8"))
    labels = json.loads((collector.root / "annotations.example.json").read_text(encoding="utf-8"))
    assert data["session_id"] == "s" and data["source_type"] == "camera_stills"
    assert labels["status"] == "needs_review" and labels["target_class"] == "drone"
    if fail:
        assert data["status"] == "failed" and data["samples"] == []
        assert data["queue_dropped"] == 4 and collector.error
    else:
        assert data["status"] == "completed" and data["queue_dropped"] == 1
        assert [s["frame_id"] for s in data["samples"]] == [1, 3, 4]
        for sample in data["samples"]:
            assert sample["sha256"] == sha256(collector.root / sample["image_path"])
            assert (sample["width"], sample["height"]) == (200, 100)
        assert all(v["box"] == "needs_label" for v in labels["frames"])
    with pytest.raises(FileExistsError):
        FrameCollector(collector.root, collector.metrics)
