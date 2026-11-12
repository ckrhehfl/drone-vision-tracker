"""GUI actions use fakes and never open a Tk window or real port."""

import sys
import threading
from types import SimpleNamespace

import pytest

from tools.camera_gui import CameraGUI
from tools.vision_motor_panel import MotorPanel


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
        "power_off": False,
        "ready_to_move": False,
        "status": "미연결",
    }.items():
        setattr(p, name, Control(value))
    for name in (
        "connect_button",
        "refresh_button",
        "off_check",
        "ready_check",
        "arm_button",
        "stop_button",
    ):
        setattr(p, name, Control())
    p.worker, p.settings, p.get_session = None, [], lambda: None
    return p, errors


def test_connection_requires_power_off_and_valid_profile_before_worker(panel, monkeypatch):
    p, errors = panel
    started = []

    def create(port, profile):
        started.append((port, profile))
        return SimpleNamespace(start=lambda: None)

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
    p.connect()
    assert not errors and started[0][1].tilt_direction == 1
    assert not p.power_off.get() and not p.ready_to_move.get()


def test_second_confirmation_is_required_for_arm_and_no_automatic_arm(panel):
    p, errors = panel
    armed = []
    p.worker = SimpleNamespace(arm=lambda session: armed.append(session))
    p.arm()
    assert errors and not armed
    p.ready_to_move.set(True)
    p.get_session = lambda: "explicit camera session"
    p.arm()
    assert armed == ["explicit camera session"] and not p.ready_to_move.get()


def test_fault_stays_visible_in_dedicated_status(panel):
    p, _ = panel
    p.worker = SimpleNamespace(
        state="FAULT", error="timeout", ticks=(208, 205), alive=lambda: False
    )
    p.poll()
    assert "오류" in p.status.get() and "timeout" in p.status.get()
    assert p.arm_button.options["state"] == "disabled"


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
    events = []
    gui = CameraGUI.__new__(CameraGUI)
    gui.label_editor = gui.training_window = gui.session = gui.video_thread = None
    gui.video_cancel = threading.Event()
    gui.motor_panel = SimpleNamespace(alive=lambda: True)
    gui.stop = lambda: events.append("stop")
    gui.root = SimpleNamespace(
        after=lambda *args: events.append("wait"), destroy=lambda: events.append("destroy")
    )
    gui.close()
    assert events == ["stop", "wait"]
    gui.motor_panel.alive = lambda: False
    gui.close()
    assert events[-2:] == ["stop", "destroy"]
