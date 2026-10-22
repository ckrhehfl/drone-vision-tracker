"""Hardware/display-free label editing and loss prevention checks."""

import copy
import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from tools.label_editor import UNLABELED, LabelDocument, LabelEditor


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def collection(tmp_path):
    root = tmp_path / "수집 폴더"
    (root / "frames").mkdir(parents=True)
    samples = []
    for frame_id in (1, 16):
        data = f"source-{frame_id}".encode()
        name = f"frames/frame-{frame_id:06d}.png"
        (root / name).write_bytes(data)
        samples.append(
            dict(
                frame_id=frame_id,
                timestamp_s=frame_id / 30,
                width=100,
                height=80,
                image_path=name,
                sha256=hashlib.sha256(data).hexdigest(),
            )
        )
    write_json(
        root / "samples.json",
        dict(
            schema_version=1,
            session_id="session-a",
            source_type="camera_stills",
            status="completed",
            samples=samples,
        ),
    )
    (root / "annotations.example.json").write_text("keep template", encoding="utf-8")
    return root


@pytest.fixture
def fake_pillow(monkeypatch):
    class Image:
        size = (100, 80)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def verify(self):
            pass

    monkeypatch.setitem(
        sys.modules, "PIL", SimpleNamespace(Image=SimpleNamespace(open=lambda p: Image()))
    )
    return Image


def test_partial_and_reviewed_roundtrip_keep_unlabeled_distinct_from_background(
    collection, fake_pillow
):
    doc = LabelDocument(collection)
    before = {p.name: p.read_bytes() for p in (collection / "frames").iterdir()}
    assert doc.remaining == 2 and not doc.dirty
    assert not doc.path.exists()
    doc.set_box(0, [10, 5, 30, 25])
    doc.save()
    assert not doc.dirty and doc.remaining == 1
    partial = json.loads(doc.path.read_text())
    assert partial["status"] == "needs_review"
    assert partial["frames"][1]["box"] == UNLABELED
    with pytest.raises(ValueError, match="미라벨"):
        doc.save(reviewed=True)
    assert json.loads(doc.path.read_text()) == partial
    resumed = LabelDocument(collection)
    resumed.set_box(1, None)
    assert resumed.remaining == 0
    resumed.save(reviewed=True)
    complete = json.loads(doc.path.read_text())
    assert complete["status"] == "reviewed" and complete["frames"][1]["box"] is None
    assert complete["session_id"] == "session-a" and complete["target_class"] == "drone"
    assert {p.name: p.read_bytes() for p in (collection / "frames").iterdir()} == before
    assert (collection / "annotations.example.json").read_text() == "keep template"


def test_edit_and_undo_reviewed_labels_require_review_again(collection, fake_pillow):
    doc = LabelDocument(collection)
    doc.set_box(0, [1, 2, 20, 30])
    doc.set_box(1, None)
    doc.save(reviewed=True)
    resumed = LabelDocument(collection)
    resumed.set_box(1, [30, 30, 40, 40])
    assert resumed.annotation["status"] == "needs_review" and resumed.dirty
    assert resumed.undo() == 1 and resumed.rows[1]["box"] is None
    assert resumed.dirty and resumed.annotation["status"] == "needs_review"
    resumed.save()
    assert LabelDocument(collection).annotation["status"] == "needs_review"


@pytest.mark.parametrize("source_type", ["camera_stills", "video_frames"])
def test_editor_preserves_source_and_annotation_metadata(collection, source_type):
    source = json.loads((collection / "samples.json").read_text())
    source.update(
        source_type=source_type, original_video="outside.mp4", original_video_sha256="f" * 64
    )
    write_json(collection / "samples.json", source)
    doc = LabelDocument(collection)
    doc.annotation["reviewer_note"] = "keep"
    doc.rows[0]["note"] = "keep row"
    doc.set_box(0, None)
    doc.save()
    resumed = LabelDocument(collection)
    assert resumed.annotation["reviewer_note"] == "keep"
    assert resumed.rows[0]["note"] == "keep row"
    assert json.loads((collection / "samples.json").read_text()) == source


@pytest.mark.parametrize(
    "problem",
    [
        "version",
        "session",
        "status",
        "source",
        "error",
        "empty",
        "duplicate",
        "id",
        "size",
        "time",
        "hash",
        "path",
    ],
)
def test_bad_collection_is_rejected_without_writing_labels(collection, problem):
    path = collection / "samples.json"
    data = json.loads(path.read_text())
    if problem == "version":
        data["schema_version"] = True
    elif problem == "session":
        data["session_id"] = ""
    elif problem == "status":
        data["status"] = "failed"
    elif problem == "source":
        data["source_type"] = "unverified_photos"
    elif problem == "error":
        data["error"] = "decode failure"
    elif problem == "empty":
        data["samples"] = []
    elif problem == "duplicate":
        data["samples"].append(data["samples"][0])
    elif problem == "id":
        data["samples"][0]["frame_id"] = True
    elif problem == "size":
        data["samples"][0]["width"] = 0
    elif problem == "time":
        data["samples"][0]["timestamp_s"] = float("nan")
    elif problem == "hash":
        data["samples"][0]["sha256"] = "not-a-hash"
    elif problem == "path":
        data["samples"][0]["image_path"] = "../outside.png"
    write_json(path, data)
    with pytest.raises(ValueError):
        LabelDocument(collection)
    assert not (collection / "annotations.json").exists()


@pytest.mark.parametrize(
    "problem", ["session", "class", "missing", "extra", "duplicate", "box", "reviewed"]
)
def test_invalid_existing_annotations_are_preserved_and_rejected(collection, problem):
    doc = LabelDocument(collection)
    data = copy.deepcopy(doc.annotation)
    if problem == "session":
        data["session_id"] = "other"
    elif problem == "class":
        data["target_class"] = "person"
    elif problem == "missing":
        data["frames"].pop()
    elif problem == "extra":
        data["frames"].append(dict(frame_id=99, box=None))
    elif problem == "duplicate":
        data["frames"].append(data["frames"][0])
    elif problem == "box":
        data["frames"][0]["box"] = [0, 0, 101, 80]
    elif problem == "reviewed":
        data["status"] = "reviewed"
    write_json(doc.path, data)
    before = doc.path.read_bytes()
    with pytest.raises(ValueError):
        LabelDocument(collection)
    assert doc.path.read_bytes() == before


@pytest.mark.parametrize("existing", [True, False])
def test_external_label_changes_are_never_overwritten(collection, existing):
    if existing:
        LabelDocument(collection).save()
    doc = LabelDocument(collection)
    doc.set_box(0, None)
    doc.path.write_text("external edit", encoding="utf-8")
    with pytest.raises(ValueError, match="다른 프로그램"):
        doc.save()
    assert doc.path.read_text() == "external edit" and doc.dirty


def test_changed_collection_or_deleted_labels_preserve_edits(collection):
    doc = LabelDocument(collection)
    doc.save()
    doc.set_box(0, None)
    doc.path.unlink()
    with pytest.raises(ValueError, match="다른 프로그램"):
        doc.save()
    assert not doc.path.exists() and doc.dirty
    doc.samples_path.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="수집 기록"):
        doc.save()


def test_failed_atomic_save_keeps_previous_file_and_memory(collection, monkeypatch):
    import tools.label_editor as labels

    doc = LabelDocument(collection)
    doc.save()
    previous = doc.path.read_bytes()
    doc.set_box(0, None)

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(labels.os, "replace", fail)
    with pytest.raises(OSError, match="disk unavailable"):
        doc.save()
    assert doc.path.read_bytes() == previous and doc.dirty
    assert not list(collection.glob(".annotations-*"))


def test_external_change_during_temp_write_is_caught(collection, monkeypatch):
    import tools.label_editor as labels

    doc = LabelDocument(collection)
    doc.save()
    doc.set_box(0, None)
    monkeypatch.setattr(
        labels.os, "fsync", lambda fd: doc.path.write_text("external", encoding="utf-8")
    )
    with pytest.raises(ValueError, match="다른 프로그램"):
        doc.save()
    assert doc.path.read_text() == "external" and not list(collection.glob(".annotations-*"))


def test_changed_images_and_wrong_actual_size_cannot_be_marked_reviewed(collection, fake_pillow):
    doc = LabelDocument(collection)
    doc.set_box(0, None)
    doc.set_box(1, [1, 1, 2, 2])
    doc.save()
    before = doc.path.read_bytes()
    fake_pillow.size = (80, 100)
    with pytest.raises(ValueError, match="실제 크기"):
        doc.save(reviewed=True)
    fake_pillow.size = (100, 80)
    (collection / doc.samples[0]["image_path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="해시"):
        doc.save(reviewed=True)
    assert doc.path.read_bytes() == before


def test_symlink_escape_and_output_symlink_are_rejected(collection, tmp_path):
    doc = LabelDocument(collection)
    image = collection / doc.samples[0]["image_path"]
    outside = tmp_path / "outside.png"
    outside.write_bytes(image.read_bytes())
    image.unlink()
    image.symlink_to(outside)
    with pytest.raises(ValueError, match="폴더 밖"):
        doc.image_bytes(0)
    doc.path.symlink_to(outside)
    with pytest.raises(ValueError):
        LabelDocument(collection)
    with pytest.raises(ValueError):
        doc.save()
    assert outside.read_bytes() == b"source-1"


@pytest.fixture
def editor(collection):
    app = LabelEditor.__new__(LabelEditor)
    app.document = LabelDocument(collection)
    app.image = SimpleNamespace(size=(100, 80))
    app.index, app.scale, app.offset, app.gesture = 0, 2, (40, 60), None
    app.canvas = SimpleNamespace(
        delete=lambda *a: None,
        create_rectangle=lambda *a, **kw: None,
        create_text=lambda *a, **kw: None,
        focus_set=lambda: None,
    )
    app.refresh = lambda: None
    return app


def at(x, y):
    return SimpleNamespace(x=x * 2 + 40, y=y * 2 + 60)


def test_draw_reverse_drag_uses_original_coordinates_and_ignores_margin(editor):
    editor.begin(SimpleNamespace(x=20, y=50))
    assert editor.gesture is None
    editor.begin(at(80, 70))
    editor.end(at(20, 10))
    assert editor.document.rows[0]["box"] == [20, 10, 80, 70]
    assert editor.document.dirty


def test_move_and_resize_keep_box_inside_original_image(editor):
    editor.document.set_box(0, [10, 10, 50, 40])
    editor.begin(at(30, 25))
    assert editor.gesture["mode"] == "move"
    editor.end(at(130, 120))
    assert editor.document.rows[0]["box"] == [60, 50, 100, 80]
    editor.begin(at(60, 50))
    assert editor.gesture["mode"] == "resize"
    editor.end(at(0, 0))
    assert editor.document.rows[0]["box"] == [0, 0, 100, 80]


def test_cancel_and_stationary_click_do_not_change_existing_box(editor):
    editor.document.set_box(0, [10, 10, 50, 40])
    before = copy.deepcopy(editor.document.rows)
    history_size = len(editor.document.history)
    editor.begin(at(70, 70))
    editor.drag(at(80, 79))
    editor.cancel_gesture()
    assert editor.document.rows == before
    editor.begin(at(25, 25))
    editor.end(at(25, 25))
    assert editor.document.rows == before and len(editor.document.history) == history_size


@pytest.mark.parametrize(
    "choice,saved,expected",
    [(None, True, False), (False, True, True), (True, False, False), (True, True, True)],
)
def test_unsaved_close_choices_protect_changes(monkeypatch, editor, choice, saved, expected):
    editor.document.set_box(0, None)
    editor.window = SimpleNamespace(destroy=lambda: destroyed.append(True))
    editor.save = lambda: saved
    destroyed = []
    monkeypatch.setitem(
        sys.modules,
        "tkinter",
        SimpleNamespace(messagebox=SimpleNamespace(askyesnocancel=lambda *a, **kw: choice)),
    )
    assert editor.request_close() is expected
    assert bool(destroyed) is expected


def test_keyboard_navigation_does_not_intercept_entry_cursor(editor):
    actions = []
    entry = SimpleNamespace(widget=SimpleNamespace(winfo_class=lambda: "TEntry"))
    canvas = SimpleNamespace(widget=SimpleNamespace(winfo_class=lambda: "Canvas"))
    assert editor.shortcut(entry, lambda: actions.append(1)) is None
    assert not actions
    assert editor.shortcut(canvas, lambda: actions.append(1)) == "break"
    assert actions == [1]
