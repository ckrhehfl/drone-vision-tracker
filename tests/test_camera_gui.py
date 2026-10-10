"""Hardware-free GUI logic checks; imports no Tk, camera or ML runtime."""

import json
import queue
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from tools.camera_gui import (
    MAX_AGE_S,
    CameraGUI,
    CameraSession,
    choose_target,
    color_mask,
    create_manual_tracker,
    list_cameras,
    match_features,
    match_region,
    match_template,
    observation_for_preview,
    overlap,
    put_latest,
    restore_detection,
    search_tiles,
    selection_box,
    target_crop,
    template_sizes,
    track_motion,
    tracker_detection,
    verify_appearance,
)
from tools.tracking_metrics import SessionMetrics


def test_camera_names_use_avfoundation_order_and_transport_without_capture(monkeypatch):
    import tools.camera_gui as gui

    data = [
        {"id": "001", "name": "USB Webcam", "transport": 0x75736220},
        {"id": "020", "name": "MacBook 카메라", "transport": 0x626C746E},
        {"id": "030", "name": "iPhone 카메라", "transport": 0},
    ]

    def run(args, **kwargs):
        assert args[:4] == ["/usr/bin/osascript", "-l", "JavaScript", "-e"]
        assert "devicesWithMediaType('vide')" in args[4]
        assert "devicesWithMediaType('muxx')" in args[4]
        assert "sortDescriptorWithKeyAscending('uniqueID', true)" in args[4]
        assert "startRunning" not in args[4] and "AVCaptureSession" not in args[4]
        assert kwargs == {"capture_output": True, "text": True, "check": True, "timeout": 3}
        return SimpleNamespace(stdout=json.dumps(data))

    monkeypatch.setattr(gui.sys, "platform", "darwin")
    monkeypatch.setattr(gui.subprocess, "run", run)
    cameras = list_cameras()
    assert [d["index"] for d in cameras] == [0, 1, 2]
    assert [d["id"] for d in cameras] == ["001", "020", "030"]
    assert [d["builtin"] for d in cameras] == [False, True, False]
    assert [d["kind"] for d in cameras] == ["USB 카메라", "Mac 내장 카메라", "외장 카메라"]
    monkeypatch.setattr(gui.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout="[]"))
    assert list_cameras() == []


@pytest.mark.parametrize(
    "failure",
    [
        OSError("unavailable"),
        subprocess.TimeoutExpired("metadata", 3),
        subprocess.CalledProcessError(1, "metadata"),
        "invalid json",
        "{}",
        '[{"id": "x", "name": "camera", "transport": true}]',
        '[{"id": 5, "name": "camera", "transport": 0}]',
    ],
)
def test_camera_listing_failure_is_explicit_and_never_guesses_index_zero(monkeypatch, failure):
    import tools.camera_gui as gui

    def run(*args, **kwargs):
        if isinstance(failure, Exception):
            raise failure
        return SimpleNamespace(stdout=failure)

    monkeypatch.setattr(gui.sys, "platform", "darwin")
    monkeypatch.setattr(gui.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="카메라 목록을 읽지 못했습니다"):
        list_cameras()


def test_other_platforms_keep_existing_number_selection_without_probing(monkeypatch):
    import tools.camera_gui as gui

    monkeypatch.setattr(gui.sys, "platform", "linux")
    monkeypatch.setattr(gui.subprocess, "run", lambda *a, **kw: pytest.fail("must not probe"))
    cameras = list_cameras()
    assert [d["index"] for d in cameras] == list(range(10))
    assert [d["id"] for d in cameras] == [str(i) for i in range(10)]
    assert all(not d["builtin"] for d in cameras)


@pytest.fixture
def camera_controls():
    class Control:
        def __init__(self, value=""):
            self.value, self.options = value, {}

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

        def configure(self, **kwargs):
            self.options.update(kwargs)

    app = CameraGUI.__new__(CameraGUI)
    app.closing = False
    app.metrics = None
    app.session, app.cameras, app.frame, app.result = None, {}, None, None
    for name in (
        "camera",
        "status",
        "camera_select",
        "refresh_button",
        "start_button",
        "performance",
        "save_metrics_button",
        "evaluate_button",
        "collect_button",
    ):
        setattr(app, name, Control())
    for name, value in (
        ("mode", "수동 지정"),
        ("threshold", "0.65"),
        ("model", ""),
        ("device", "cpu"),
        ("resolution", "1920×1080"),
    ):
        setattr(app, name, Control(value))
    app.canvas = SimpleNamespace(delete=lambda *args: None)
    return app


def test_camera_menu_defaults_to_mac_and_preserves_usb_identity(monkeypatch, camera_controls):
    import tools.camera_gui as gui

    usb = {"id": "usb", "name": "Webcam", "index": 0, "builtin": False, "kind": "USB 카메라"}
    twin = dict(usb, id="twin", index=1)
    mac = dict(usb, id="mac", name="MacBook 카메라", index=2, builtin=True, kind="Mac 내장 카메라")
    devices = [usb, twin, mac]
    monkeypatch.setattr(gui, "list_cameras", lambda: devices)
    app = camera_controls
    app.refresh_cameras()
    assert app.cameras[app.camera.get()]["id"] == "mac"
    assert tuple(app.cameras) == app.camera_select.options["values"]
    assert "USB 카메라 · Webcam (2)" in app.cameras
    app.camera.set("USB 카메라 · Webcam (2)")
    devices[:] = [dict(mac, index=0), dict(twin, name="Renamed Webcam", index=1)]
    app.refresh_cameras()
    assert app.cameras[app.camera.get()]["id"] == "twin"
    assert app.camera.get() == "USB 카메라 · Renamed Webcam"
    devices[:] = [mac]
    app.refresh_cameras()
    assert app.cameras[app.camera.get()]["id"] == "mac"
    devices.clear()
    app.refresh_cameras()
    assert app.cameras == {} and app.camera.get() == "연결된 카메라 없음"
    assert app.start_button.options["state"] == "disabled"
    assert "연결된 카메라가 없습니다" in app.status.get()
    app.session = object()
    monkeypatch.setattr(gui, "list_cameras", lambda: pytest.fail("active camera menu is locked"))
    app.refresh_cameras()


@pytest.mark.parametrize("device", ["mps", "cpu"])
def test_camera_start_passes_selected_identity_and_locks_menu(monkeypatch, camera_controls, device):
    import tools.camera_gui as gui

    camera = {"id": "usb", "index": 7, "name": "Webcam"}
    app, calls, errors = camera_controls, [], []
    app.device.set(device)
    app.cameras["USB 카메라 · Webcam"] = camera
    app.camera.set("USB 카메라 · Webcam")
    monkeypatch.setitem(
        sys.modules,
        "tkinter",
        SimpleNamespace(messagebox=SimpleNamespace(showerror=lambda *a: errors.append(a))),
    )

    def start(*args):
        calls.append(args)
        return SimpleNamespace(stop=threading.Event(), metrics=SessionMetrics())

    monkeypatch.setattr(gui, "CameraSession", start)
    app.start()
    assert calls == [(camera, None, 0.65, device, (1920, 1080))]
    assert errors == []
    assert all(
        widget.options["state"] == "disabled"
        for widget in (app.camera_select, app.refresh_button, app.start_button)
    )
    assert "USB 카메라 · Webcam 연결 중" in app.status.get()
    app.start()
    assert len(calls) == 1
    app.session = None
    app.camera.set("disconnected")
    app.start()
    assert len(calls) == 1 and len(errors) == 1
    assert "연결된 카메라를 선택" in errors[0][1]
    app.camera.set("USB 카메라 · Webcam")
    app.closing = True
    app.start()
    assert len(calls) == 1 and len(errors) == 1


@pytest.mark.parametrize("alive", [False, True])
def test_close_stops_workers_before_destroying_window(camera_controls, alive):
    app, destroyed, callbacks = camera_controls, [], []
    session = SimpleNamespace(stop=threading.Event(), alive=lambda: alive, finish=lambda: None)
    app.session = session
    app.root = SimpleNamespace(
        destroy=lambda: destroyed.append(True),
        after=lambda delay, callback: callbacks.append((delay, callback)),
    )
    app.close()
    assert app.closing and session.stop.is_set()
    if alive:
        assert callbacks == [(50, app.close)] and not destroyed
        session.alive = lambda: False
        callbacks[0][1]()
    else:
        assert not callbacks
    assert destroyed == [True]


def test_camera_menu_disables_start_on_listing_error(monkeypatch, camera_controls):
    import tools.camera_gui as gui

    def unavailable():
        raise RuntimeError("카메라 목록을 읽지 못했습니다")

    monkeypatch.setattr(gui, "list_cameras", unavailable)
    app = camera_controls
    app.refresh_cameras()
    assert app.cameras == {} and app.camera_select.options["values"] == ()
    assert app.camera.get() == "카메라 목록 조회 실패"
    assert app.start_button.options["state"] == "disabled"
    assert app.status.get() == "카메라 목록을 읽지 못했습니다"


@pytest.mark.parametrize("state", ["renumbered", "removed", "race", "unavailable"])
def test_capture_resolves_selected_id_and_rejects_connection_changes(monkeypatch, state):
    import tools.camera_gui as gui

    selected = {"id": "mac", "index": 0}
    devices = [{"id": "usb", "index": 0}, {"id": "mac", "index": 1}]
    opens, released, reads, listings = [], [], [], []

    def listing():
        listings.append(True)
        if state == "removed" or (state == "race" and len(listings) > 1):
            return devices[:1]
        return devices

    def read():
        reads.append(True)
        return False, None

    camera = SimpleNamespace(
        isOpened=lambda: state != "unavailable",
        set=lambda *args: None,
        read=read,
        release=lambda: released.append(True),
    )

    def open_camera(index, backend):
        opens.append((index, backend))
        return camera

    monkeypatch.setattr(gui.sys, "platform", "darwin")
    monkeypatch.setattr(gui, "list_cameras", listing)
    monkeypatch.setitem(
        sys.modules,
        "cv2",
        SimpleNamespace(
            CAP_AVFOUNDATION=1,
            CAP_ANY=0,
            CAP_PROP_FRAME_WIDTH=3,
            CAP_PROP_FRAME_HEIGHT=4,
            VideoCapture=open_camera,
        ),
    )
    session = CameraSession.__new__(CameraSession)
    session.collector = None
    session.collection_lock = threading.Lock()
    session.metrics = SessionMetrics()
    session.camera, session.resolution = selected, (1920, 1080)
    session.stop, session.errors = threading.Event(), queue.Queue()
    session.capture()
    assert session.stop.is_set()
    error = session.errors.get_nowait()
    assert opens == ([] if state == "removed" else [(1, 1)])
    assert released == ([] if state == "removed" else [True])
    assert len(reads) == (1 if state == "renumbered" else 0)
    assert (
        "영상이 끊겼습니다"
        if state == "renumbered"
        else "연결되어 있지 않습니다"
        if state == "removed"
        else "목록이 바뀌었습니다"
        if state == "race"
        else "열 수 없습니다"
    ) in error


@pytest.fixture
def manual_appearance(monkeypatch):
    import tools.camera_gui as gui

    monkeypatch.setattr(gui, "color_signature", lambda image: "signature")
    monkeypatch.setattr(gui, "color_mask", lambda *args: SimpleNamespace(any=lambda: True))
    monkeypatch.setattr(gui, "verify_appearance", lambda frame, detection, signature: detection)
    monkeypatch.setattr(gui, "match_features", lambda *args: None)


def test_latest_frame_and_selection_bounds():
    channel = queue.Queue(maxsize=1)
    assert put_latest(channel, "old") is False
    assert put_latest(channel, "new") is True
    assert channel.get_nowait() == "new"
    assert selection_box((80, 70), (-5, -6), 100, 100) == (0, 0, 80, 70)
    assert selection_box((0, 0), (500, 500), 100, 100) == (0, 0, 100, 100)
    assert selection_box((0, 0), (4, 4), 100, 100) is None


def test_single_target_does_not_jump_on_loss():
    original = (10, 10, 30, 30, 0.8)
    nearby = (12, 12, 32, 32, 0.7)
    other = (70, 70, 90, 90, 0.99)
    assert choose_target([original, other], None) == other
    assert choose_target([nearby, other], original[:4]) == nearby
    assert choose_target([other], original[:4]) is None
    assert choose_target([], None) is None
    assert overlap((0, 0, 0, 0), (0, 0, 0, 0)) == 0


@pytest.mark.parametrize("score,found", [(0.8, True), (0.2, False), (float("nan"), False)])
def test_template_threshold(monkeypatch, score, found):
    cv = SimpleNamespace(
        INTER_AREA=3,
        INTER_LINEAR=1,
        resize=lambda source, size, interpolation: SimpleNamespace(
            shape=(size[1], size[0]), std=lambda: 20
        ),
        TM_CCOEFF_NORMED=5,
        matchTemplate=lambda *args: None,
        minMaxLoc=lambda scores: (0, score, (0, 0), (4, 6)),
    )
    monkeypatch.setitem(sys.modules, "cv2", cv)
    frame = SimpleNamespace(shape=(100, 100))
    template = SimpleNamespace(shape=(12, 20), std=lambda: 20)
    result = match_template(frame, template, 0.65)
    assert (result is not None) == found
    if found:
        assert result == (4, 6, 24, 18, 0.8)
    assert match_template(SimpleNamespace(shape=(5, 5)), template, 0.65) is None


def test_camera_failure_releases_device(monkeypatch):
    released = []
    camera = SimpleNamespace(
        isOpened=lambda: True,
        set=lambda *args: None,
        read=lambda: (False, None),
        release=lambda: released.append(True),
    )
    cv = SimpleNamespace(
        CAP_AVFOUNDATION=1,
        CAP_ANY=0,
        CAP_PROP_FRAME_WIDTH=3,
        CAP_PROP_FRAME_HEIGHT=4,
        VideoCapture=lambda *args: camera,
    )
    monkeypatch.setitem(sys.modules, "cv2", cv)
    session = CameraSession.__new__(CameraSession)
    session.collector = None
    session.collection_lock = threading.Lock()
    session.metrics = SessionMetrics()
    session.camera = 0
    session.resolution = (1920, 1080)
    session.stop = threading.Event()
    session.errors = queue.Queue()
    session.frames = queue.Queue(maxsize=1)
    session.capture()
    assert session.stop.is_set()
    assert "영상이 끊겼습니다" in session.errors.get_nowait()
    assert released == [True]


def test_scale_sizes_keep_aspect_and_fit_small_targets():
    sizes = template_sizes((80, 100), (480, 640))
    assert sizes[0] == (100, 80)
    assert (10, 8) in sizes
    assert (200, 160) in sizes
    assert len(sizes) == len(set(sizes))
    assert all(8 <= w <= 640 and 8 <= h <= 480 for w, h in sizes)
    assert all(abs(w / 100 - h / 80) <= 1 / 80 for w, h in sizes)
    assert template_sizes((80, 100), (7, 640)) == []
    assert template_sizes((80, 100), (40, 50))  # Original too large, smaller copy fits.


def test_scaled_match_uses_best_size_and_rejects_flat_template(monkeypatch):
    cv = SimpleNamespace(
        INTER_AREA=3,
        INTER_LINEAR=1,
        TM_CCOEFF_NORMED=5,
        resize=lambda source, size, interpolation: SimpleNamespace(
            shape=(size[1], size[0]), std=source.std
        ),
        matchTemplate=lambda frame, sample, method: sample.shape,
        minMaxLoc=lambda shape: (0, 0.95 if shape == (8, 10) else 0.2, (0, 0), (30, 40)),
    )
    monkeypatch.setitem(sys.modules, "cv2", cv)
    frame = SimpleNamespace(shape=(480, 640))
    template = SimpleNamespace(shape=(80, 100), std=lambda: 20)
    assert match_template(frame, template, 0.65) == (30, 40, 40, 48, 0.95)
    template.std = lambda: 0
    assert match_template(frame, template, 0.65) is None


def test_scale_refinement_can_recover_a_match_below_coarse_threshold(monkeypatch):
    cv = SimpleNamespace(
        INTER_AREA=3,
        INTER_LINEAR=1,
        TM_CCOEFF_NORMED=5,
        resize=lambda source, size, interpolation: SimpleNamespace(
            shape=(size[1], size[0]), std=lambda: 20
        ),
        matchTemplate=lambda frame, sample, method: sample.shape,
        minMaxLoc=lambda shape: (
            0,
            0.95 if shape == (40, 50) else 0.5 if shape == (41, 51) else 0.2,
            (0, 0),
            (30, 40),
        ),
    )
    monkeypatch.setitem(sys.modules, "cv2", cv)
    assert match_template(
        SimpleNamespace(shape=(480, 640)),
        SimpleNamespace(shape=(80, 100), std=lambda: 20),
        0.65,
    ) == (30, 40, 80, 80, 0.95)


def test_exact_template_match_skips_expensive_scale_search(monkeypatch):
    calls = []
    cv = SimpleNamespace(
        INTER_AREA=3,
        INTER_LINEAR=1,
        TM_CCOEFF_NORMED=5,
        matchTemplate=lambda frame, sample, method: calls.append(sample.shape),
        minMaxLoc=lambda scores: (0, 0.96, (0, 0), (30, 40)),
    )
    monkeypatch.setitem(sys.modules, "cv2", cv)
    assert match_template(
        SimpleNamespace(shape=(1080, 1920)),
        SimpleNamespace(shape=(500, 300), std=lambda: 20),
        0.65,
    ) == (30, 40, 330, 540, 0.96)
    assert calls == [(500, 300)]


def test_fast_matching_uses_one_native_scale_even_below_early_return_score(monkeypatch):
    calls = []
    monkeypatch.setitem(
        sys.modules,
        "cv2",
        SimpleNamespace(
            TM_CCOEFF_NORMED=5,
            matchTemplate=lambda frame, sample, method: calls.append(sample.shape),
            minMaxLoc=lambda scores: (0, 0.85, (0, 0), (30, 40)),
        ),
    )
    result = match_template(
        SimpleNamespace(shape=(1080, 1920)),
        SimpleNamespace(shape=(80, 100), std=lambda: 20),
        0.8,
        fast=True,
    )
    assert result == (30, 40, 130, 120, 0.85)
    assert calls == [(80, 100)]


@pytest.mark.parametrize("device", ["cpu", "mps"])
def test_manual_model_requires_pinned_hash_and_never_downloads(monkeypatch, tmp_path, device):
    import hashlib

    import tools.camera_gui as gui

    path = tmp_path / "local.onnx"
    monkeypatch.setattr(gui, "MANUAL_MODEL", path)
    assert create_manual_tracker(0.65, device) is None
    path.write_bytes(b"invalid")
    with pytest.raises(ValueError, match="해시"):
        create_manual_tracker(0.65, device)
    monkeypatch.setattr(gui, "MANUAL_MODEL_SHA256", hashlib.sha256(b"invalid").hexdigest())
    params = SimpleNamespace()
    calls = []
    monkeypatch.setitem(
        sys.modules,
        "tools.manual_vit",
        SimpleNamespace(
            MPSVitTracker=lambda p, threshold: calls.append((p, threshold)) or "mps-tracker"
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "cv2",
        SimpleNamespace(
            TrackerVit_Params=lambda: params,
            TrackerVit_create=lambda p: calls.append(p) or "tracker",
        ),
    )
    if device == "mps":
        assert create_manual_tracker(0.65, device) == "mps-tracker"
        assert calls == [(path, 0.65)]
    else:
        assert create_manual_tracker(0.65, device) == "tracker"
        assert params.net == str(path) and params.tracking_score_threshold == 0.65
        assert calls == [params]


@pytest.mark.parametrize(
    "ok,box,score,expected",
    [
        (True, (10, 20, 30, 40), 0.8, (10, 20, 40, 60, 0.8)),
        (False, (10, 20, 30, 40), 0.9, None),
        (True, (10, 20, 30, 40), 0.4, None),
        (True, (10, 20, 30, 40), float("nan"), None),
        (True, (10, 20, -30, 40), 0.8, None),
        (True, (10, 20, 7, 40), 0.8, None),
        (True, (float("nan"), 20, 30, 40), 0.8, None),
        (True, (1900, 20, 30, 40), 0.8, (1900, 20, 1920, 60, 0.8)),
        (True, (1950, 20, 30, 40), 0.8, None),
    ],
)
def test_learned_tracker_requires_a_valid_observation(ok, box, score, expected):
    tracker = SimpleNamespace(update=lambda frame: (ok, box), getTrackingScore=lambda: score)
    assert tracker_detection(tracker, SimpleNamespace(shape=(1080, 1920)), 0.65) == expected


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "turn",
        "few",
        "missing",
        "drift",
        "outliers",
        "cluster",
        "scale",
        "scale_y",
        "reflection",
        "shear",
        "nan",
        "edge",
    ],
)
def test_motion_requires_consistent_distributed_features(monkeypatch, failure):
    class Array(list):
        def reshape(self, *args):
            return self

        def ravel(self):
            return self

        def tolist(self):
            return list(self)

        def __getitem__(self, index):
            return (
                Array(list.__getitem__(self, i) for i in index)
                if isinstance(index, list)
                else super().__getitem__(index)
            )

        def __iadd__(self, offset):
            self[:] = [(x + offset[0], y + offset[1]) for x, y in self]
            return self

    class Image:
        shape = (1080, 1920, 3)

        def __getitem__(self, slices):
            return self

    points = [
        (10, 10),
        (150, 10),
        (10, 150),
        (150, 150),
        (80, 60),
        (60, 80),
        (100, 100),
        (120, 130),
    ]
    if failure == "few":
        points = points[:5]
    if failure == "cluster":
        points = [(20 + i, 20 + i) for i in range(8)]
    calls = []

    def flow(before, frame, features, unused, **kwargs):
        calls.append(features)
        if failure == "missing":
            return None, None, None
        dx = 10 if len(calls) == 1 else -10
        dy = 5 if len(calls) == 1 else (-1 if failure == "drift" else -5)
        return Array((x + dx, y + dy) for x, y in features), Array([1] * len(features)), None

    matrix = [[1, 0, 10], [0, 1, 5]]
    if failure == "turn":
        matrix = [[0.8, 0, 70], [0, 1.1, -25]]
    if failure == "scale":
        matrix = [[1.5, 0, 10], [0, 1.5, 5]]
    if failure == "scale_y":
        matrix[1][1] = 1.5
    if failure == "reflection":
        matrix[1][1] = -1
    if failure == "shear":
        matrix[0][1] = 0.6
    if failure == "nan":
        matrix[0][2] = float("nan")
    if failure == "edge":
        matrix[0][2] = -1000
    cv = SimpleNamespace(
        COLOR_BGR2GRAY=6,
        RANSAC=8,
        cvtColor=lambda image, code: image,
        goodFeaturesToTrack=lambda *args, **kwargs: Array(points),
        calcOpticalFlowPyrLK=flow,
        estimateAffine2D=lambda *args, **kwargs: (
            Array(matrix),
            Array(
                [1] * (4 if failure == "outliers" else len(points))
                + [0] * (4 if failure == "outliers" else 0)
            ),
        ),
    )
    monkeypatch.setitem(sys.modules, "cv2", cv)
    result = track_motion(Image(), Image(), (200, 200, 400, 400), 0.65)
    expected = (210, 205, 410, 405, 1.0) if failure is None else None
    if failure == "turn":
        expected = (230, 195, 390, 415, 1.0)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


@pytest.mark.parametrize("reset", ["clear", "geometry"])
@pytest.mark.parametrize("recovered", [False, True])
def test_manual_worker_motion_recovery_and_reset(monkeypatch, manual_appearance, reset, recovered):
    import time

    import tools.camera_gui as gui

    session = CameraSession.__new__(CameraSession)
    session.collector = None
    session.collection_lock = threading.Lock()
    session.metrics = SessionMetrics()
    session.model_path, session.threshold, session.device = None, 0.65, "cpu"
    session.stop = threading.Event()
    session.errors, session.selections = queue.Queue(), queue.Queue(maxsize=1)
    session.results = queue.Queue(maxsize=1)

    class Frame:
        def __init__(self, shape):
            self.shape = shape

        def __getitem__(self, slices):
            return "patch"

    selected = Frame((1080, 1920, 3))
    box = (800, 200, 1100, 700)
    session.selections.put(("template", box, selected, time.monotonic()))
    frames, motion_calls, searches, records = [], [], [], []

    def get(timeout):
        shape = (480, 640, 3) if reset == "geometry" and len(frames) >= 2 else (1080, 1920, 3)
        frame = Frame(shape)
        frames.append(frame)
        if len(frames) == 3 and reset == "clear":
            session.selections.put(None)
        if len(frames) == 4:
            session.stop.set()
        return time.monotonic(), frame

    def motion(before, current, previous, threshold):
        motion_calls.append((before, current, previous))
        return (*box, 0.9) if len(motion_calls) == 1 else None

    def search(*args, **kwargs):
        searches.append((args, kwargs))
        if recovered and kwargs.get("fast") and args[3] == (0, 0, 1920, 1080):
            return (1500, 800, 1800, 1080, 0.95)
        return None

    monkeypatch.setattr(gui, "track_motion", motion)
    monkeypatch.setattr(gui, "create_manual_tracker", lambda threshold, device: None)
    monkeypatch.setattr(gui, "match_region", search)
    monkeypatch.setattr(gui, "put_latest", lambda channel, record: records.append(record) or False)
    monkeypatch.setitem(
        sys.modules,
        "cv2",
        SimpleNamespace(
            COLOR_BGR2GRAY=6, cvtColor=lambda image, code: SimpleNamespace(std=lambda: 20)
        ),
    )
    session.frames = SimpleNamespace(get=get)
    session.process()
    assert session.errors.empty(), list(session.errors.queue)
    assert motion_calls == [(selected, frames[0], box), (frames[0], frames[1], box)]
    assert len(searches) == (2 if recovered else 3)
    assert searches[0][1] == {"fast": True, "unique": False}
    assert searches[1][1] == {"fast": True, "unique": True}
    assert searches[1][0][2:4] == (0.8, (0, 0, 1920, 1080))
    if not recovered:
        assert searches[2][0][3] == target_crop(box, (1080, 1920))
    assert records[0][2] == (*box, 0.9)
    assert records[0][5] == "특징점"
    if recovered:
        assert records[1][2] == (1500, 800, 1800, 1080, 0.95)
        assert records[1][4:] == ((0, 0, 1920, 1080), "빠른 재검색")
    else:
        assert records[1][2] is None
    assert all(record[2] is None for record in records[2:])


@pytest.mark.parametrize("reset", ["clear", "geometry"])
@pytest.mark.parametrize("device", ["cpu", "mps"])
def test_manual_worker_uses_vit_and_clears_its_state(monkeypatch, manual_appearance, reset, device):
    import time

    import tools.camera_gui as gui

    initializations, observations, frames, records = [], [], [], []

    class Frame:
        def __init__(self, shape):
            self.shape = shape

        def __getitem__(self, slices):
            return SimpleNamespace(std=lambda: 20)

    tracker = SimpleNamespace(init=lambda frame, box: initializations.append((frame, box)))
    session = CameraSession.__new__(CameraSession)
    session.collector = None
    session.collection_lock = threading.Lock()
    session.metrics = SessionMetrics()
    session.model_path, session.threshold, session.device = None, 0.65, device
    session.stop = threading.Event()
    session.errors, session.selections = queue.Queue(), queue.Queue(maxsize=1)
    session.results = queue.Queue(maxsize=1)
    selected = Frame((1080, 1920, 3))
    session.selections.put(("template", (800, 200, 1100, 700), selected, time.monotonic()))

    def get(timeout):
        shape = (480, 640, 3) if reset == "geometry" and frames else (1080, 1920, 3)
        frame = Frame(shape)
        frames.append(frame)
        if len(frames) == 2:
            if reset == "clear":
                session.selections.put(None)
            session.stop.set()
        return time.monotonic(), frame

    def detection(current_tracker, frame, threshold):
        observations.append((current_tracker, frame, threshold))
        return (900, 200, 1200, 700, 0.9)

    def create_tracker(threshold, selected_device):
        assert (threshold, selected_device) == (0.65, device)
        return tracker

    monkeypatch.setattr(gui, "create_manual_tracker", create_tracker)
    monkeypatch.setattr(gui, "track_motion", lambda *args: None)
    monkeypatch.setattr(gui, "tracker_detection", detection)
    monkeypatch.setattr(gui, "put_latest", lambda channel, record: records.append(record) or False)
    monkeypatch.setitem(
        sys.modules, "cv2", SimpleNamespace(COLOR_BGR2GRAY=6, cvtColor=lambda image, code: image)
    )
    session.frames = SimpleNamespace(get=get)
    session.process()
    assert session.errors.empty(), list(session.errors.queue)
    assert session.manual_engine == f"특징점 CPU + ViT {device.upper()}"
    assert initializations == [(selected, (800, 200, 300, 500))]
    assert observations == [(tracker, frames[0], 0.65)]
    assert records[0][5] == "ViT"
    assert records[0][2] == (900, 200, 1200, 700, 0.9)
    assert records[1][2] is None


@pytest.mark.parametrize("return_pose", ["initial", "recent", "features"])
@pytest.mark.parametrize("reset", ["clear", "geometry"])
@pytest.mark.parametrize("confirmation", ["flow", "template"])
def test_manual_worker_reacquires_after_leaving_frame(
    monkeypatch, manual_appearance, return_pose, reset, confirmation
):
    import tools.camera_gui as gui

    clock = SimpleNamespace(now=10.0)
    initial, recent = (SimpleNamespace(std=lambda: 20) for _ in range(2))

    class Frame:
        def __init__(self, name, shape=(1080, 1920, 3)):
            self.name, self.shape = name, shape

        def __getitem__(self, slices):
            return recent

    frames = [
        Frame(name) for name in ("pose", "absent", "absent", "return", "confirm", "move", "reset")
    ]
    if reset == "geometry":
        frames[-1].shape = (480, 640, 3)
    selected = Frame("selected")
    original_box, returned_box = (100, 100, 200, 180), (1700, 900, 1800, 980)
    records, searches, motions, initializations, feature_searches = [], [], [], [], []
    tracker = SimpleNamespace(init=lambda frame, box: initializations.append((frame, box)))
    session = CameraSession.__new__(CameraSession)
    session.collector = None
    session.collection_lock = threading.Lock()
    session.metrics = SessionMetrics()
    session.model_path, session.threshold, session.device = None, 0.65, "cpu"
    session.stop = threading.Event()
    session.errors, session.selections = queue.Queue(), queue.Queue(maxsize=1)
    session.results = queue.Queue(maxsize=1)
    session.selections.put((initial, original_box, selected, clock.now))
    remaining = iter(zip((10.1, 10.2, 13.0, 13.1, 13.2, 13.3, 13.4), frames, strict=True))

    def get(timeout):
        clock.now, frame = next(remaining)
        if frame.name == "reset":
            if reset == "clear":
                session.selections.put(None)
            session.stop.set()
        return clock.now, frame

    def motion(before, frame, box, threshold):
        motions.append((before, frame))
        if frame.name == "pose":
            return (*original_box, 0.9)
        if frame.name == "move" or (frame.name == "confirm" and confirmation == "flow"):
            return (*returned_box, 0.9)
        return None

    def search(frame, sample, threshold, region, **kwargs):
        searches.append((frame, sample, threshold, region, kwargs))
        if frame.name == "confirm" and confirmation == "template" and kwargs.get("fast"):
            return (*returned_box, 0.95)
        wanted = initial if return_pose == "initial" else recent
        if (
            return_pose != "features"
            and frame.name == "return"
            and sample is wanted
            and kwargs.get("fast")
        ):
            return (*returned_box, 0.95)
        return None

    def features(frame, sample, *args):
        feature_searches.append((frame, sample))
        return (
            (*returned_box, 0.95)
            if return_pose == "features" and frame.name == "return" and sample is initial
            else None
        )

    monkeypatch.setattr(gui, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(gui, "create_manual_tracker", lambda threshold, device: tracker)
    monkeypatch.setattr(gui, "track_motion", motion)
    monkeypatch.setattr(gui, "tracker_detection", lambda *args: None)
    monkeypatch.setattr(gui, "match_region", search)
    monkeypatch.setattr(gui, "match_features", features)
    monkeypatch.setattr(gui, "put_latest", lambda channel, record: records.append(record) or False)
    monkeypatch.setitem(
        sys.modules, "cv2", SimpleNamespace(COLOR_BGR2GRAY=6, cvtColor=lambda image, code: image)
    )
    session.frames = SimpleNamespace(get=get)
    session.process()
    assert session.errors.empty(), list(session.errors.queue)
    assert records[1][2] is records[2][2] is records[6][2] is None
    assert records[1][5] == records[2][5] == "재검색"
    assert records[3][2] is None and records[3][5] == "재획득 확인 중"
    assert records[4][2] == (*returned_box, 0.9 if confirmation == "flow" else 0.95)
    assert records[4][5] == "재획득"
    assert records[5][5] == "특징점"
    assert motions == [
        (selected, frames[0]),
        (frames[0], frames[1]),
        (frames[3], frames[4]),
        (frames[4], frames[5]),
    ]
    assert [frame for frame, box in initializations] == [selected, frames[0], frames[4], frames[5]]
    returning = [call for call in searches if call[0] is frames[3]]
    assert all(
        call[2:] == (0.8, (0, 0, 1920, 1080), {"fast": True, "unique": True}) for call in returning
    )
    assert not any(call[0] is frames[6] for call in searches)
    if return_pose == "features":
        assert [sample for frame, sample in feature_searches if frame is frames[3]] == [initial]


@pytest.mark.parametrize("invalid", ["edge", "flat", "stale"])
def test_manual_worker_does_not_remember_invalid_appearance(
    monkeypatch, manual_appearance, invalid
):
    import tools.camera_gui as gui

    clock = SimpleNamespace(now=10.0)
    initial = SimpleNamespace(std=lambda: 20)
    patch = SimpleNamespace(std=lambda: 4 if invalid == "flat" else 20)

    class Frame:
        shape = (1080, 1920, 3)

        def __getitem__(self, slices):
            return patch

    frames = [Frame() for _ in range(4)]
    box = (0 if invalid == "edge" else 100, 100, 200, 180)
    samples, records = [], []
    session = CameraSession.__new__(CameraSession)
    session.collector = None
    session.collection_lock = threading.Lock()
    session.metrics = SessionMetrics()
    session.model_path, session.threshold, session.device = None, 0.65, "cpu"
    session.stop = threading.Event()
    session.errors, session.selections = queue.Queue(), queue.Queue(maxsize=1)
    session.results = queue.Queue(maxsize=1)
    session.selections.put((initial, box, Frame(), clock.now))
    remaining = iter(zip((10.1, 13.0, 13.1, 13.2), frames, strict=True))

    def get(timeout):
        clock.now, frame = next(remaining)
        if frame is frames[-1]:
            session.stop.set()
        return clock.now, frame

    def motion(before, frame, previous, threshold):
        if frame is frames[-1]:
            return (1700, 900, 1800, 980, 0.9)
        if invalid == "stale" and frame is frames[0]:
            clock.now += 0.6
        return (*box, 0.9)

    def search(frame, sample, threshold, region, **kwargs):
        samples.append(sample)
        if frame is frames[2] and sample is initial and kwargs.get("fast"):
            return (1700, 900, 1800, 980, 0.95)
        return None

    monkeypatch.setattr(gui, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(gui, "create_manual_tracker", lambda threshold, device: None)
    monkeypatch.setattr(gui, "track_motion", motion)
    monkeypatch.setattr(gui, "match_region", search)
    monkeypatch.setattr(gui, "put_latest", lambda channel, record: records.append(record) or False)
    monkeypatch.setitem(
        sys.modules, "cv2", SimpleNamespace(COLOR_BGR2GRAY=6, cvtColor=lambda image, code: image)
    )
    session.frames = SimpleNamespace(get=get)
    session.process()
    assert session.errors.empty(), list(session.errors.queue)
    assert all(sample is initial for sample in samples)
    assert records[1][2] is None
    assert records[2][2] is None and records[2][5] == "재획득 확인 중"
    assert records[3][2] == (1700, 900, 1800, 980, 0.9)
    assert records[3][5] == "재획득"
    if invalid == "stale":
        assert records[0][2] is None


@pytest.mark.parametrize("runner_up,found", [(0.7, True), (0.9, False), (float("nan"), False)])
def test_recovery_rejects_ambiguous_template_peaks(monkeypatch, runner_up, found):
    masked = []

    class Scores:
        def __setitem__(self, region, value):
            masked.append((region, value))

    peaks = iter([(0, 0.95, (0, 0), (4, 6)), (0, runner_up, (0, 0), (60, 60))])
    monkeypatch.setitem(
        sys.modules,
        "cv2",
        SimpleNamespace(
            TM_CCOEFF_NORMED=5,
            matchTemplate=lambda *args: Scores(),
            minMaxLoc=lambda scores: next(peaks),
        ),
    )
    result = match_template(
        SimpleNamespace(shape=(100, 100)),
        SimpleNamespace(shape=(12, 20), std=lambda: 20),
        0.8,
        fast=True,
        unique=True,
    )
    assert (result is not None) == found
    assert masked == [((slice(0, 13), slice(0, 15)), -1)]


@pytest.mark.parametrize(
    "distance,accepted",
    [(0.2, True), (0.45, True), (0.46, False), (-1, False), (float("nan"), False)],
)
def test_color_anchor_rejects_background_and_invalid_distance(monkeypatch, distance, accepted):
    import tools.camera_gui as gui

    class Frame:
        def __getitem__(self, slices):
            assert slices == (slice(20, 80), slice(10, 50))
            return "patch"

    monkeypatch.setattr(
        gui, "color_signature", lambda image: SimpleNamespace(reshape=lambda *args: image)
    )

    def compare(anchor, current, metric):
        assert (anchor, current, metric) == ("anchor", "patch", 3)
        return distance

    monkeypatch.setitem(
        sys.modules, "cv2", SimpleNamespace(HISTCMP_BHATTACHARYYA=3, compareHist=compare)
    )
    detection = (10, 20, 50, 80, 0.95)
    anchor = SimpleNamespace(reshape=lambda *args: "anchor")
    assert verify_appearance(Frame(), detection, anchor) == (detection if accepted else None)
    assert verify_appearance(Frame(), None, "anchor") is None


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "turn",
        "few",
        "descriptors",
        "ambiguous",
        "duplicate",
        "outliers",
        "cluster",
        "scale",
        "scale_y",
        "reflection",
        "shear",
        "nan",
        "outside",
    ],
)
def test_global_features_require_unique_distributed_geometry(monkeypatch, failure):
    class Array(list):
        def tolist(self):
            return list(self)

        def ravel(self):
            return self

    points = [(20, 20), (80, 20), (20, 80), (80, 80), (40, 50), (50, 40), (60, 60), (70, 70)]
    if failure == "cluster":
        points = [(20 + i, 20 + i) for i in range(8)]
    keys = [SimpleNamespace(pt=p) for p in points]
    calls = []

    def detect(image, mask):
        calls.append(mask)
        return keys, None if failure == "descriptors" else "descriptors"

    pairs = [
        [
            SimpleNamespace(
                queryIdx=i,
                trainIdx=0 if failure == "duplicate" else i,
                distance=19 if failure == "ambiguous" else 10,
            ),
            SimpleNamespace(distance=20),
        ]
        for i in range(8)
    ]
    if failure == "few":
        pairs = pairs[:7]
    matrix = [[0, -1, 300], [1, 0, 200]]
    if failure == "turn":
        matrix = [[0.5, 0, 300], [0, 1.2, 200]]
    if failure == "scale":
        matrix = [[3, 0, 300], [0, 3, 200]]
    if failure == "scale_y":
        matrix = [[1, 0, 300], [0, 3, 200]]
    if failure == "reflection":
        matrix = [[1, 0, 300], [0, -1, 200]]
    if failure == "shear":
        matrix = [[1, 0.6, 300], [0, 1, 200]]
    if failure == "nan":
        matrix[0][2] = float("nan")
    if failure == "outside":
        matrix[0][2] = 2000
    cv = SimpleNamespace(
        COLOR_BGR2GRAY=6,
        COLOR_BGR2HSV=40,
        NORM_HAMMING=6,
        THRESH_BINARY=0,
        RANSAC=8,
        ORB_create=lambda **kwargs: SimpleNamespace(detectAndCompute=detect),
        cvtColor=lambda image, code: image,
        calcBackProject=lambda *args: "projection",
        threshold=lambda *args: (1, "color mask"),
        BFMatcher=lambda norm: SimpleNamespace(knnMatch=lambda *args, **kwargs: pairs),
        KeyPoint_convert=lambda keys: Array(k.pt for k in keys),
        estimateAffine2D=lambda *args, **kwargs: (
            Array(matrix),
            Array([1] * 7 + [0] if failure == "outliers" else [1] * 8),
        ),
    )
    monkeypatch.setitem(sys.modules, "cv2", cv)
    result = match_features(
        SimpleNamespace(shape=(600, 1000)), SimpleNamespace(shape=(100, 100)), 0.65, "color mask"
    )
    expected = (200, 200, 300, 300, 1.0) if failure is None else None
    if failure == "turn":
        expected = (300, 200, 350, 320, 1.0)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)
    if failure != "descriptors":
        assert calls == [None, "color mask"]
    assert match_features(None, SimpleNamespace(shape=(20, 20)), 0.65, "color mask") is None


@pytest.mark.parametrize("gap", [0.1, 0.6])
def test_manual_worker_rejects_background_and_unconfirmed_or_expired_candidate(
    monkeypatch, manual_appearance, gap
):
    import tools.camera_gui as gui

    clock = SimpleNamespace(now=10.0)
    patch = SimpleNamespace(std=lambda: 20)

    class Frame:
        shape = (1080, 1920, 3)

        def __init__(self, name):
            self.name = name

        def __getitem__(self, slices):
            return patch

    frames = [
        Frame(name) for name in ("background", "spike", "background", "return", "confirm", "clear")
    ]
    selected = Frame("selected")
    records, initializations, anchors = [], [], []
    tracker = SimpleNamespace(init=lambda frame, box: initializations.append(frame))
    background, target = (100, 100, 200, 180, 0.99), (1700, 900, 1800, 980, 0.95)
    session = CameraSession.__new__(CameraSession)
    session.collector = None
    session.collection_lock = threading.Lock()
    session.metrics = SessionMetrics()
    session.model_path, session.threshold, session.device = None, 0.65, "cpu"
    session.stop = threading.Event()
    session.errors, session.selections = queue.Queue(), queue.Queue(maxsize=1)
    session.results = queue.Queue(maxsize=1)
    session.selections.put((patch, background[:4], selected, clock.now))
    remaining = iter(zip((10.1, 10.2, 10.3, 13.0, 13.0 + gap, 13.1 + gap), frames, strict=True))

    def get(timeout):
        clock.now, frame = next(remaining)
        if frame.name == "clear":
            session.selections.put(None)
            session.stop.set()
        return clock.now, frame

    def search(frame, sample, threshold, region, **kwargs):
        assert sample is patch
        return target if frame.name in ("spike", "return", "confirm") else background

    def verify(frame, detection, signature):
        anchors.append(signature)
        return detection if detection is not None and detection[0] > 500 else None

    monkeypatch.setattr(gui, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(gui, "create_manual_tracker", lambda threshold, device: tracker)
    monkeypatch.setattr(
        gui,
        "track_motion",
        lambda before, frame, *args: target if frame.name == "confirm" else background,
    )
    monkeypatch.setattr(gui, "tracker_detection", lambda *args: background)
    monkeypatch.setattr(gui, "verify_appearance", verify)
    monkeypatch.setattr(gui, "match_region", search)
    monkeypatch.setattr(gui, "put_latest", lambda channel, record: records.append(record) or False)
    monkeypatch.setitem(
        sys.modules, "cv2", SimpleNamespace(COLOR_BGR2GRAY=6, cvtColor=lambda image, code: image)
    )
    session.frames = SimpleNamespace(get=get)
    session.process()
    assert session.errors.empty(), list(session.errors.queue)
    assert all(record[2] is None for record in records[:4])
    assert records[1][5] == records[3][5] == "재획득 확인 중"
    assert records[0][5] == records[2][5] == "재검색"
    assert all(anchor == "signature" for anchor in anchors)
    assert initializations == ([selected, frames[4]] if gap < MAX_AGE_S else [selected])
    assert records[4][2] == (target if gap < MAX_AGE_S else None)
    assert records[5][2] is None


@pytest.mark.parametrize("value,present", [(0, False), (128, True)])
def test_search_mask_handles_achromatic_histograms(monkeypatch, value, present):
    class Signature:
        def sum(self, axis):
            return SimpleNamespace(shape=(12, 4) if axis == 2 else (4,))

    def project(images, channels, histogram, ranges, scale):
        assert len(histogram.shape) == len(channels) <= 2
        return 255 if channels == [0, 1] or images[0] == 128 else 0

    monkeypatch.setitem(
        sys.modules,
        "cv2",
        SimpleNamespace(
            COLOR_BGR2HSV=40,
            THRESH_BINARY=0,
            cvtColor=lambda image, code: image,
            calcBackProject=project,
            threshold=lambda image, *args: (1, image > 1),
            bitwise_and=lambda hs, value: hs and value,
        ),
    )
    assert color_mask(value, Signature()) is present


def test_manual_worker_skips_search_when_target_colors_are_absent(monkeypatch, manual_appearance):
    import tools.camera_gui as gui

    class Frame:
        shape = (1080, 1920, 3)

        def __getitem__(self, slices):
            return "patch"

    session = CameraSession.__new__(CameraSession)
    session.collector = None
    session.collection_lock = threading.Lock()
    session.metrics = SessionMetrics()
    session.model_path, session.threshold, session.device = None, 0.65, "cpu"
    session.stop = threading.Event()
    session.errors, session.selections = queue.Queue(), queue.Queue(maxsize=1)
    session.results = queue.Queue(maxsize=1)
    session.selections.put(("template", (100, 100, 200, 180), Frame(), 10.0))

    def get(timeout):
        session.stop.set()
        return 10.1, Frame()

    def unexpected(*args, **kwargs):
        raise AssertionError("background-only frame must not run broad appearance search")

    monkeypatch.setattr(gui, "time", SimpleNamespace(monotonic=lambda: 10.1))
    monkeypatch.setattr(gui, "create_manual_tracker", lambda threshold, device: None)
    monkeypatch.setattr(gui, "track_motion", lambda *args: None)
    monkeypatch.setattr(gui, "color_mask", lambda *args: SimpleNamespace(any=lambda: False))
    monkeypatch.setattr(gui, "match_region", unexpected)
    monkeypatch.setattr(gui, "match_features", unexpected)
    session.frames = SimpleNamespace(get=get)
    session.process()
    assert session.errors.empty(), list(session.errors.queue)
    record = session.results.get_nowait()
    assert record[2] is None and record[5] == "재검색"


def test_camera_publishes_preview_without_waiting_for_worker(monkeypatch):
    session = CameraSession.__new__(CameraSession)
    session.collector = None
    session.collection_lock = threading.Lock()
    session.metrics = SessionMetrics()
    session.camera = 0
    session.resolution = (1920, 1080)
    session.stop = threading.Event()
    session.errors = queue.Queue()
    session.frames = queue.Queue(maxsize=1)
    session.previews = queue.Queue(maxsize=1)
    session.results = queue.Queue(maxsize=1)
    readings = iter([SimpleNamespace(name=f"frame-{i}", shape=(480, 640, 3)) for i in (1, 2, 3)])

    def read():
        frame = next(readings)
        if frame.name == "frame-3":
            session.stop.set()
        return True, frame

    cv = SimpleNamespace(
        CAP_AVFOUNDATION=1,
        CAP_ANY=0,
        CAP_PROP_FRAME_WIDTH=3,
        CAP_PROP_FRAME_HEIGHT=4,
        VideoCapture=lambda *args: SimpleNamespace(
            isOpened=lambda: True, set=lambda *args: None, read=read, release=lambda: None
        ),
    )
    monkeypatch.setitem(sys.modules, "cv2", cv)
    session.capture()  # No processing worker has even started.
    assert session.results.empty()
    assert session.previews.get_nowait()[1].name == "frame-3"
    assert session.frames.get_nowait()[1].name == "frame-3"
    assert session.errors.empty()
    summary = session.metrics.snapshot()
    assert summary["counts"]["capture"] == 3
    assert summary["counts"]["tracking_replaced"] == 2
    assert summary["counts"]["preview_replaced"] == 2
    assert summary["counts"].get("process", 0) == 0


def test_worker_records_stale_skips_and_completed_missing_observations(monkeypatch):
    import tools.camera_gui as gui

    now = [0.0]
    monkeypatch.setattr(gui.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(gui, "create_manual_tracker", lambda *args: None)
    session = CameraSession.__new__(CameraSession)
    session.collector = None
    session.collection_lock = threading.Lock()
    session.metrics = SessionMetrics()
    session.model_path, session.device, session.threshold = None, "cpu", 0.65
    session.stop = threading.Event()
    session.results, session.selections, session.errors = (
        queue.Queue(),
        queue.Queue(),
        queue.Queue(),
    )
    frame = SimpleNamespace(shape=(100, 100, 3))
    pending = [(0.1, frame), (1.0, frame)]
    for at, image in pending:
        session.metrics.capture(at, image.shape)
    now[0] = 1.0

    def get(timeout):
        record = pending.pop(0)
        if not pending:
            session.stop.set()
        return record

    session.frames = SimpleNamespace(get=get)
    session.process()
    assert session.errors.empty(), list(session.errors.queue)
    assert session.metrics.snapshot()["counts"]["stale_input"] == 1
    assert session.metrics.snapshot()["counts"]["process"] == 1
    rows = session.metrics.report()["frames"]
    assert not rows[0]["processed"] and rows[0]["box"] is None
    assert rows[1]["processed"] and rows[1]["box"] is None


def test_preview_expires_old_or_mismatched_observations():
    old_result = (10.0, (480, 640), (20, 20, 40, 40, 0.9), 0.18)
    assert observation_for_preview(old_result, 10.1, (480, 640), 10.2) == old_result
    assert observation_for_preview(old_result, 10.1, (480, 640), 10.6) is None
    assert observation_for_preview(old_result, 10.1, (720, 1280), 10.2) is None
    assert observation_for_preview(old_result, 9.9, (480, 640), 10.2) is None
    assert observation_for_preview(None, 10.1, (480, 640), 10.2) is None


def test_gui_rejects_late_results_after_clear_and_accepts_fresh_results(
    monkeypatch, camera_controls
):
    import tools.camera_gui as gui

    app = camera_controls
    app.result, app.selection_time, app.drag_start = None, 0.0, None
    app.root = SimpleNamespace(after=lambda *args: None)
    session = SimpleNamespace(
        model_path=None,
        collector=None,
        metrics=SessionMetrics(),
        errors=queue.Queue(),
        results=queue.Queue(maxsize=1),
        previews=queue.Queue(maxsize=1),
        selections=queue.Queue(maxsize=1),
        stop=threading.Event(),
    )
    app.session = session
    monkeypatch.setattr(gui.time, "monotonic", lambda: 10.0)
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "PIL", SimpleNamespace(Image=None, ImageTk=None))
    app.clear()
    assert session.selections.get_nowait() is None
    monkeypatch.setattr(gui.time, "monotonic", lambda: 10.2)
    old_result = (9.9, (480, 640), (20, 20, 40, 40, 0.9), 0.02)
    session.results.put_nowait(old_result)
    app.poll()
    assert app.result is None
    fresh_result = (10.1, *old_result[1:])
    session.results.put_nowait(fresh_result)
    app.poll()
    assert app.result == fresh_result


def test_gui_saves_metadata_and_evaluates_complete_truth(monkeypatch, camera_controls, tmp_path):
    import tools.camera_gui as gui

    now = [0.0]
    monkeypatch.setattr(gui.time, "monotonic", lambda: now[0])
    app = camera_controls
    app.metrics = SessionMetrics()
    app.metrics.capture(0.1, (100, 100))
    app.metrics.processed(0.1, 0.11, 0.12, (10, 10, 30, 30, 0.9), "ViT")
    now[0] = 0.2
    app.metrics.finish()
    record_path, truth_path = tmp_path / "measurement.json", tmp_path / "truth.json"
    truth = {
        "schema_version": 1,
        "session_id": app.metrics.session_id,
        "frames": [{"frame_id": 1, "box": [10, 10, 30, 30]}],
    }
    truth_path.write_text(json.dumps(truth))
    errors, summaries = [], []
    dialogs = SimpleNamespace(
        asksaveasfilename=lambda **kwargs: str(record_path),
        askopenfilename=lambda **kwargs: str(truth_path),
    )
    messages = SimpleNamespace(
        showerror=lambda *a: errors.append(a), showinfo=lambda *a: summaries.append(a)
    )
    monkeypatch.setitem(
        sys.modules, "tkinter", SimpleNamespace(filedialog=dialogs, messagebox=messages)
    )
    app.save_metrics()
    record = json.loads(record_path.read_text())
    assert record["status"] == "completed" and record["frames"][0]["box"] == [10, 10, 30, 30]
    assert "frame" not in record["frames"][0]  # No image arrays are serialized.
    app.evaluate_metrics()

    assert not errors and "100.0%" in summaries[0][1]
    truth["frames"] = []
    truth_path.write_text(json.dumps(truth))
    app.evaluate_metrics()
    assert len(errors) == 1 and "모든 frame_id" in errors[0][1]
    dialogs.asksaveasfilename = lambda **kwargs: str(tmp_path)  # Unwritable as a file.
    app.save_metrics()
    assert len(errors) == 2 and errors[-1][0] == "측정 저장 실패"
    dialogs.asksaveasfilename = lambda **kwargs: ""
    app.save_metrics()
    assert len(errors) == 2
    app.session = object()
    dialogs.askopenfilename = lambda **kwargs: pytest.fail("Cannot evaluate a running session")
    app.evaluate_metrics()


def test_opt_in_collection_dialog_and_finish_on_exit(monkeypatch, camera_controls, tmp_path):
    import tools.camera_gui as gui

    app, dialogs, errors, finishes = camera_controls, [], [], []
    session = CameraSession.__new__(CameraSession)
    session.stop, session.collector = threading.Event(), None
    session.collection_lock = threading.Lock()
    session.metrics = SessionMetrics()
    session.metrics.capture(session.metrics.started + 0.01, (100, 100))
    session.alive = lambda: False
    app.session = session

    def collector(path, metrics):
        path.mkdir()
        return SimpleNamespace(root=path, error=None)

    monkeypatch.setattr(gui, "FrameCollector", collector)
    monkeypatch.setitem(
        sys.modules,
        "tkinter",
        SimpleNamespace(
            filedialog=SimpleNamespace(askdirectory=lambda **kw: dialogs.pop()),
            messagebox=SimpleNamespace(showerror=lambda *a: errors.append(a)),
        ),
    )
    dialogs.append("")
    app.collect_frames()
    assert session.collector is None
    dialogs.append(str(tmp_path))
    app.collect_frames()
    assert app.collect_button.options["state"] == "disabled" and session.collector
    dialogs.append(str(tmp_path))
    app.collect_frames()
    assert len(errors) == 1  # The second start cannot replace existing data.
    app.root = SimpleNamespace(destroy=lambda: finishes.append(True))
    app.close()
    path = session.collector.root / "measurement.json"
    record = json.loads(path.read_text())
    assert record["status"] == "completed" and record["session_id"] == session.metrics.session_id
    assert record["settings"]["collection_path"] == str(session.collector.root)
    assert session.stop.is_set() and finishes == [True]
    original = path.read_bytes()
    session.finish()
    assert path.read_bytes() == original
    app.collect_frames()  # A stopped session cannot reopen the dialog.


@pytest.mark.parametrize("box", [(0, 0, 20, 20), (950, 530, 970, 550), (1890, 1050, 1920, 1080)])
def test_native_crop_stays_in_frame_and_contains_target(box):
    x1, y1, x2, y2 = target_crop(box, (1080, 1920))
    assert 0 <= x1 <= box[0] < box[2] <= x2 <= 1920
    assert 0 <= y1 <= box[1] < box[3] <= y2 <= 1080
    assert (x2 - x1, y2 - y1) == (640, 640)
    assert target_crop((10, 10, 30, 30), (100, 120)) == (0, 0, 120, 100)


def test_search_tiles_cover_camera_edges_and_overlap():
    tiles = search_tiles((1080, 1920))
    for x in range(0, 1920, 80):
        for y in range(0, 1080, 80):
            assert any(x1 <= x < x2 and y1 <= y < y2 for x1, y1, x2, y2 in tiles)
    assert any(x2 == 1920 and y2 == 1080 for _, _, x2, y2 in tiles)
    assert all(x2 - x1 == 640 and y2 - y1 == 640 for x1, y1, x2, y2 in tiles)
    assert overlap(tiles[0], tiles[1]) > 0
    assert search_tiles((480, 640)) == [(0, 0, 640, 480)]


def test_detection_coordinates_return_to_original_pixels():
    region = (1000, 300, 1640, 940)
    assert restore_detection((10, 20, 30, 40, 0.8), region, (640, 640)) == (
        1010,
        320,
        1030,
        340,
        0.8,
    )
    assert restore_detection((10, 20, 30, 40, 0.8), region, (320, 320)) == (
        1020,
        340,
        1060,
        380,
        0.8,
    )
    assert restore_detection(None, region, (640, 640)) is None


def test_region_matching_preserves_far_crop_pixels_and_maps_broad_search(monkeypatch):
    import tools.camera_gui as gui

    class Image:
        def __init__(self, shape):
            self.shape = shape

        def __getitem__(self, slices):
            y, x = slices
            return Image((y.stop - y.start, x.stop - x.start, 3))

    resizes, searches = [], []

    def resize(image, size, interpolation):
        resizes.append(size)
        return Image((size[1], size[0]))

    monkeypatch.setitem(
        sys.modules,
        "cv2",
        SimpleNamespace(
            COLOR_BGR2GRAY=6,
            INTER_AREA=3,
            cvtColor=lambda image, code: Image(image.shape[:2]),
            resize=resize,
        ),
    )

    def match(gray, template, threshold):
        searches.append((gray.shape, template.shape))
        return (10, 20, 30, 40, 0.8)

    monkeypatch.setattr(gui, "match_template", match)
    frame, template = Image((1080, 1920, 3)), Image((80, 100))
    assert match_region(frame, template, 0.65, (1000, 300, 1640, 940)) == (
        1010,
        320,
        1030,
        340,
        0.8,
    )
    assert searches == [((640, 640), (80, 100))]
    assert not resizes
    assert match_region(frame, template, 0.65, (0, 0, 1920, 1080)) == (20, 40, 60, 80, 0.8)
    assert searches[-1] == ((540, 960), (40, 50))


@pytest.mark.parametrize("device", ["mps", "cpu"])
def test_yolo_switches_to_native_crop_and_keeps_camera_coordinates(monkeypatch, device):
    import time

    class Frame:
        def __init__(self, shape, offset=(0, 0)):
            self.shape, self.offset = shape, offset

        def __getitem__(self, slices):
            y, x = slices
            return Frame((y.stop - y.start, x.stop - x.start, 3), (x.start, y.start))

    calls = []

    def predict(crop, **kwargs):
        assert kwargs["device"] == device
        calls.append(crop.shape[:2])
        x, y = crop.offset
        coords = [[1600 - x, 800 - y, 1620 - x, 820 - y]]

        def tensor(values):
            return SimpleNamespace(cpu=lambda: SimpleNamespace(tolist=lambda: values))

        return [SimpleNamespace(boxes=SimpleNamespace(xyxy=tensor(coords), conf=tensor([0.9])))]

    monkeypatch.setitem(
        sys.modules,
        "ultralytics",
        SimpleNamespace(
            YOLO=lambda path: SimpleNamespace(task="detect", names={0: "drone"}, predict=predict)
        ),
    )
    session = CameraSession.__new__(CameraSession)
    session.collector = None
    session.collection_lock = threading.Lock()
    session.metrics = SessionMetrics()
    session.model_path, session.threshold, session.device = "trusted-local.pt", 0.25, device
    session.stop = threading.Event()
    session.errors, session.selections = queue.Queue(), queue.Queue(maxsize=1)
    session.results = queue.Queue(maxsize=1)
    count = 0

    def get(timeout):
        nonlocal count
        count += 1
        if count == 2:
            session.stop.set()
        return time.monotonic(), Frame((1080, 1920, 3))

    session.frames = SimpleNamespace(get=get)
    session.process()
    assert session.errors.empty(), list(session.errors.queue)
    assert calls == [(1080, 1920), (640, 640)]
    result = session.results.get_nowait()
    assert result[1] == (1080, 1920)
    assert result[2] == (1600, 800, 1620, 820, 0.9)
    assert result[4] == target_crop(result[2], (1080, 1920))
