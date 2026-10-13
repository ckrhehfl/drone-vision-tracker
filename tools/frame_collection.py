"""Opt-in camera stills for reviewed learning data, with a bounded disk queue."""

import json
import queue
import threading
from pathlib import Path

from tools.training_data import sha256

SAMPLE_INTERVAL_S = 0.5


class FrameCollector:
    def __init__(self, root, metrics):
        self.root, self.metrics = Path(root).resolve(), metrics
        self.root.mkdir(exist_ok=False)
        (self.root / "frames").mkdir()
        self.jobs = queue.Queue(maxsize=2)
        self.samples, self.error, self.dropped = [], None, 0
        self.last_sample, self.closed = None, False
        self.thread = threading.Thread(target=self._write, daemon=True)
        self.thread.start()

    def submit(self, frame_id, captured, frame):
        if (
            self.error
            or self.closed
            or (self.last_sample is not None and captured - self.last_sample < SAMPLE_INTERVAL_S)
        ):
            return
        self.last_sample = captured
        try:
            self.jobs.put_nowait((frame_id, captured, frame.copy()))
        except queue.Full:
            self.dropped += 1

    def _write(self):
        while True:
            job = self.jobs.get()
            if job is None:
                return
            if self.error:
                self.dropped += 1
                continue
            frame_id, captured, frame = job
            try:
                import cv2

                name = f"frames/frame-{frame_id:06d}.png"
                path = self.root / name
                if not cv2.imwrite(str(path), frame):
                    raise OSError("PNG 저장 실패")
                self.samples.append(
                    {
                        "frame_id": frame_id,
                        "timestamp_s": captured - self.metrics.started,
                        "width": frame.shape[1],
                        "height": frame.shape[0],
                        "image_path": name,
                        "sha256": sha256(path),
                    }
                )
            except Exception as exc:
                self.error = str(exc)
                self.dropped += 1

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.jobs.put(None)
        self.thread.join()
        try:
            (self.root / "samples.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "session_id": self.metrics.session_id,
                        "source_type": "camera_stills",
                        "status": "failed" if self.error else "completed",
                        "sample_interval_s": SAMPLE_INTERVAL_S,
                        "queue_dropped": self.dropped,
                        "error": self.error,
                        "samples": self.samples,
                    },
                    ensure_ascii=False,
                    indent=2,
                    allow_nan=False,
                ),
                encoding="utf-8",
            )
            (self.root / "annotations.example.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "session_id": self.metrics.session_id,
                        "status": "needs_review",
                        "target_class": "drone",
                        "frames": [
                            {"frame_id": row["frame_id"], "box": "needs_label"}
                            for row in self.samples
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            self.error = str(exc)
