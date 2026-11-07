from types import SimpleNamespace

import pytest

from tools.servo0_manual_gui import ManualControl, Servo0Link, angle_to_ticks


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeServo:
    def __init__(self, ticks=205):
        self.ticks = ticks
        self.commands = []
        self.incoming = bytearray(b"BOOT,SERVO0_SAFE\n")
        self.delayed = None
        self.delay_command = None
        self.closed = False
        self.armed = False

    def write(self, data):
        command, token, *args = data.decode("ascii").strip().split(",")
        self.commands.append((command, *args[1:]))
        if command == "STOP":
            return len(data)
        if command == "HELLO":
            self.armed = False
            reply = f"READY,SERVO0_SAFE,{token},OK,{self.ticks}\n"
        else:
            sequence, *values = args
            if command == "INIT":
                self.ticks = angle_to_ticks(int(values[0]))
                state = "INITIALIZED"
            elif command == "ARM":
                self.armed = True
                state = "ARMED"
            elif command == "DISARM":
                self.armed = False
                state = "DISARMED"
            elif command == "STEP":
                state = "STEP"
            elif command == "SET":
                state = "TARGET"
            else:
                state = "HOLD"
            reply = f"ACK,{token},{sequence},{state},{self.ticks}\n"
        if command == self.delay_command:
            self.delayed = reply.encode()
        else:
            self.incoming.extend(reply.encode())
        return len(data)

    def read(self, size):
        data = bytes(self.incoming[:size])
        del self.incoming[:size]
        return data

    def close(self):
        self.closed = True

    def reset(self):
        self.armed = False
        self.incoming.extend(b"BOOT,SERVO0_SAFE\n")


def connected(ticks=205):
    clock = Clock()
    device = FakeServo(ticks)
    link = Servo0Link(device, clock)
    control = ManualControl(link, clock)
    control.poll()
    assert device.commands == []
    clock.advance(2)
    control.poll()
    control.poll()
    assert device.commands == [("HELLO",)]
    assert link.ready and not link.armed
    return clock, device, link, control


def arm(control):
    assert control.arm()
    control.poll()
    assert control.link.armed


def test_init_requires_an_explicit_numeric_command():
    _clock, device, link, control = connected(-1)
    with pytest.raises(ValueError, match="초기 출력"):
        control.arm()
    control.press("a")
    control.poll()
    assert device.commands == [("HELLO",)]
    assert link.initialize(90)
    control.poll()
    assert link.ticks == 205
    assert device.commands[-1] == ("INIT", "90")
    arm(control)


def test_delayed_arm_and_step_ack_cannot_latch_released_key():
    clock, device, link, control = connected()
    device.delay_command = "ARM"
    assert control.arm()
    control.press("a")
    device.incoming.extend(device.delayed)
    control.poll()
    control.release("a")
    assert link.armed and not control.keys
    device.delay_command = "STEP"
    control.press("a")
    control.poll()
    assert device.commands[-1] == ("STEP", "+")
    control.release("a")
    assert device.commands[-1] == ("STOP",)
    device.incoming.extend(device.delayed)
    control.poll()
    for _ in range(8):
        clock.advance(0.1)
        control.poll()
    assert [cmd for cmd in device.commands if cmd[0] == "STEP"] == [("STEP", "+")]
    assert link.ticks == 205


def test_key_repeat_cannot_shorten_interval_and_focus_loss_locks():
    clock, device, link, control = connected()
    arm(control)
    control.press("right")
    control.poll()
    control.poll()
    for _ in range(4):
        clock.advance(0.05)
        control.release("right")
        control.press("right")
        control.poll()
        control.poll()
    assert [cmd for cmd in device.commands if cmd[0] == "STEP"] == [("STEP", "-")]
    clock.advance(0.06)
    control.poll()
    assert len([cmd for cmd in device.commands if cmd[0] == "STEP"]) == 2
    control.stop()
    control.poll()
    control.poll()
    control.poll()
    assert not link.armed
    assert device.commands[-1] == ("DISARM",)


def test_opposite_keys_stop_even_with_a_step_ack_outstanding():
    _clock, device, _link, control = connected()
    arm(control)
    device.delay_command = "STEP"
    control.press("a")
    control.poll()
    assert device.commands[-1] == ("STEP", "+")
    control.press("d")
    assert device.commands[-1] == ("STOP",)
    device.incoming.extend(device.delayed)
    control.poll()
    assert device.commands[-1] == ("HOLD",)
    control.poll()
    assert [cmd for cmd in device.commands if cmd[0] == "STEP"] == [("STEP", "+")]


def test_uno_restart_clears_permission_and_never_reuses_old_key():
    _clock, device, link, control = connected()
    arm(control)
    control.press("d")
    device.reset()
    control.poll()
    assert not link.ready and not link.armed and device.closed
    assert not control.keys
    control.press("d")
    control.poll()
    assert not any(cmd[0] == "STEP" for cmd in device.commands)


def test_late_ack_is_rejected_before_processing_and_locks():
    clock, device, link, control = connected()
    arm(control)
    device.delay_command = "STEP"
    control.press("a")
    control.poll()
    device.incoming.extend(device.delayed)
    clock.advance(0.31)
    control.poll()
    assert device.closed and not link.ready and not link.armed
    assert "시간 초과" in link.error
    assert not control.keys


def test_bounds_and_numeric_mapping_follow_supplied_sketch():
    assert [angle_to_ticks(angle) for angle in (0, 90, 180)] == [330, 205, 80]
    clock, device, link, control = connected(330)
    arm(control)
    control.press("left")
    control.poll()
    control.poll()
    assert device.commands[-1] == ("STEP", "+")
    assert link.ticks == 330
    control.release("left")
    control.poll()
    control.poll()
    control.move_to(180)
    control.poll()
    control.poll()
    assert device.commands[-1] == ("SET", "180")
    device.ticks = 80
    clock.advance(0.13)
    control.poll()
    control.poll()
    assert link.ticks == 80


def test_unexpected_ack_closes_link():
    _clock, device, link, control = connected()
    arm(control)
    device.delay_command = "STEP"
    control.press("d")
    control.poll()
    device.incoming.extend(f"ACK,{link.session},{link.sequence},STEP,70\n".encode())
    control.poll()
    assert not link.ready and device.closed
    assert "다릅니다" in link.error


class FakeDualServo(FakeServo):
    def __init__(self, sketch="SERVO01_PAIR"):
        super().__init__(-1)
        self.sketch = sketch
        self.ticks_by_channel = [-1, -1]
        self.channel = 0
        self.incoming = bytearray(f"BOOT,{sketch}\n".encode())

    def write(self, data):
        command, token, *args = data.decode("ascii").strip().split(",")
        if command == "STOP":
            self.commands.append((command, self.channel, *args))
            return len(data)
        self.commands.append((command, self.channel, *args[1:]))
        if command == "HELLO":
            reply = f"READY,{self.sketch},{token},OK,-1\n"
        else:
            sequence, *values = args
            if command == "CHANNEL":
                self.channel = int(values[0])
                state = "SELECTED"
            elif command == "INIT":
                value = int(values[0])
                self.ticks_by_channel[self.channel] = (
                    angle_to_ticks(value) if not self.channel else value
                )
                state = "INITIALIZED"
            elif command == "ARM":
                state = "ARMED"
            elif command == "DISARM":
                state = "DISARMED"
            elif command == "STEP":
                state = "STEP"
            elif command == "PAIR":
                state = "PAIR"
            elif command == "SET":
                state = "TARGET"
            elif command == "HOME":
                state = "HOME"
            else:
                state = "HOLD"
            ticks = (
                ":".join(str(tick) for tick in self.ticks_by_channel)
                if command in {"PAIR", "DISARM", "SET", "HOME"}
                else self.ticks_by_channel[self.channel]
            )
            reply = f"ACK,{token},{sequence},{state},{ticks}\n"
        if command == self.delay_command:
            self.delayed = reply.encode()
        else:
            self.incoming.extend(reply.encode())
        return len(data)


def dual_connected(sketch="SERVO01_PAIR"):
    from tools.servo01_manual_gui import Servo01Control, Servo01Link

    clock, device = Clock(), FakeDualServo(sketch)
    link = Servo01Link(device, clock)
    control = Servo01Control(link, clock)
    clock.advance(2)
    control.poll()
    control.poll()
    return clock, device, link, control


@pytest.mark.parametrize(("released", "channel", "remaining"), [("a", "0", "0+"), ("w", "1", "+0")])
def test_release_during_delayed_pair_ack_stops_only_that_axis(released, channel, remaining):
    clock, device, link, control = dual_connected()
    assert link.initialize(90)
    control.poll()
    assert link.select(1)
    control.poll()
    assert link.initialize_tilt(205)
    control.poll()
    assert link.select(0)
    control.poll()
    assert control.arm()
    control.poll()
    device.delay_command = "PAIR"
    control.press("a")
    control.press("w")
    clock.advance(0.13)
    control.poll()
    assert device.commands[-1] == ("PAIR", 0, "++")
    control.release(released)
    assert device.commands[-1] == ("STOP", 0, channel)
    device.incoming.extend(device.delayed)
    device.delay_command = None
    control.poll()
    clock.advance(0.13)
    control.poll()
    assert device.commands[-1] == ("PAIR", 0, remaining)
    assert device.ticks_by_channel == [205, 205]


def test_dual_control_does_not_move_an_uninitialized_axis():
    clock, device, link, control = dual_connected()
    assert link.select(1)
    control.poll()
    for ticks in (79, 331):
        with pytest.raises(ValueError, match="1번 초기 출력"):
            link.initialize_tilt(ticks)
    assert link.select(0)
    control.poll()
    device.commands.clear()
    assert link.initialize(90)
    control.poll()
    assert control.arm()
    control.poll()
    control.press("up")
    for _ in range(4):
        clock.advance(0.1)
        control.poll()
    assert all(item[-1][1] == "0" for item in device.commands if item[0] == "PAIR")
    control.press("a")
    clock.advance(0.13)
    control.poll()
    assert device.commands[-1] == ("PAIR", 0, "+0")


@pytest.mark.parametrize("ticks", [80, 330])
def test_tilt_initialization_accepts_each_test_endpoint(ticks):
    _clock, _device, link, control = dual_connected()
    assert link.select(1)
    control.poll()
    assert link.initialize_tilt(ticks)
    control.poll()
    assert link.ticks == ticks


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        (("a", "d", "w"), "0+"),
        (("w", "s", "a"), "+0"),
        (("left", "right", "up"), "0+"),
        (("down", "up", "right"), "-0"),
    ],
)
def test_opposite_keys_cancel_only_their_axis(keys, expected):
    clock, device, link, control = dual_connected()
    assert link.initialize(90)
    control.poll()
    assert link.select(1)
    control.poll()
    assert link.initialize_tilt(205)
    control.poll()
    assert link.select(0)
    control.poll()
    assert control.arm()
    control.poll()
    for key in keys:
        control.press(key)
    clock.advance(0.13)
    control.poll()
    assert device.commands[-1] == ("PAIR", 0, expected)


@pytest.mark.parametrize(
    ("pan", "tilt", "expected"),
    [("a", "w", "++"), ("a", "s", "+-"), ("d", "w", "-+"), ("d", "s", "--")],
)
def test_two_axis_keys_generate_a_combined_motion_command(pan, tilt, expected):
    clock, device, link, control = dual_connected()
    assert link.initialize(90)
    control.poll()
    assert link.select(1)
    control.poll()
    assert link.initialize_tilt(205)
    control.poll()
    assert control.arm()
    control.poll()
    control.press(pan)
    control.press(tilt)
    clock.advance(0.13)
    control.poll()
    assert device.commands[-1] == ("PAIR", 1, expected)
    control.poll()
    assert link.channel_ticks == [205, 205]


def test_invalid_pair_reply_closes_the_serial_connection():
    clock, device, link, control = dual_connected()
    assert link.initialize(90)
    control.poll()
    assert control.arm()
    control.poll()
    device.delay_command = "PAIR"
    control.press("a")
    clock.advance(0.13)
    control.poll()
    device.incoming.extend(f"ACK,{link.session},{link.sequence},PAIR,331:-1\n".encode())
    control.poll()
    assert not link.ready and device.closed


def test_numeric_pan_target_still_works_when_tilt_was_selected():
    _clock, device, link, control = dual_connected()
    assert link.initialize(90)
    control.poll()
    assert link.select(1)
    control.poll()
    assert link.initialize_tilt(205)
    control.poll()
    assert control.arm()
    control.poll()
    control.move_to(180)
    control.poll()
    assert device.commands[-1] == ("CHANNEL", 1, "0")
    control.poll()
    assert device.commands[-1] == ("SET", 0, "180")


def test_numeric_pan_reply_updates_stopped_tilt_after_delayed_pair():
    clock, device, link, control = dual_connected()
    assert link.initialize(90)
    control.poll()
    assert link.select(1)
    control.poll()
    assert link.initialize_tilt(205)
    control.poll()
    assert control.arm()
    control.poll()
    device.delay_command = "PAIR"
    control.press("a")
    control.press("w")
    clock.advance(0.13)
    control.poll()
    device.ticks_by_channel[:] = [206, 207]
    control.release("w")
    control.move_to(180)
    device.incoming.extend(device.delayed)
    device.delay_command = None
    control.poll()
    assert device.commands[-1] == ("CHANNEL", 1, "0")
    control.poll()
    assert device.commands[-1] == ("SET", 0, "180")
    control.poll()
    assert link.channel_ticks == [206, 207]


def test_focus_loss_stops_both_axes_even_with_pair_ack_pending():
    clock, device, link, control = dual_connected()
    assert link.initialize(90)
    control.poll()
    assert link.select(1)
    control.poll()
    assert link.initialize_tilt(205)
    control.poll()
    assert control.arm()
    control.poll()
    device.delay_command = "PAIR"
    control.press("a")
    control.press("w")
    clock.advance(0.13)
    control.poll()
    control.stop()
    assert device.commands[-1] == ("STOP", 1)
    device.incoming.extend(device.delayed)
    control.poll()
    control.poll()
    assert not link.armed
    assert device.commands[-1][0] == "DISARM"


def test_repeated_conflicting_key_events_do_not_starve_the_other_axis():
    clock, device, link, control = dual_connected()
    assert link.initialize(90)
    control.poll()
    assert link.select(1)
    control.poll()
    assert link.initialize_tilt(205)
    control.poll()
    assert control.arm()
    control.poll()
    control.press("a")
    control.press("w")
    clock.advance(0.13)
    control.poll()
    control.poll()
    control.press("d")
    for _ in range(20):
        clock.advance(0.04)
        control.release("d")
        control.press("d")
        control.poll()
        control.poll()
    pairs = [item[-1] for item in device.commands if item[0] == "PAIR"]
    assert pairs.count("0+") >= 4
    assert link.ready and link.armed


def test_disarm_updates_both_last_commanded_ticks():
    clock, device, link, control = dual_connected()
    assert link.initialize(90)
    control.poll()
    assert link.select(1)
    control.poll()
    assert link.initialize_tilt(205)
    control.poll()
    assert control.arm()
    control.poll()
    control.press("a")
    control.press("w")
    clock.advance(0.13)
    control.poll()
    control.poll()
    device.ticks_by_channel[:] = [206, 207]
    control.stop()
    control.poll()
    control.poll()
    assert not link.armed and link.channel_ticks == [206, 207]


class GuiValue:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


@pytest.mark.parametrize("key", ["w", "s", "Up", "Down"])
def test_uninitialized_tilt_key_explains_how_to_enable_it(key):
    from tools.servo01_manual_gui import Servo01Window

    _clock, device, link, control = dual_connected()
    assert link.initialize(90)
    control.poll()
    assert control.arm()
    control.poll()
    window = Servo01Window.__new__(Servo01Window)
    window.control, window.notice = control, GuiValue()
    device.commands.clear()
    assert window.press(SimpleNamespace(keysym=key, state=0)) == "break"
    assert "1번 미설정" in window.notice.get()
    assert "잠금" in window.notice.get() and "1번 초기 출력" in window.notice.get()
    assert control.keys == {key.lower()}
    assert device.commands[-1] == ("STOP", 0, "1")
    window.release(SimpleNamespace(keysym=key, state=0))
    assert not control.keys


def test_uninitialized_tilt_key_still_stops_pan_numeric_goal():
    from tools.servo01_manual_gui import Servo01Window

    clock, device, link, control = dual_connected()
    assert link.initialize(90)
    control.poll()
    assert control.arm()
    control.poll()
    control.move_to(180)
    control.poll()
    assert device.commands[-1][0] == "SET"
    window = Servo01Window.__new__(Servo01Window)
    window.control, window.notice = control, GuiValue()
    window.press(SimpleNamespace(keysym="up", state=0))
    assert control.goal is None and "1번 미설정" in window.notice.get()
    assert device.commands[-2:] == [("STOP", 0), ("STOP", 0, "1")]
    control.poll()
    clock.advance(0.13)
    control.poll()
    assert device.commands[-1] == ("PAIR", 0, "00")


def test_embedded_control_pad_controls_both_axes_and_releases_them_independently():
    from tools.servo01_manual_gui import Servo01Window

    clock, device, link, control = dual_connected()
    assert link.initialize(90)
    control.poll()
    assert link.select(1)
    control.poll()
    assert link.initialize_tilt(205)
    control.poll()
    assert control.arm()
    control.poll()
    window = Servo01Window.__new__(Servo01Window)
    window.control = control
    pan = SimpleNamespace(keysym="a", state=0)
    tilt = SimpleNamespace(keysym="w", state=0)
    assert window.press(pan) == "break"
    assert window.press(tilt) == "break"
    clock.advance(0.13)
    control.poll()
    assert device.commands[-1] == ("PAIR", 1, "++")
    window.release(pan)
    assert device.commands[-1] == ("STOP", 1, "0")
    control.poll()
    clock.advance(0.13)
    control.poll()
    assert device.commands[-1] == ("PAIR", 1, "0+")
    window.release_all()
    assert device.commands[-1] == ("STOP", 1)


class DummyWindow:
    def after(self, _ms, _callback):
        pass

    def focus_get(self):
        return None


@pytest.mark.parametrize("focus_state", ["unknown_widget", "destroyed_widget", "camera_control"])
def test_embedded_motor_control_stops_when_focus_leaves_panel(focus_state):
    import tkinter as tk

    from tools.servo01_manual_gui import Servo01Window

    _clock, device, link, control = dual_connected()
    assert link.initialize(90)
    control.poll()
    assert control.arm()
    control.poll()
    control.move_to(180)
    control.poll()
    assert device.commands[-1][0] == "SET"

    window = Servo01Window.__new__(Servo01Window)
    window.embedded, window.body = True, object()
    window.control, window.pending_init = control, None
    window.window = SimpleNamespace()
    outside = SimpleNamespace(winfo_toplevel=lambda: window.window, master=window.window)
    window.window.master = None

    def focus_get():
        if focus_state == "unknown_widget":
            raise KeyError("tk popdown listbox")
        if focus_state == "destroyed_widget":
            raise tk.TclError("widget was destroyed")
        return outside

    window.window.focus_get = focus_get
    window.check_focus()
    assert control.goal is None and control.lock_requested
    assert device.commands[-1][0] == "STOP"
    control.poll()
    control.poll()
    assert not link.armed and any(item[0] == "DISARM" for item in device.commands)


def test_embedded_motor_control_keeps_focus_inside_its_panel():
    from tools.servo01_manual_gui import Servo01Window

    window = Servo01Window.__new__(Servo01Window)
    window.embedded, window.body = True, object()
    window.pending_init = None
    stops = []
    window.control = SimpleNamespace(stop=lambda: stops.append(True))
    window.window = SimpleNamespace()
    window.window.focus_get = lambda: SimpleNamespace(
        winfo_toplevel=lambda: window.window, master=window.body
    )
    window.check_focus()
    assert not stops


@pytest.mark.parametrize("channel", [0, 1])
@pytest.mark.parametrize("stop_action", ["stop", "focus"])
def test_pending_initial_output_is_cancelled_by_stop_or_focus_loss(channel, stop_action):
    from tools.servo01_manual_gui import Servo01Window

    _clock, device, link, control = dual_connected()
    window = Servo01Window.__new__(Servo01Window)
    window.link, window.control = link, control
    window.window = DummyWindow()
    window.status = GuiValue()
    window.notice = GuiValue()
    window.power_reset = GuiValue(True)
    window.reported_fault = False
    window.pending_init = (channel, 90 if channel == 0 else 205)
    if channel == 1:
        device.delay_command = "CHANNEL"
        assert link.select(1)
    if stop_action == "stop":  # Esc and the lock button share this handler.
        window.stop()
    else:
        window.check_focus()
    if channel == 1:
        device.incoming.extend(device.delayed)
    window.poll()
    window.poll()
    assert not any(item[0] == "INIT" for item in device.commands)
    assert window.pending_init is None


def test_tilt_initialization_does_not_prefill_an_unverified_position(monkeypatch):
    from tkinter import simpledialog

    from tools.servo01_manual_gui import Servo01Window

    _clock, _device, link, control = dual_connected()
    window = Servo01Window.__new__(Servo01Window)
    window.link, window.control = link, control
    window.window = DummyWindow()
    window.notice = GuiValue()
    window.pending_init = None
    calls = []
    monkeypatch.setattr(simpledialog, "askinteger", lambda *args, **kwargs: calls.append(kwargs))
    window.begin_initialize(1)
    assert len(calls) == 1 and "initialvalue" not in calls[0]
    assert window.pending_init is None


@pytest.mark.parametrize(("channel", "value"), [(0, 90), (1, 205)])
def test_embedded_initialization_keeps_panel_focus_after_dialog(monkeypatch, channel, value):
    from tkinter import simpledialog

    from tools.servo01_manual_gui import Servo01Window

    _clock, device, link, control = dual_connected()
    window = Servo01Window.__new__(Servo01Window)
    window.embedded, window.body, window.closed = True, object(), False
    window.link, window.control, window.pending_init = link, control, None
    window.notice, window.status = GuiValue(), GuiValue()
    window.reported_fault, window.power_reset = False, GuiValue(True)
    window.window = SimpleNamespace(after=lambda *_args: None)
    window.pad = SimpleNamespace(winfo_toplevel=lambda: window.window, master=window.body)
    focus = {"widget": window.window}
    window.pad.focus_set = lambda: focus.update(widget=window.pad)
    window.window.focus_get = lambda: focus["widget"]
    monkeypatch.setattr(simpledialog, "askinteger", lambda *_args, **_kwargs: value)

    window.begin_initialize(channel)
    assert focus["widget"] is window.pad
    window.check_focus()  # A pending focus check must not cancel the chosen output.
    window.poll()
    window.poll()
    assert link.initialized[channel]
    assert ("INIT", channel, str(value)) in device.commands


def test_closing_embedded_servo_panel_keeps_the_camera_window_open():
    from tools.servo01_manual_gui import Servo01Window

    calls = []
    window = Servo01Window.__new__(Servo01Window)
    window.closed = False
    window.embedded = True
    window.pending_init = None
    window.control = SimpleNamespace(stop=lambda: calls.append("stop"))
    window.link = None
    window.poll_id = "timer"
    window.focus_binding = "focus-binding"
    window.escape_binding = "escape-binding"
    window.body = SimpleNamespace(destroy=lambda: calls.append("panel-destroy"))
    window.window = SimpleNamespace(
        after_cancel=lambda timer: calls.append(("cancel", timer)),
        unbind=lambda event, binding: calls.append(("unbind", event, binding)),
        destroy=lambda: pytest.fail("camera window must stay open"),
    )
    window.close()
    assert window.closed and calls[0] == "stop" and calls[-1] == "panel-destroy"
    assert ("cancel", "timer") in calls
    assert ("unbind", "<FocusOut>", "focus-binding") in calls


def test_reconnect_applies_visible_tilt_direction_setting(monkeypatch):
    from tools.servo0_manual_gui import Servo0Window
    from tools.servo01_manual_gui import Servo01Window

    window = Servo01Window.__new__(Servo01Window)
    window.reverse = GuiValue(True)

    def fake_connect(self):
        self.control = SimpleNamespace(reverse_tilt=False)

    monkeypatch.setattr(Servo0Window, "connect", fake_connect)
    window.connect()
    assert window.control.reverse_tilt
    window.control = None  # Reconnection creates another control object.
    window.connect()
    assert window.control.reverse_tilt
    window.reverse.set(False)
    window.connect()
    assert not window.control.reverse_tilt


def auto_home_window(sketch="SERVO01_HOME"):
    from tools.servo01_manual_gui import Servo01Window

    clock, device, link, control = dual_connected(sketch)
    window = Servo01Window.__new__(Servo01Window)
    window.link, window.control = link, control
    window.window, window.pad = DummyWindow(), SimpleNamespace(focus_set=lambda: None)
    window.closed, window.reported_fault = False, False
    window.status, window.notice, window.power_reset = GuiValue(), GuiValue(), GuiValue(True)
    window.auto_home, window.auto_home_saved = GuiValue(True), True
    window.auto_start_pending, window.home_flow, window.pending_init = True, None, None
    return clock, device, link, control, window


def pump_home(clock, window, done, attempts=40):
    for _ in range(attempts):
        window.poll()
        if done():
            return
        clock.advance(0.13)
    pytest.fail("auto-home flow did not reach the expected state")


def test_auto_home_preference_is_opt_in_and_survives_restart(tmp_path):
    from tools.servo01_manual_gui import load_auto_home, save_auto_home

    path = tmp_path / "local" / "servo01-auto-home.json"
    assert not load_auto_home(path)
    save_auto_home(True, path)
    assert load_auto_home(path)
    save_auto_home(False, path)
    assert not load_auto_home(path)
    path.write_text('{"auto_home": 1}', encoding="utf-8")
    assert not load_auto_home(path)
    path.write_text("{", encoding="utf-8")
    assert not load_auto_home(path)


def test_reconnecting_uses_saved_auto_home_setting(monkeypatch, tmp_path):
    from tools.servo0_manual_gui import Servo0Window
    from tools.servo01_manual_gui import Servo01Window, load_auto_home, save_auto_home

    path = tmp_path / "servo01-auto-home.json"
    save_auto_home(True, path)
    _clock, _device, link, control = dual_connected("SERVO01_HOME")
    window = Servo01Window.__new__(Servo01Window)
    window.link, window.control = None, None
    window.reverse, window.auto_home = GuiValue(False), GuiValue(load_auto_home(path))
    window.auto_start_pending = False

    def fake_connect(self):
        self.link, self.control = link, control

    monkeypatch.setattr(Servo0Window, "connect", fake_connect)
    window.connect()
    assert window.auto_start_pending


def test_auto_home_after_connect_initializes_both_axes_and_arms():
    clock, device, link, control, window = auto_home_window()
    focused = []
    window.pad.focus_set = lambda: focused.append(True)
    pump_home(
        clock,
        window,
        lambda: window.home_flow is None and any(cmd[0] == "HOME" for cmd in device.commands),
    )
    assert link.initialized == [True, True] and link.channel_ticks == [205, 205]
    assert link.armed and not control.homing
    assert [(cmd[0], cmd[-1]) for cmd in device.commands if cmd[0] == "INIT"] == [
        ("INIT", "90"),
        ("INIT", "205"),
    ]
    assert "실제 위치는 측정되지 않습니다" in window.notice.get()
    assert not focused


def test_checking_auto_home_returns_existing_outputs_to_the_center(monkeypatch):
    import tools.servo01_manual_gui as gui

    clock, device, link, control, window = auto_home_window()
    window.auto_start_pending = False
    assert link.initialize(90)
    control.poll()
    assert link.select(1)
    control.poll()
    assert link.initialize_tilt(205)
    control.poll()
    assert control.arm()
    control.poll()
    device.ticks_by_channel[:] = [230, 190]
    saved = []
    monkeypatch.setattr(gui, "save_auto_home", lambda value: saved.append(value))
    window.toggle_auto_home()
    assert saved == [True] and device.commands[-1][0] == "STOP"
    pump_home(clock, window, lambda: any(cmd[0] == "HOME" for cmd in device.commands))
    device.ticks_by_channel[:] = [205, 205]
    pump_home(clock, window, lambda: window.home_flow is None)
    assert link.armed and link.channel_ticks == [205, 205]


def test_legacy_sketch_stays_locked_when_auto_home_is_requested():
    clock, device, link, _control, window = auto_home_window("SERVO01_PAIR")
    window.poll()
    assert window.home_flow is None and not link.armed
    assert "최신 servo01_manual_safe" in window.notice.get()
    assert not any(cmd[0] in {"INIT", "ARM", "HOME"} for cmd in device.commands)
    clock.advance(0.13)
    window.poll()
    assert link.ready


def test_auto_home_disabled_does_not_initialize_or_arm():
    clock, device, link, _control, window = auto_home_window()
    window.auto_home.set(False)
    window.auto_start_pending = False
    for _ in range(5):
        clock.advance(0.13)
        window.poll()
    assert link.ready and not link.armed
    assert device.commands == [("HELLO", 0)]


def test_failed_connection_clears_pending_auto_home():
    _clock, device, link, _control, window = auto_home_window()
    link.fail("disconnected")
    window.poll()
    assert device.closed and not window.auto_start_pending and window.home_flow is None
    assert not any(cmd[0] in {"INIT", "ARM", "HOME"} for cmd in device.commands)


def test_stop_during_delayed_initialization_cancels_auto_home():
    clock, device, link, _control, window = auto_home_window()
    device.delay_command = "INIT"
    pump_home(clock, window, lambda: link.pending == "INIT")
    window.stop()
    device.incoming.extend(device.delayed)
    device.delay_command = None
    for _ in range(6):
        clock.advance(0.13)
        window.poll()
    assert window.home_flow is None and not link.armed
    assert [cmd[0] for cmd in device.commands].count("INIT") == 1
    assert not any(cmd[0] in {"ARM", "HOME"} for cmd in device.commands)


@pytest.mark.parametrize("pending", ["INIT", "ARM"])
def test_numeric_target_during_auto_setup_cancels_it_even_with_delayed_ack(pending):
    clock, device, link, _control, window = auto_home_window()
    device.delay_command = pending
    pump_home(clock, window, lambda: link.pending == pending)
    before = len(device.commands)
    window.number = GuiValue("180")
    window.move_to_number()
    assert window.home_flow is None and not window.auto_start_pending
    assert "영점 복귀 중단" in window.notice.get()
    device.incoming.extend(device.delayed)
    device.delay_command = None
    for _ in range(6):
        clock.advance(0.13)
        window.poll()
    assert not link.armed
    assert not any(cmd[0] in {"INIT", "ARM", "HOME", "SET"} for cmd in device.commands[before:])


def test_manual_initialization_and_arm_are_blocked_during_auto_home(monkeypatch):
    from tkinter import simpledialog

    clock, device, link, _control, window = auto_home_window()
    device.delay_command = "INIT"
    pump_home(clock, window, lambda: link.pending == "INIT")
    monkeypatch.setattr(
        simpledialog, "askinteger", lambda *_args, **_kwargs: pytest.fail("unexpected dialog")
    )
    window.initialize_tilt()
    window.arm()
    assert "영점 복귀" in window.notice.get()
    assert not any(cmd[0] == "ARM" for cmd in device.commands)


def test_direction_key_during_delayed_auto_arm_cannot_start_movement():
    clock, device, link, control, window = auto_home_window()
    device.delay_command = "ARM"
    pump_home(clock, window, lambda: link.pending == "ARM")
    window.press(SimpleNamespace(keysym="a", state=0))
    assert window.home_flow is None and control.lock_requested
    device.incoming.extend(device.delayed)
    device.delay_command = None
    pump_home(clock, window, lambda: not link.armed and not link.pending)
    assert not any(cmd[0] in {"HOME", "PAIR"} for cmd in device.commands)
    assert not control.keys


def test_direction_key_interrupts_home_and_returns_to_manual_control():
    clock, device, link, control, window = auto_home_window()
    device.delay_command = "HOME"
    pump_home(clock, window, lambda: link.pending == "HOME")
    assert control.homing and link.armed
    window.press(SimpleNamespace(keysym="a", state=0))
    assert not control.homing and window.home_flow is None
    assert device.commands[-1][0] == "STOP" and "a" in control.keys
    device.incoming.extend(device.delayed)
    device.delay_command = None
    pump_home(
        clock, window, lambda: any(cmd[0] == "PAIR" and cmd[-1] == "+0" for cmd in device.commands)
    )
    assert [cmd[0] for cmd in device.commands].count("HOME") == 1


def test_numeric_target_interrupts_home_without_false_completion():
    clock, device, link, control, window = auto_home_window()
    device.delay_command = "HOME"
    pump_home(clock, window, lambda: link.pending == "HOME")
    window.number = GuiValue("180")
    focused = []
    window.pad.focus_set = lambda: focused.append(True)
    window.move_to_number()
    assert control.goal == 180 and not control.homing
    window.poll()
    assert window.home_flow is None and not focused
    assert "목표 명령" in window.notice.get()
    device.incoming.extend(device.delayed)
    device.delay_command = None
    pump_home(clock, window, lambda: any(cmd[0] == "SET" for cmd in device.commands))


def test_invalid_home_ack_closes_link_and_aborts_auto_home():
    clock, device, link, _control, window = auto_home_window()
    device.delay_command = "HOME"
    pump_home(clock, window, lambda: link.pending == "HOME")
    device.incoming.extend(f"ACK,{link.session},{link.sequence},HOME,331:205\n".encode())
    window.poll()
    assert device.closed and not link.ready and window.home_flow is None
    assert "PWM" in link.error


def test_version_change_on_uno_restart_closes_the_link():
    _clock, device, link, control = dual_connected("SERVO01_HOME")
    device.incoming.extend(b"BOOT,SERVO01_PAIR\n")
    control.poll()
    assert device.closed and not link.ready and "재시작" in link.error


def test_boot_and_ready_versions_must_match():
    from tools.servo01_manual_gui import Servo01Link

    clock, device = Clock(), FakeDualServo("SERVO01_HOME")
    link = Servo01Link(device, clock)
    link.poll()
    clock.advance(2)
    link.poll()
    device.incoming = bytearray(f"READY,SERVO01_PAIR,{link.session},OK,-1\n".encode())
    link.poll()
    assert device.closed and not link.ready and "응답이 다릅니다" in link.error


def test_unchecking_during_pending_home_stops_and_does_not_resume(monkeypatch):
    import tools.servo01_manual_gui as gui

    clock, device, link, _control, window = auto_home_window()
    device.delay_command = "HOME"
    pump_home(clock, window, lambda: link.pending == "HOME")
    monkeypatch.setattr(gui, "save_auto_home", lambda _value: None)
    window.auto_home.set(False)
    window.toggle_auto_home()
    assert device.commands[-1][0] == "STOP"
    device.incoming.extend(device.delayed)
    device.delay_command = None
    pump_home(clock, window, lambda: not link.armed and not link.pending)
    sent = sum(cmd[0] == "HOME" for cmd in device.commands)
    for _ in range(4):
        clock.advance(0.13)
        window.poll()
    assert sum(cmd[0] == "HOME" for cmd in device.commands) == sent
    assert window.home_flow is None and not window.auto_start_pending


def test_preference_save_failure_stops_motion_and_preserves_previous_setting(monkeypatch):
    import tools.servo01_manual_gui as gui

    clock, device, link, _control, window = auto_home_window()
    device.delay_command = "HOME"
    pump_home(clock, window, lambda: link.pending == "HOME")

    def fail(_value):
        raise OSError("read only")

    monkeypatch.setattr(gui, "save_auto_home", fail)
    window.auto_home.set(False)
    window.toggle_auto_home()
    assert device.commands[-1][0] == "STOP" and window.auto_home.get()
    assert window.home_flow is None and "저장 실패" in window.notice.get()
