"""GUI actions use fakes and never open a Tk window or real port."""

import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.camera_gui import CameraGUI
from tools.vision_motor_panel import AUTO_BOTH, AUTO_TILT, MANUAL_TILT, MotorPanel


class Control:
    def __init__(self, value=None):
        self.value, self.options = value, {}

    def get(self):
        return self.value

    def set(self, value):
        self.value = value

    def configure(self, **options):
        self.options.update(options)


@pytest.fixture
def panel(monkeypatch):
    errors = []
    monkeypatch.setitem(
        sys.modules,
        "tkinter",
        SimpleNamespace(messagebox=SimpleNamespace(showerror=lambda *args: errors.append(args))),
    )
    p = MotorPanel.__new__(MotorPanel)
    for name, value in {
        "port": "fake",
        "tilt_min": "80",
        "tilt_max": "330",
        "tilt_rate": "5",
        "direction": "틱 증가 → 위",
        "pan_image_reversed": False,
        "mode": AUTO_BOTH,
        "power_off": False,
        "ready_to_move": False,
        "status": "미연결",
    }.items():
        setattr(p, name, Control(value))
    for name in (
        "connect_button",
        "refresh_button",
        "off_check",
        "mode_select",
        "pan_image_reverse_check",
        "ready_check",
        "arm_button",
        "jog_button",
        "stop_button",
    ):
        setattr(p, name, Control())
    p.worker, p.get_session = None, lambda: None
    p.is_closing = lambda: False
    p.jog_button.winfo_viewable = lambda: True
    p.settings = [(p.mode_select, "readonly"), (p.pan_image_reverse_check, "normal")]
    p._pressed_keys = set()
    p._release_jobs = {}
    p._selected_mode = AUTO_BOTH
    p._mode_settings = {}
    return p, errors


def test_motor_selectors_keep_input_in_the_window(monkeypatch):
    from serial.tools import list_ports

    ttk = Mock()
    ttk.Combobox.side_effect = AssertionError("native menu can block live controls")
    ttk.Spinbox.side_effect = lambda *_args, **_options: Mock()
    monkeypatch.setitem(
        sys.modules,
        "tkinter",
        SimpleNamespace(StringVar=Control, BooleanVar=Control, ttk=ttk, messagebox=Mock()),
    )
    found = [SimpleNamespace(device="fake-port-1"), SimpleNamespace(device="fake-port-2")]
    monkeypatch.setattr(list_ports, "comports", lambda: found)
    p = MotorPanel(Mock(), lambda: None, lambda: False)
    assert p.pan_image_reversed.get() is False
    assert ttk.Checkbutton.call_args_list[0].kwargs["variable"] is p.pan_image_reversed
    assert [call.kwargs["textvariable"] for call in ttk.Spinbox.call_args_list] == [
        p.port,
        p.direction,
        p.mode,
    ]
    p.mode.set(AUTO_TILT)
    ttk.Spinbox.call_args.kwargs["command"]()
    assert p.tilt_rate.get() == "2"

    p.port.set("entered-port")
    p.refresh()
    p.port_select.configure.assert_called_once_with(values=("fake-port-1", "fake-port-2"))
    found.clear()
    p.refresh()
    p.port_select.configure.assert_called_with(values=("entered-port",))
    assert p.port.get() == "entered-port" and p.worker is None


@pytest.mark.parametrize("mode", [AUTO_BOTH, AUTO_TILT, MANUAL_TILT])
def test_connection_requires_power_off_and_valid_profile_before_worker(panel, monkeypatch, mode):
    p, errors = panel
    started = []

    def create(port, profile):
        def start():
            assert p.mode_select.options["state"] == "disabled"
            assert p.pan_image_reverse_check.options["state"] == "disabled"
            started.append((port, profile))

        return SimpleNamespace(start=start)

    monkeypatch.setattr("tools.vision_motor_panel.MotorWorker", create)
    p.connect()
    assert errors and not started
    errors.clear()
    p.power_off.set(True)
    p.tilt_max.set("331")
    p.connect()
    assert errors and not started
    errors.clear()
    p.tilt_max.set("330")
    p.pan_image_reversed.set(True)
    p.mode.set(mode)
    if mode != AUTO_BOTH:
        p.connect()
        assert errors and not started
        errors.clear()
        p.select_mode()
    p.connect()
    assert not errors and started[0][1].tilt_direction == 1
    assert started[0][1].tilt_only is (mode == AUTO_TILT)
    assert started[0][1].manual_tilt is (mode == MANUAL_TILT)
    assert started[0][1].pan_image_reversed is (mode == AUTO_BOTH)
    if mode == AUTO_TILT:
        profile = started[0][1]
        assert (profile.tilt_min, profile.tilt_max, profile.tilt_rate) == (80, 330, 2)
    assert not p.power_off.get() and not p.ready_to_move.get()


@pytest.mark.parametrize(("mode", "video_path"), [(AUTO_BOTH, None), (AUTO_TILT, "saved.mp4")])
def test_incompatible_input_cannot_open_motor_connection(panel, monkeypatch, mode, video_path):
    p, errors = panel
    p.power_off.set(True)
    p.mode.set(mode)
    p.select_mode()
    p.get_session = lambda: SimpleNamespace(video_path=video_path, model_path=None)
    create = Mock()
    monkeypatch.setattr("tools.vision_motor_panel.MotorWorker", create)

    p.connect()

    create.assert_not_called()
    assert errors and p.worker is None


def test_second_confirmation_is_required_for_arm_and_no_automatic_arm(panel):
    p, errors = panel
    armed = []
    p.worker = SimpleNamespace(
        profile=SimpleNamespace(manual_tilt=False), arm=lambda session: armed.append(session)
    )
    p.arm()
    assert errors and not armed
    p.ready_to_move.set(True)
    p.get_session = lambda: "explicit camera session"
    p.arm()
    assert armed == ["explicit camera session"] and not p.ready_to_move.get()


@pytest.mark.parametrize("state", ["CONNECTING", "READY", "ARMING", "ARMED", "CLOSED"])
@pytest.mark.parametrize("tilt_only", [False, True])
def test_target_change_revokes_live_auto_tilt_connection_and_confirmation(panel, state, tilt_only):
    p, _ = panel
    p.ready_to_move.set(True)
    p.worker = SimpleNamespace(
        state=state,
        profile=SimpleNamespace(tilt_only=tilt_only),
        alive=lambda: state != "CLOSED",
        stop=Mock(),
    )
    p.stop_tilt_tracking()
    cancelled = tilt_only and state != "CLOSED"
    assert p.worker.stop.call_count == int(cancelled)
    assert p.ready_to_move.get() is not cancelled


def test_fault_stays_visible_in_dedicated_status(panel):
    p, _ = panel
    p.worker = SimpleNamespace(
        state="FAULT", error="timeout", ticks=(208, 205), alive=lambda: False
    )
    p.poll()
    assert "오류" in p.status.get() and "timeout" in p.status.get()
    assert p.arm_button.options["state"] == "disabled"


def test_mode_setting_unlocks_after_identical_connection_faults(panel, monkeypatch):
    p, _ = panel
    p.settings = [(p.mode_select, "readonly")]

    def fail_immediately(*_):
        return SimpleNamespace(
            start=lambda: None, state="FAULT", error="port missing", ticks=None, alive=lambda: False
        )

    monkeypatch.setattr("tools.vision_motor_panel.MotorWorker", fail_immediately)
    for _ in range(2):
        p.power_off.set(True)
        p.connect()
        assert p.mode_select.options["state"] == "disabled"
        p.poll()
        assert p.mode_select.options["state"] == "readonly"


def test_keyboard_steps_and_focus_loss(panel):
    p, _ = panel
    directions, stopped = [], []
    p.worker = SimpleNamespace(
        state="ARMED",
        profile=SimpleNamespace(manual_tilt=True),
        jog=directions.append,
        stop=lambda: stopped.append(True),
    )
    callbacks = {}

    def after_idle(callback, key):
        callbacks[key] = callback
        return key

    p.jog_button.after_idle = after_idle
    p.jog_button.after_cancel = callbacks.pop
    up, down = SimpleNamespace(keysym="Up"), SimpleNamespace(keysym="Down")
    p.release_key(up)  # Aqua repeats a key held before the button received focus.
    p.jog_key(up)
    p.jog_key(up)
    assert not directions
    p.release_key(up)
    callbacks.pop("Up")("Up")
    assert p.jog_key(up) == "break"
    p.jog_key(up)
    p.release_key(up)
    p.jog_key(up)
    assert not callbacks
    p.release_key(up)
    callbacks.pop("Up")("Up")
    p.jog_key(up)
    p.jog_key(down)
    assert directions == [1, 1, -1]
    p.release_key(down)
    p.exit_jog(None)
    assert stopped == [True] and not p._pressed_keys and not callbacks
    p.jog_key(up)
    p.worker.state = "FAULT"
    p.exit_jog(None)
    assert not p._pressed_keys


@pytest.mark.parametrize("detect", ["unmap", "key", "poll"])
def test_hidden_keyboard_controls_stop_the_manual_worker(panel, detect):
    p, _ = panel
    stopped = []
    p.worker = SimpleNamespace(
        state="ARMED",
        error=None,
        ticks=(247, 200),
        profile=SimpleNamespace(manual_tilt=True),
        alive=lambda: True,
        jog=lambda _: pytest.fail("hidden controls sent a jog"),
        stop=lambda: stopped.append(True),
    )
    p.jog_button.winfo_viewable = lambda: False
    if detect == "key":
        p.jog_key(SimpleNamespace(keysym="Up"))
    elif detect == "poll":
        p.poll()
    else:
        p.exit_jog(None)
    assert stopped == [True]


def test_tilt_only_and_manual_defaults_are_separate_from_two_axis_settings(panel):
    p, _ = panel
    p.tilt_min.set("115")
    p.tilt_max.set("285")
    p.mode.set(AUTO_TILT)
    p.select_mode()
    assert (p.tilt_min.get(), p.tilt_max.get(), p.tilt_rate.get()) == ("80", "330", "2")
    assert "0번 고정" in p.ready_check.options["text"]
    p.tilt_min.set("100")
    p.mode.set(MANUAL_TILT)
    p.select_mode()
    assert (p.tilt_min.get(), p.tilt_max.get(), p.tilt_rate.get()) == ("200", "210", "2")
    assert "회전 경로" in p.ready_check.options["text"]
    p.tilt_max.set("220")
    p.mode.set(AUTO_TILT)
    p.select_mode()
    assert (p.tilt_min.get(), p.tilt_max.get(), p.tilt_rate.get()) == ("100", "330", "2")
    p.mode.set(AUTO_BOTH)
    p.select_mode()
    assert (p.tilt_min.get(), p.tilt_max.get(), p.tilt_rate.get()) == ("115", "285", "5")
    assert "방향 확인" in p.ready_check.options["text"]


@pytest.mark.parametrize("focused", [False, True])
def test_keyboard_arm_requires_focus_and_can_stop_while_arming(panel, focused):
    p, errors = panel
    actions = []
    p.ready_to_move.set(True)
    p.jog_button.focus_set = lambda: actions.append("focus")
    p.jog_button.focus_get = lambda: p.jog_button if focused else None
    p.worker = SimpleNamespace(
        state="ARMING",
        profile=SimpleNamespace(manual_tilt=True),
        arm=lambda _session: actions.append("arm"),
        stop=lambda: actions.append("stop"),
    )
    p.arm()
    if focused:
        assert not errors and actions == ["focus", "arm"]
        assert p.exit_jog(None) == "break"
        assert actions[-1] == "stop"
    else:
        assert errors and actions == ["focus"]


def test_unchanged_motor_state_reuses_controls_between_video_polls(panel):
    p, _ = panel
    p.worker = SimpleNamespace(
        state="FAULT", error="timeout", ticks=(208, 205), alive=lambda: False
    )
    p.poll()
    p.connect_button.configure = lambda **_: pytest.fail("unchanged controls redrawn")
    p.poll()


def test_camera_stop_requests_motor_shutdown_immediately():
    stopped = []
    gui = CameraGUI.__new__(CameraGUI)
    gui.motor_panel = SimpleNamespace(stop=lambda: stopped.append(True))
    gui.session = None
    gui.stop_button, gui.clear_button, gui.processing_time = Control(), Control(), Control()
    gui.canvas = SimpleNamespace(delete=lambda *_: None)
    gui.set_observation, gui.draw_empty_preview = lambda *_: None, lambda: None
    gui.stop()
    assert stopped == [True] and gui.frame is None


def test_close_waits_for_motor_cleanup_before_destroying_gui():
    events, callbacks = [], []
    gui = CameraGUI.__new__(CameraGUI)
    gui.recording_page = SimpleNamespace(worker=None, stop=lambda: None, poll=lambda: None)
    gui.closing = False
    gui.label_editor = gui.training_window = gui.session = gui.video_thread = None
    gui.video_cancel = threading.Event()
    gui.motor_panel = SimpleNamespace(alive=lambda: True, stop=lambda: events.append("motor stop"))
    gui.stop = lambda: events.append("stop")
    gui.root = SimpleNamespace(
        after=lambda _delay, callback: callbacks.append(callback),
        destroy=lambda: events.append("destroy"),
    )
    gui.close()
    assert events == ["motor stop", "stop"] and callbacks == [gui.finish_close]
    gui.motor_panel.alive = lambda: False
    callbacks[0]()
    assert events == ["motor stop", "stop", "destroy"]


@pytest.mark.parametrize("child", ["label_editor", "training_window"])
def test_close_stops_motor_before_asking_to_save_and_keeps_it_locked_on_cancel(
    panel, monkeypatch, child
):
    p, _ = panel
    gui = CameraGUI.__new__(CameraGUI)
    gui.closing = False
    gui.label_editor = gui.training_window = None
    gui.motor_panel = p
    p.is_closing = lambda: gui.closing
    p.power_off.set(True)
    p.ready_to_move.set(True)
    profile = SimpleNamespace(manual_tilt=False)
    worker = p.worker = SimpleNamespace(
        profile=profile,
        alive=lambda: False,
        stop=Mock(),
        arm=Mock(),
        stopping=threading.Event(),
    )
    worker.stop.side_effect = worker.stopping.set
    create = Mock()
    monkeypatch.setattr("tools.vision_motor_panel.MotorWorker", create)

    def request_close():
        assert gui.closing and worker.stopping.is_set()
        p.connect()
        p.arm()
        return False

    setattr(
        gui,
        child,
        SimpleNamespace(
            window=SimpleNamespace(winfo_exists=lambda: True),
            request_close=request_close,
            closing=False,
        ),
    )
    gui.close()
    assert not gui.closing and worker.stopping.is_set()
    assert p.worker is worker
    create.assert_not_called()
    worker.stop.assert_called_once()
    worker.arm.assert_not_called()


def test_closing_camera_rejects_new_motor_connection_while_video_finishes(panel, monkeypatch):
    p, errors = panel
    gui = CameraGUI.__new__(CameraGUI)
    gui.recording_page = SimpleNamespace(worker=None, stop=lambda: None, poll=lambda: None)
    gui.closing = False
    gui.label_editor = gui.training_window = gui.session = None
    gui.video_thread = SimpleNamespace(is_alive=lambda: True)
    gui.video_cancel = threading.Event()
    gui.motor_panel, gui.stop = p, p.stop
    p.is_closing = lambda: gui.closing
    callbacks, destroyed = [], []
    gui.root = SimpleNamespace(
        after=lambda _delay, callback: callbacks.append(callback),
        destroy=lambda: destroyed.append(True),
    )
    monkeypatch.setattr(
        "tools.vision_motor_panel.MotorWorker", lambda *_: pytest.fail("opened during window close")
    )
    p.poll()
    assert p.connect_button.options["state"] == "normal"
    gui.close()
    p.power_off.set(True)
    p.connect()
    p.arm()
    p.poll()
    assert not errors and p.worker is None and not destroyed
    assert p.connect_button.options["state"] == p.refresh_button.options["state"] == "disabled"
    assert p.arm_button.options["state"] == p.jog_button.options["state"] == "disabled"
    gui.video_thread.is_alive = lambda: False
    callbacks[0]()
    assert destroyed == [True]
