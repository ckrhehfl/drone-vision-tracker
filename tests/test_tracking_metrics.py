"""Deterministic runtime accounting and evaluation, without camera/Tk/AI."""

import copy
import json

import pytest

from tools.tracking_metrics import SessionMetrics, evaluate, evaluation_text, main, performance_text


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("tools.tracking_metrics.time.monotonic", lambda: now[0])
    return now


def test_fps_counts_completed_work_and_unique_frames_then_expires(clock):
    metrics = SessionMetrics()
    for at in (0.1, 0.2, 0.3):
        metrics.capture(at, (100, 100, 3))
    metrics.processed(0.1, 0.2, 0.4, None, "재검색")
    metrics.displayed(0.3, 0.4)
    metrics.displayed(0.3, 0.5)
    metrics.discard("tracking_replaced", 2)
    clock[0] = 1.0
    summary = metrics.snapshot()
    assert summary["fps"] == {"capture": 3.0, "process": 1.0, "display": 1.0}
    assert summary["counts"]["process"] == 1  # Missing detection still consumes processing.
    assert summary["counts"]["tracking_replaced"] == 2
    assert summary["processing_ms"]["p95"] == pytest.approx(200)
    assert summary["host_latency_ms"]["p50"] == pytest.approx(300)
    text = performance_text(summary)
    assert "화면 갱신 1.0" in text and "추적 큐 2" in text
    clock[0] = 4.0
    assert set(metrics.snapshot()["fps"].values()) == {0.0}
    assert metrics.snapshot()["host_latency_ms"]["p95"] is None
    metrics.finish()
    clock[0] = 100.0
    assert metrics.snapshot()["duration_s"] == 4.0


def test_record_limit_is_explicit_and_does_not_stop_runtime_measurement(clock, monkeypatch):
    monkeypatch.setattr("tools.tracking_metrics.MAX_RECORDS", 2)
    metrics = SessionMetrics()
    assert [metrics.capture(at, (100, 100)) for at in (0.1, 0.2, 0.3)] == [1, 2, None]
    metrics.processed(0.1, 0.1, 0.2, (1, 2, 20, 30, 0.9), "ViT")
    metrics.processed(0.3, 0.3, 0.4, None, "재검색")
    clock[0] = 0.5
    metrics.finish()
    report = metrics.report()
    assert len(report["frames"]) == 2
    assert report["performance"]["counts"]["capture"] == 3
    assert report["performance"]["counts"]["process"] == 2
    assert report["performance"]["truncated"] is True
    assert "기록 한도 초과" in performance_text(report["performance"])
    with pytest.raises(ValueError, match="기록 한도"):
        evaluate(report, {"schema_version": 1, "session_id": metrics.session_id, "frames": []})
    report["frames"][0]["box"][0] = 99
    assert metrics.report()["frames"][0]["box"][0] == 1


@pytest.fixture
def trial(clock):
    metrics = SessionMetrics({"device": "mps"})
    target, wrong = [10, 10, 30, 30], [60, 60, 80, 80]
    predictions = {1: target, 3: wrong, 4: wrong, 5: target}
    for number in range(1, 9):
        at = number / 10
        metrics.capture(at, (100, 100, 3))
        if number != 2:
            metrics.processed(at, at + 0.01, at + 0.02, predictions.get(number), "test")
    clock[0] = 1.0
    metrics.finish()
    record = metrics.report()
    truth = {
        "schema_version": 1,
        "session_id": metrics.session_id,
        "frames": [{"frame_id": i, "box": None if i in (3, 6) else target} for i in range(1, 9)],
    }
    return record, truth


def test_all_capture_frames_count_missing_and_wrong_boxes_and_failed_reentry(trial):
    result = evaluate(*trial)
    assert result["frames"] == 8
    assert result["visible_frames"] == 6
    assert result["matched_frames"] == 2
    assert result["tracking_success_rate"] == pytest.approx(1 / 3)
    assert result["mean_iou_visible"] == pytest.approx(1 / 3)
    assert result["missed_visible_frames"] == 4
    assert result["false_box_frames"] == 2
    assert result["precision"] == 0.5
    assert result["background_false_box_rate"] == 0.5
    assert result["reentry_success_rate"] == 0.5
    assert result["reentry_events"][0]["start_frame_id"] == 4
    assert result["reentry_events"][0]["reacquired_frame_id"] == 5
    assert result["reentry_delay_s"]["p95"] == pytest.approx(0.12)
    assert result["reentry_events"][1]["delay_s"] is None
    assert "2/6 가시 프레임" in evaluation_text(result)


def test_no_positive_or_reentry_denominator_is_unavailable_not_zero(trial):
    record, truth = trial
    for label in truth["frames"]:
        label["box"] = None
    result = evaluate(record, truth)
    assert result["tracking_success_rate"] is None
    assert result["mean_iou_visible"] is None
    assert result["reentry_success_rate"] is None
    assert "평가 대상 없음" in evaluation_text(result)


@pytest.mark.parametrize(
    "case",
    [
        "session",
        "missing",
        "duplicate",
        "extra",
        "running",
        "failed",
        "partial",
        "nan",
        "bool",
        "bounds",
        "order",
        "time",
        "complete",
        "unprocessed",
        "no_box",
        "stale_box",
        "version_bool",
    ],
)
def test_invalid_or_partial_evidence_never_produces_accuracy(trial, case):
    record, truth = copy.deepcopy(trial)
    if case == "session":
        truth["session_id"] = "different"
    elif case == "missing":
        truth["frames"].pop()
    elif case == "duplicate":
        truth["frames"].append(truth["frames"][0])
    elif case == "extra":
        truth["frames"].append({"frame_id": 9, "box": None})
    elif case in ("running", "failed"):
        record["status"] = case
    elif case == "partial":
        record["performance"]["counts"]["capture"] += 1
    elif case in ("nan", "bool", "bounds"):
        truth["frames"][0]["box"][0] = {"nan": float("nan"), "bool": True, "bounds": -1}[case]
    elif case == "order":
        record["frames"][1]["frame_id"] = 1
    elif case == "time":
        record["frames"][1]["timestamp_s"] = 0.1
    elif case == "complete":
        record["frames"][0]["completed_s"] = 0
    elif case == "unprocessed":
        record["frames"][0]["processed"] = False
    elif case == "no_box":
        truth["frames"][0].pop("box")
    elif case == "stale_box":
        record["frames"][0]["completed_s"] = 0.8
    elif case == "version_bool":
        truth["schema_version"] = True
    with pytest.raises(ValueError):
        evaluate(record, truth)


def test_cli_outputs_machine_readable_result_and_rejects_bad_json(trial, tmp_path, capsys):
    record, truth = trial
    inputs = [tmp_path / "record.json", tmp_path / "truth.json"]
    for path, value in zip(inputs, (record, truth), strict=True):
        path.write_text(json.dumps(value))
    argv = ["--record", str(inputs[0]), "--truth", str(inputs[1])]
    assert main(argv) == 0
    assert json.loads(capsys.readouterr().out)["missed_visible_frames"] == 4
    inputs[1].write_text('{"schema_version":1,"schema_version":1}')
    assert main(argv) == 1
    assert "Duplicate JSON key" in capsys.readouterr().out
