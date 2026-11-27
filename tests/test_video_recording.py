import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools import video_recording
from tools.video_frames import extract_video
from tools.video_recording import CameraRecording


def test_failed_writer_preserves_existing_clip_and_releases_camera(monkeypatch, tmp_path):
    target = tmp_path / "기존 영상.mp4"
    target.write_bytes(b"previous recording")
    camera = Mock()
    camera.read.return_value = (True, SimpleNamespace(shape=(48, 64, 3)))
    writer = Mock()
    writer.isOpened.return_value = False
    cv = SimpleNamespace(
        flip=lambda frame, axis: frame,
        VideoWriter_fourcc=lambda *args: 0,
        VideoWriter=lambda *args: writer,
    )
    monkeypatch.setitem(sys.modules, "cv2", cv)

    @contextmanager
    def source():
        try:
            yield camera
        finally:
            camera.release()

    recorder = CameraRecording(source())
    recorder.destination = target
    recorder.run()
    assert recorder.saved is None and "MP4" in recorder.error
    assert target.read_bytes() == b"previous recording" and list(tmp_path.iterdir()) == [target]
    writer.release.assert_called_once()
    camera.release.assert_called_once()


@pytest.mark.parametrize("ending", ["stop", "disconnect", "collision", "permission"])
@pytest.mark.parametrize("fps", [30, 60])
def test_flipped_mp4_and_review_frames_preserve_orientation_and_camera_time(
    monkeypatch, tmp_path, ending, fps
):
    cv = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    raw = np.zeros((48, 64, 3), dtype=np.uint8)
    raw[:24, :32] = (0, 0, 225)
    raw[:24, 32:] = (225, 0, 0)
    raw[24:] = (0, 225, 0)
    clock = [0.0]
    monkeypatch.setattr(video_recording, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    target = tmp_path / "학습 촬영.mp4"
    if ending == "permission":
        monkeypatch.setattr(Path, "hardlink_to", Mock(side_effect=PermissionError("locked")))
    frames = iter(range(7))

    def read():
        index = next(frames, None)
        if index is None:
            if ending == "collision":
                target.write_bytes(b"another file")
            if ending != "disconnect":
                clock[0] += 0.24
                recorder.request_stop()
            return False, None
        clock[0] = index / 4
        if index == 1:
            assert not target.exists() and np.array_equal(recorder.frame, raw[::-1])
            if ending == "permission":
                target.write_bytes(b"another file")
            recorder.destination = target
        return True, raw

    @contextmanager
    def source():
        yield SimpleNamespace(read=read)

    recorder = CameraRecording(source(), fps)
    recorder.run()
    if ending in ("collision", "permission"):
        assert target.read_bytes() == b"another file"
        assert recorder.saved != target and recorder.saved.parent == tmp_path
    else:
        assert recorder.saved == target
    assert bool(recorder.error) == (ending == "disconnect")
    encoded = cv.VideoCapture(str(recorder.saved))
    try:
        assert encoded.get(cv.CAP_PROP_FPS) == pytest.approx(fps)
    finally:
        encoded.release()
    report = extract_video(recorder.saved, tmp_path / "검수")
    assert report["status"] == "completed"
    assert report["frames_decoded"] == (
        int(1.25 * fps) + 1 if ending == "disconnect" else 1.5 * fps
    )
    assert [s["timestamp_s"] for s in report["samples"]] == pytest.approx([0, 0.5, 1])
    for sample in report["samples"]:
        image = cv.imread(str(tmp_path / "검수" / sample["image_path"]))
        assert image.shape == raw.shape
        for y, x in ((8, 8), (32, 8), (32, 56)):
            assert np.allclose(image[y, x], raw[-1 - y, x], atol=8)
    labels = json.loads((tmp_path / "검수" / "annotations.example.json").read_text("utf-8"))
    assert labels["status"] == "needs_review"


@pytest.mark.parametrize(
    ("finish", "mode", "resolution", "fps"),
    [
        ("button", "4K · 30fps", (3840, 2160), 30),
        ("leave_page", "1080p · 60fps", (1920, 1080), 60),
        ("close", "1280×720", (1280, 720), 30),
        ("collision", "640×480", (640, 480), 30),
    ],
)
def test_record_through_gui_then_import_without_reselecting_clip(
    monkeypatch, tmp_path, finish, mode, resolution, fps
):
    tk = pytest.importorskip("tkinter")
    pytest.importorskip("cv2")
    pytest.importorskip("PIL.ImageTk")
    np = pytest.importorskip("numpy")
    from tkinter import filedialog, messagebox

    from tools import camera_gui

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    root.attributes("-alpha", 0.0)
    raw = np.zeros((48, 64, 3), dtype=np.uint8)
    raw[:24] = 220
    opened, released = [], []

    def read():
        time.sleep(0.015)
        return True, raw

    @contextmanager
    def source(camera, resolution, fps):
        opened.append((camera["id"], resolution, fps))
        try:
            yield SimpleNamespace(read=read)
        finally:
            released.append(True)

    camera = {"id": "sample", "kind": "USB", "name": "샘플", "index": 0, "builtin": False}
    target = tmp_path / "첫 촬영.mp4"
    from tools.pretrained_drone import FIELD_CAMERA

    field_camera = {**camera, "id": "external", "name": FIELD_CAMERA}
    monkeypatch.setattr(camera_gui, "list_cameras", lambda: [camera, field_camera])
    monkeypatch.setattr(camera_gui, "camera_input", source)
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **kw: str(target))
    monkeypatch.setattr(filedialog, "askdirectory", lambda **kw: str(tmp_path))
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **kw: pytest.fail("clip reselected"))
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **kw: None)
    warnings = []
    monkeypatch.setattr(
        messagebox, "showwarning", lambda title, message, **kw: warnings.append(message)
    )

    def wait_for(test):
        deadline = time.monotonic() + 5
        while not test():
            assert time.monotonic() < deadline
            root.update()
            time.sleep(0.005)

    app = camera_gui.CameraGUI(root)
    page = app.recording_page
    destroyed = []
    root.bind("<Destroy>", lambda event: destroyed.append(True) if event.widget is root else None)
    try:
        wait_for(lambda: not app.scanning_cameras)
        original_label = app.camera.get()
        field_label = next(
            label for label, device in app.cameras.items() if device["id"] == "external"
        )
        page.camera.set(field_label)
        assert app.flip_vertical.get()
        app.source.set("video")
        app.change_source()
        assert str(app.flip_check.cget("variable")) == str(app.video_flip_vertical)
        assert not app.video_flip_vertical.get()
        app.flip_check.invoke()
        assert app.video_flip_vertical.get()
        page.camera.set(original_label)
        assert not app.flip_vertical.get() and app.video_flip_vertical.get()
        app.flip_check.invoke()
        assert not app.video_flip_vertical.get()
        page.camera.set(field_label)
        app.source.set("camera")
        app.change_source()
        assert str(app.flip_check.cget("variable")) == str(app.flip_vertical)
        app.flip_vertical.set(False)
        page.camera.set(field_label)
        assert not app.flip_vertical.get()
        page.camera.set(original_label)
        assert not app.flip_vertical.get()

        root_bindings = (root.bind("<Escape>"), root.bind("<FocusOut>"))
        for channels in (1, 2, 2):
            app.open_pan_control(channels)
        assert app.pan_window is None
        assert (root.bind("<Escape>"), root.bind("<FocusOut>")) == root_bindings
        for previous in ("tracking", "learning"):
            app.page.set(previous)
            app.show_page()
            root.update_idletasks()
            assert all(widget.winfo_ismapped() for widget in app.dashboard_widgets)
            app.page.set("recording")
            app.show_page()
            root.update_idletasks()
            assert not any(widget.winfo_ismapped() for widget in app.dashboard_widgets)
            visible = set()
            widget = page.frame.tk_focusNext()
            while widget is not None and widget not in visible:
                visible.add(widget)
                widget = widget.tk_focusNext()
            assert page.preview_button in visible
            assert not {app.start_button, app.video_button}.intersection(visible)
        assert {"4K · 30fps", "1080p · 60fps"}.issubset(page.mode_select.cget("values"))
        assert app.camera_mode_select.get() == "1080p · 60fps"
        page.mode_select.set(mode)
        assert app.camera_mode_select.get() == mode and not opened
        page.preview_button.invoke()
        wait_for(lambda: page.record_button.instate(["!disabled"]))
        assert opened == [("sample", resolution, fps)] and not target.exists()
        assert page.mode_select.instate(["disabled"]) and app.camera_mode_select.instate(
            ["disabled"]
        )
        assert f"실제 입력 64×48 · MP4 {fps}fps" in page.notice.get()
        page.record_button.invoke()
        wait_for(lambda: page.state.get().startswith("●  녹화 중"))
        if finish == "collision":
            target.write_bytes(b"other clip")
        if finish == "close":
            app.close()
            wait_for(lambda: destroyed)
            assert target.is_file() and page.last_clip == target and released == [True]
            return
        if finish == "leave_page":
            app.page.set("learning")
            app.show_page()
        else:
            page.stop_button.invoke()
        wait_for(lambda: page.worker is None)
        if finish == "collision":
            assert page.last_clip != target and target.read_bytes() == b"other clip"
            assert str(page.last_clip) in warnings[0] and str(page.last_clip) in page.notice.get()
        else:
            assert page.last_clip == target and not warnings
        assert page.last_clip.is_file() and released == [True]
        app.page.set("recording")
        app.show_page()
        page.import_button.invoke()
        wait_for(lambda: app.video_thread is None)
        assert app.page.get() == "learning" and "추출 완료" in app.status.get()
        samples = json.loads(next(tmp_path.glob("video-*/samples.json")).read_text("utf-8"))
        assert samples["original_video"] == str(page.last_clip.resolve()) and samples["samples"]
        assert samples["recorded_flip_vertical"] is True
        if finish == "button":
            received = []

            def inspect_first_frame(session):
                try:
                    received.append(session.frames.get(timeout=3)[1])
                finally:
                    session.request_stop()

            monkeypatch.setattr(camera_gui.CameraSession, "process", inspect_first_frame)
            monkeypatch.setattr(filedialog, "askopenfilename", lambda **kw: str(page.last_clip))
            model = tmp_path / "test.pt"
            model.touch()
            app.model.set(str(model))
            app.device.set("cpu")
            app.camera.set(field_label)
            app.page.set("tracking")
            app.show_page()
            app.choose_video()
            assert app.flip_vertical.get() and not app.video_flip_vertical.get()
            for expected in (raw[::-1], raw):
                app.start_selected()
                wait_for(lambda: app.session is None)
                assert np.allclose(received.pop(), expected, atol=8)
                app.flip_check.invoke()
    finally:
        if not destroyed:
            page.stop()
            if page.worker:
                page.worker.thread.join(2)
            app.close()
