import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import tools.training_gui as gui


class Variable:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


@pytest.fixture
def window(tmp_path):
    app = gui.TrainingWindow.__new__(gui.TrainingWindow)
    app.workspace = tmp_path
    app.dataset = app.result = app.process = app.log_reader = app.log_writer = None
    app.approved = app.cancelled = app.closing = False
    app.sequence, app.stage = 0, None
    app.log_decoder = gui.codecs.getincrementaldecoder("utf-8")(errors="replace")
    app.sources = []
    app.folder, app.split, app.capture = Variable(), Variable("train"), Variable()
    app.parent_folder, app.version = Variable(str(tmp_path)), Variable("manual-v1")
    app.groups_reviewed, app.data_reviewed = Variable(False), Variable(False)
    app.model, app.device = Variable(), Variable("cuda")
    app.epochs, app.imgsz, app.batch = Variable("2"), Variable("64"), Variable("1")
    app.data_info, app.status = Variable(), Variable()
    app.messages, app.errors, app.callbacks, app.destroyed = [], [], [], []
    app.can_run = lambda: True
    app.append_log = app.messages.append
    app.error = app.errors.append
    app.refresh = lambda: None
    app.poll_id = "poll-id"
    app.window = SimpleNamespace(
        after=lambda *args: app.callbacks.append(args),
        after_cancel=lambda *args: None,
        destroy=lambda: app.destroyed.append(True),
    )
    yield app
    if app.process is not None:
        app.process.kill()
        app.process.wait(timeout=5)
    for stream in (app.log_reader, app.log_writer):
        if stream is not None:
            stream.close()


@pytest.fixture
def labels(tmp_path):
    folder = tmp_path / "수동 라벨"
    folder.mkdir()
    (folder / "samples.json").write_text(
        json.dumps(
            dict(
                schema_version=1,
                session_id="one",
                source_type="camera_stills",
                status="completed",
                samples=[
                    dict(
                        frame_id=1,
                        width=100,
                        height=100,
                        timestamp_s=0,
                        image_path="frames/frame-000001.png",
                        sha256="a" * 64,
                    )
                ],
            )
        ),
        encoding="utf-8",
    )
    (folder / "annotations.json").write_text(
        json.dumps(
            dict(
                schema_version=1,
                session_id="one",
                status="reviewed",
                target_class="drone",
                frames=[dict(frame_id=1, box=[10, 10, 40, 40])],
            )
        ),
        encoding="utf-8",
    )
    (folder / "measurement.json").write_text("{}", encoding="utf-8")
    return folder


def test_source_uses_reviewed_labels_and_camera_or_video_provenance(labels):
    entry = gui.label_source(labels, "train", " 같은 촬영 ")
    assert entry["capture_id"] == "같은 촬영" and entry["source_type"] == "photos"
    assert Path(entry["record"]).name == "measurement.json"
    path = labels / "samples.json"
    data = json.loads(path.read_text())
    data["source_type"] = "video_frames"
    path.write_text(json.dumps(data))
    entry = gui.label_source(labels, "val", "다른 영상")
    assert entry["source_type"] == "video" and "record" not in entry
    data = json.loads((labels / "annotations.json").read_text())
    data["status"] = "needs_review"
    (labels / "annotations.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match="검수 완료 저장"):
        gui.label_source(labels, "train", "one")


def test_source_requires_capture_group_and_real_camera_record(labels):
    for split, group in (("random", "one"), ("train", " ")):
        with pytest.raises(ValueError, match="촬영 그룹"):
            gui.label_source(labels, split, group)
    (labels / "measurement.json").unlink()
    with pytest.raises(ValueError, match="measurement.json"):
        gui.label_source(labels, "train", "one")


def sources():
    return [
        dict(split=s, capture_id=s, samples=f"/{s}/samples.json") for s in ("train", "val", "test")
    ]


def test_plan_keeps_capture_splits_and_rejects_leakage():
    entries = sources()
    plan = gui.reviewed_plan(entries)
    assert plan["status"] == "reviewed" and plan["sources"] == entries
    assert plan["split_rule"] == "capture_group_before_case_selection"
    with pytest.raises(ValueError, match="train / val / test"):
        gui.reviewed_plan(entries[:2])
    entries[1]["capture_id"] = "train"
    with pytest.raises(ValueError, match="같은 촬영 그룹"):
        gui.reviewed_plan(entries)
    entries[1]["capture_id"] = "val"
    entries[1]["samples"] = entries[0]["samples"]
    with pytest.raises(ValueError, match="중복 폴더"):
        gui.reviewed_plan(entries)


def test_prepare_requires_human_group_review_and_writes_a_new_workspace(window):
    app, commands = window, []
    app.sources = sources()
    app.start_job = lambda stage, command: commands.append((stage, command))
    app.prepare()
    assert not commands and "직접 확인" in str(app.errors[-1])
    app.groups_reviewed.set(True)
    app.prepare()
    first = app.workspace
    assert commands[-1][0] == "prepare" and first.is_dir()
    assert gui.read_json(first / "plan.json") == gui.reviewed_plan(sources())
    assert app.dataset is None and not app.approved
    app.prepare()
    assert app.workspace != first and (first / "plan.json").is_file()


@pytest.mark.parametrize("device", ["cuda", "mps", "cpu", "auto"])
def test_training_command_preserves_paths_and_explicit_device(tmp_path, device):
    model = tmp_path / "trusted model.pt"
    model.touch()
    command = gui.training_command(
        tmp_path, str(model), device, "3", "64", "2", tmp_path / "new output"
    )
    assert command[:4] == [sys.executable, "-u", "-m", "tools.train"]
    assert command[command.index("--device") + 1] == device
    assert command[command.index("--model") + 1] == str(model)
    assert "--check-only" not in command and "test" not in command


@pytest.mark.parametrize(
    "epochs,imgsz,batch,device",
    [
        ("x", "64", "1", "cpu"),
        ("0", "64", "1", "cpu"),
        ("1", "63", "1", "cpu"),
        ("1", "64", "0", "cpu"),
        ("1", "64", "1", "other"),
    ],
)
def test_invalid_training_options_do_not_launch(tmp_path, epochs, imgsz, batch, device):
    model = tmp_path / "trusted.pt"
    model.touch()
    with pytest.raises(ValueError):
        gui.training_command(tmp_path, str(model), device, epochs, imgsz, batch, tmp_path / "new")
    with pytest.raises(ValueError, match="로컬 .pt"):
        gui.training_command(tmp_path, "missing.pt", "cpu", "1", "64", "1", tmp_path / "new")


def test_prepare_and_approval_are_not_automatic_training(window):
    app, calls = window, []
    app.start_job = lambda *args: calls.append(args)
    app.dataset = app.workspace / "dataset"
    app.start_training()
    assert not calls
    app.approve()
    assert not calls and "직접 확인" in str(app.errors[-1])
    app.data_reviewed.set(True)
    app.approve()
    assert calls[0][0] == "approve" and "--approve" in calls[0][1]
    assert not app.approved  # Only successful validation can enable training.
    app.approved = True
    app.groups_reviewed.set(False)
    app.review_change()
    assert not app.approved


def test_background_job_uses_current_python_utf8_no_shell_and_excludes_tracking(
    window, monkeypatch
):
    app, calls = window, []
    app.can_run = lambda: False
    app.start_job("train", ["never"])
    assert app.process is None and app.errors
    app.can_run = lambda: True
    process = SimpleNamespace(kill=lambda: None, wait=lambda **kw: None)
    monkeypatch.setattr(gui.subprocess, "Popen", lambda *a, **kw: calls.append((a, kw)) or process)
    app.start_job("train", [sys.executable, "-u", "-m", "tools.train"])
    assert app.process is process
    assert calls[0][1]["cwd"] == gui.ROOT and "shell" not in calls[0][1]
    assert calls[0][1]["env"]["PYTHONIOENCODING"] == "utf-8"
    assert calls[0][1]["stderr"] == subprocess.STDOUT
    assert gui.read_json(app.record_path)["status"] == "running"
    app.start_job("train", ["duplicate"])
    assert len(calls) == 1


def test_process_launch_failure_closes_logs_and_records_failure(window, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("cannot launch")

    monkeypatch.setattr(gui.subprocess, "Popen", fail)
    window.start_job("prepare", ["missing"])
    assert window.process is window.log_reader is window.log_writer is None
    assert gui.read_json(window.record_path)["status"] == "failed"
    assert "cannot launch" in "".join(window.messages)


@pytest.mark.parametrize(
    "stage,code,cancelled", [("prepare", 1, False), ("approve", -15, True), ("train", 0, False)]
)
def test_finish_never_claims_failed_cancelled_or_missing_training_results_as_success(
    window, stage, code, cancelled
):
    app = window
    app.stage, app.cancelled = stage, cancelled
    app.record_path, app.job_record = app.workspace / "job.json", {}
    app.pending_output = app.workspace / "missing-training"
    app.finish(code)
    assert gui.read_json(app.record_path)["status"] == ("cancelled" if cancelled else "failed")
    assert app.result is None and not app.approved


def test_finish_success_only_after_expected_artifacts_exist(window):
    app = window
    app.stage, app.record_path, app.job_record = "prepare", app.workspace / "job.json", {}
    dataset = app.workspace / "dataset"
    dataset.mkdir()
    manifest = dataset / "manifest.json"
    manifest.write_text('{"status":"needs_review"}')
    app.finish(0)
    assert app.dataset == dataset and not app.approved
    manifest.write_text('{"status":"ready"}')
    app.stage = "approve"
    app.finish(0)
    assert app.approved
    app.pending_output = app.workspace / "train-one"
    (app.pending_output / "weights").mkdir(parents=True)
    (app.pending_output / "training_record.json").write_text('{"status":"completed"}')
    (app.pending_output / "weights/best.pt").touch()
    app.stage = "train"
    app.finish(0)
    assert app.result == app.pending_output


def test_real_process_can_be_stopped_and_partial_utf8_logs_are_kept(window):
    app = window
    command = [sys.executable, "-u", "-c", "import time; print('수동 라벨 학습'); time.sleep(60)"]
    app.start_job("train", command)
    process = app.process
    app.stop()
    process.wait(timeout=5)
    app.poll()
    record = gui.read_json(app.record_path)
    assert record["status"] == "cancelled" and Path(record["log"]).is_file()
    assert app.process is None and not app.result


def test_poll_bounds_log_reads_and_kills_an_unresponsive_cancelled_process(window, monkeypatch):
    app, killed = window, []
    app.process = SimpleNamespace(poll=lambda: None, kill=lambda: killed.append(True))
    app.log_reader = io.BytesIO(b"x" * 500000)
    app.cancelled, app.cancel_time = True, 0
    monkeypatch.setattr(gui.time, "monotonic", lambda: 10)
    app.poll()
    assert len(app.messages[0]) == 65536 and killed == [True]
    app.process = None


def test_poll_keeps_utf8_characters_across_chunk_and_temporary_eof_boundaries(window):
    app = window
    app.process = SimpleNamespace(poll=lambda: None)
    text = "a" * 65535 + "한글"
    app.log_reader = io.BytesIO(text.encode("utf-8"))
    app.poll()
    app.poll()
    assert "".join(app.messages) == text
    app.messages.clear()
    encoded = "새 로그".encode()
    app.log_reader = io.BytesIO(encoded[:2])
    app.poll()
    position = app.log_reader.tell()
    app.log_reader.write(encoded[2:])
    app.log_reader.seek(position)
    app.poll()
    assert "".join(app.messages) == "새 로그"
    app.process = None


def test_final_log_tail_resets_decoder_and_starts_at_a_utf8_boundary(window):
    app, finished = window, []
    app.process = SimpleNamespace(poll=lambda: 0)
    app.log_reader = io.BytesIO(("한글" * 50000).encode("utf-8"))

    def finish(code):
        finished.append(code)
        app.process = None

    app.finish = finish
    app.poll()
    assert finished == [0]
    assert "�" not in "".join(app.messages)
    assert "중간 출력" in "".join(app.messages) and app.messages[-1].endswith("한글")


def test_close_can_cancel_and_waits_for_process_exit(window, monkeypatch):
    app, answers, stopped = window, [False], []
    monkeypatch.setitem(
        sys.modules,
        "tkinter",
        SimpleNamespace(messagebox=SimpleNamespace(askyesno=lambda *a, **kw: answers[0])),
    )
    app.process = SimpleNamespace(terminate=lambda: stopped.append(True), poll=lambda: -15)
    assert not app.request_close() and not app.closing and not stopped
    answers[0] = True
    assert not app.request_close() and app.closing and stopped == [True]
    assert not app.request_close() and stopped == [True]
    app.process = None
    assert app.request_close() and app.destroyed == [True]
