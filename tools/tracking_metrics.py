"""Local runtime measurements and single-target box evaluation; no media or AI imports."""

import argparse
import json
import math
import threading
import time
import uuid
from collections import Counter, deque
from pathlib import Path

WINDOW_S = 2.0
MAX_RECORDS = 6000


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)] if ordered else None


class SessionMetrics:
    def __init__(self, settings=None):
        self.started = time.monotonic()
        self.session_id = str(uuid.uuid4())
        self.settings = settings or {}
        self.lock = threading.Lock()
        self.events = {name: deque() for name in ("capture", "process", "display")}
        self.counts = Counter()
        self.timings = deque()
        self.frames = {}
        self.last_display = None
        self.ended, self.error = None, None

    def _event(self, name, at):
        self.counts[name] += 1
        events = self.events[name]
        events.append(at)
        while events and events[0] <= at - WINDOW_S:
            events.popleft()

    def capture(self, captured, shape):
        with self.lock:
            self._event("capture", captured)
            # shortcut: retain metadata for 6000 frames, use shorter trials until file input exists.
            if len(self.frames) < MAX_RECORDS:
                self.frames[captured] = {
                    "frame_id": self.counts["capture"],
                    "timestamp_s": captured - self.started,
                    "width": shape[1],
                    "height": shape[0],
                    "processed": False,
                    "box": None,
                    "completed_s": None,
                    "method": None,
                }
                return self.counts["capture"]

    def discard(self, reason, count=1):
        with self.lock:
            self.counts[reason] += count

    def processed(self, captured, started, finished, detection, method):
        with self.lock:
            self._event("process", finished)
            self.timings.append(
                (finished, (finished - started) * 1000, (finished - captured) * 1000)
            )
            while self.timings and self.timings[0][0] <= finished - WINDOW_S:
                self.timings.popleft()
            if captured in self.frames:
                self.frames[captured].update(
                    processed=True,
                    box=list(detection[:4]) if detection is not None else None,
                    completed_s=finished - self.started,
                    method=method,
                )

    def displayed(self, captured, finished):
        with self.lock:
            if captured != self.last_display:
                self._event("display", finished)
                self.last_display = captured

    def fail(self, error):
        with self.lock:
            self.error = str(error)

    def finish(self):
        with self.lock:
            if self.ended is None:
                self.ended = time.monotonic()

    def _snapshot(self, now):
        now = self.ended if self.ended is not None else now
        duration = max(0, now - self.started)
        window = min(WINDOW_S, duration)
        fps = {
            name: sum(now - WINDOW_S < at <= now for at in events) / window if window else 0.0
            for name, events in self.events.items()
        }
        recent = [row for row in self.timings if now - WINDOW_S < row[0] <= now]
        return {
            "duration_s": duration,
            "window_s": WINDOW_S,
            "fps": fps,
            "counts": dict(self.counts),
            "processing_ms": {
                key: percentile([v[1] for v in recent], p)
                for key, p in (("p50", 0.5), ("p95", 0.95))
            },
            "host_latency_ms": {
                key: percentile([v[2] for v in recent], p)
                for key, p in (("p50", 0.5), ("p95", 0.95))
            },
            "recorded_frames": len(self.frames),
            "truncated": self.counts["capture"] > len(self.frames),
        }

    def snapshot(self):
        with self.lock:
            return self._snapshot(time.monotonic())

    def report(self):
        with self.lock:
            return {
                "schema_version": 1,
                "session_id": self.session_id,
                "status": "failed"
                if self.error
                else "completed"
                if self.ended is not None
                else "running",
                "error": self.error,
                "settings": dict(self.settings),
                "performance": self._snapshot(time.monotonic()),
                "frames": [
                    dict(row, box=list(row["box"]) if row["box"] else None)
                    for row in self.frames.values()
                ],
            }


def performance_text(summary):
    fps, counts = summary["fps"], summary["counts"]

    def timing(key):
        values = summary[key]
        return "/".join("—" if values[p] is None else f"{values[p]:.0f}" for p in ("p50", "p95"))

    return (
        f"최근 2초 FPS · 수신 {fps['capture']:.1f} · 화면 갱신 {fps['display']:.1f}"
        f" · 추적 처리 {fps['process']:.1f}  | 누적 {counts.get('capture', 0)}"
        f"/{counts.get('display', 0)}/{counts.get('process', 0)}\n"
        f"폐기 · 추적 큐 {counts.get('tracking_replaced', 0)}"
        f" · 표시 큐 {counts.get('preview_replaced', 0)}"
        f" · 결과 큐 {counts.get('result_replaced', 0)}"
        f" · 오래된 입력 {counts.get('stale_input', 0)}"
        f" · 완료 후 만료 {counts.get('stale_result', 0)}\n"
        f"p50/p95 ms · 처리 {timing('processing_ms')} · 수신→완료 {timing('host_latency_ms')}"
        + (
            " · 기록 한도 초과: 정확도 평가 불가"
            if summary["truncated"]
            else f" · 평가 기록 {summary['recorded_frames']}장"
        )
    )


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def validate_box(value, width, height):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 4 or not all(_number(v) for v in value):
        raise ValueError("박스는 원본 픽셀 [x1,y1,x2,y2] 또는 null이어야 합니다.")
    if not (0 <= value[0] < value[2] <= width and 0 <= value[1] < value[3] <= height):
        raise ValueError("박스가 원본 영상 범위를 벗어나거나 크기가 잘못됐습니다.")
    return value


def validate_record(record):
    from tools.camera_gui import MAX_AGE_S

    if (
        not isinstance(record, dict)
        or type(record.get("schema_version")) is not int
        or record["schema_version"] != 1
    ):
        raise ValueError("지원하지 않는 측정 기록 형식입니다.")
    if not isinstance(record.get("session_id"), str) or not record["session_id"]:
        raise ValueError("측정 기록의 session_id가 필요합니다.")
    performance = record.get("performance", {})
    rows = record.get("frames")
    if (
        record.get("status") != "completed"
        or record.get("error") is not None
        or not isinstance(performance, dict)
        or performance.get("truncated") is not False
    ):
        raise ValueError(
            "정상 종료된 전체 기록만 평가합니다. "
            "기록 한도 초과·진행 중·오류 기록은 평가할 수 없습니다."
        )
    if not isinstance(rows, list) or not rows:
        raise ValueError("평가 프레임과 정답 목록이 필요합니다.")
    if (
        not isinstance(performance.get("counts"), dict)
        or type(performance["counts"].get("capture")) is not int
        or performance["counts"]["capture"] != len(rows)
    ):
        raise ValueError("전체 수신 프레임이 기록에 포함되어야 합니다.")
    previous_time = -1.0
    for index, row in enumerate(rows, 1):
        if (
            not isinstance(row, dict)
            or type(row.get("frame_id")) is not int
            or row["frame_id"] != index
        ):
            raise ValueError("기록 frame_id가 순서대로 이어지지 않습니다.")
        timestamp, width, height = row.get("timestamp_s"), row.get("width"), row.get("height")
        if (
            not _number(timestamp)
            or timestamp < 0
            or timestamp <= previous_time
            or type(width) is not int
            or type(height) is not int
            or min(width, height) < 1
            or not _number(width)
            or not _number(height)
        ):
            raise ValueError("프레임 시간은 증가해야 하며 원본 영상 크기가 유효해야 합니다.")
        previous_time = timestamp
        if type(row.get("processed")) is not bool or "box" not in row:
            raise ValueError("처리 여부와 예측 박스가 필요합니다.")
        prediction = validate_box(row["box"], width, height)
        completed = row.get("completed_s")
        if row["processed"]:
            if not _number(completed) or completed < timestamp:
                raise ValueError("처리 완료 시각이 수신 시각보다 빠르거나 유효하지 않습니다.")
            if prediction is not None and completed - timestamp > MAX_AGE_S:
                raise ValueError("만료된 프레임의 박스는 유효한 예측으로 평가할 수 없습니다.")
        elif prediction is not None or completed is not None:
            raise ValueError("미처리 프레임에는 예측 박스·완료 시각이 없어야 합니다.")
    return rows


def evaluate(record, truth, iou_threshold=0.5):
    """Evaluate every captured frame; skipped processing remains a missing prediction."""
    from tools.camera_gui import overlap

    if not _number(iou_threshold) or not 0 < iou_threshold <= 1:
        raise ValueError("IoU 기준은 0보다 크고 1 이하여야 합니다.")
    if not isinstance(record, dict) or not isinstance(truth, dict):
        raise ValueError("측정 기록과 정답 파일은 JSON 객체여야 합니다.")
    if any(
        type(v.get("schema_version")) is not int or v["schema_version"] != 1
        for v in (record, truth)
    ):
        raise ValueError("지원하지 않는 평가 파일 버전입니다.")
    if (
        not isinstance(record.get("session_id"), str)
        or not record["session_id"]
        or truth.get("session_id") != record["session_id"]
    ):
        raise ValueError("측정 기록과 정답의 session_id가 다릅니다.")
    rows = validate_record(record)
    labels = truth.get("frames")
    if not isinstance(labels, list):
        raise ValueError("정답 목록이 필요합니다.")
    by_id = {}
    for label in labels:
        if (
            not isinstance(label, dict)
            or type(label.get("frame_id")) is not int
            or "box" not in label
            or label["frame_id"] in by_id
        ):
            raise ValueError(
                "정답 frame_id가 잘못됐거나 중복됐습니다. 각 프레임의 box가 필요합니다."
            )
        by_id[label["frame_id"]] = label["box"]
    if set(by_id) != set(range(1, len(rows) + 1)):
        raise ValueError("정답은 기록의 모든 frame_id를 빠짐없이 포함해야 합니다.")
    present = background = matched = false_boxes = background_false = 0
    overlaps, reentries = [], []
    seen_visible, previously_visible, pending = False, False, None
    for index, row in enumerate(rows, 1):
        width, height = row["width"], row["height"]
        prediction, completed = row["box"], row["completed_s"]
        target = validate_box(by_id[index], width, height)
        score = (
            overlap(target, prediction) if target is not None and prediction is not None else 0.0
        )
        correct = target is not None and prediction is not None and score >= iou_threshold
        if target is not None:
            present += 1
            overlaps.append(score)
            matched += correct
            false_boxes += prediction is not None and not correct
            if not previously_visible and seen_visible:
                pending = {"start_frame_id": index, "reacquired_frame_id": None, "delay_s": None}
                reentries.append(pending)
            if pending is not None and correct:
                pending.update(
                    reacquired_frame_id=index,
                    delay_s=completed - rows[pending["start_frame_id"] - 1]["timestamp_s"],
                )
                pending = None
            seen_visible = True
        else:
            background += 1
            background_false += prediction is not None
            false_boxes += prediction is not None
            pending = None
        previously_visible = target is not None
    recovered = [event["delay_s"] for event in reentries if event["delay_s"] is not None]
    return {
        "schema_version": 1,
        "session_id": record["session_id"],
        "iou_threshold": iou_threshold,
        "weighting": "all_captured_frames",
        "frames": len(rows),
        "visible_frames": present,
        "matched_frames": matched,
        "missed_visible_frames": present - matched,
        "tracking_success_rate": matched / present if present else None,
        "mean_iou_visible": sum(overlaps) / present if present else None,
        "false_box_frames": false_boxes,
        "background_frames": background,
        "background_false_box_frames": background_false,
        "background_false_box_rate": background_false / background if background else None,
        "precision": matched / (matched + false_boxes) if matched + false_boxes else None,
        "reentry_events": reentries,
        "reentry_success_rate": len(recovered) / len(reentries) if reentries else None,
        "reentry_delay_s": {"p50": percentile(recovered, 0.5), "p95": percentile(recovered, 0.95)},
    }


def evaluation_text(result):
    def percent(value):
        return "평가 대상 없음" if value is None else f"{value:.1%}"

    delays = result["reentry_delay_s"]
    latency = "/".join("—" if delays[p] is None else f"{delays[p]:.3f}" for p in ("p50", "p95"))
    return (
        f"원본 수신 {result['frames']}장 전체 · IoU ≥ {result['iou_threshold']}\n"
        f"추적 박스 성공률 {percent(result['tracking_success_rate'])} "
        f"({result['matched_frames']}/{result['visible_frames']} 가시 프레임)\n"
        f"배경 오인식 {result['background_false_box_frames']}/{result['background_frames']}장 · "
        f"전체 잘못된 박스 {result['false_box_frames']}장\n"
        f"재진입 성공률 {percent(result['reentry_success_rate'])} · 재획득 p50/p95 {latency}초\n"
        "프레임 기준 평가이며 카메라 중앙 추종 성공률·mAP가 아닙니다."
    )


def main(argv=None):
    from tools.validate_config import strict_json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--truth", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = evaluate(
            strict_json(args.record.read_text(encoding="utf-8")),
            strict_json(args.truth.read_text(encoding="utf-8")),
        )
    except (OSError, ValueError) as exc:
        print(f"평가 실패: {exc}")
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
