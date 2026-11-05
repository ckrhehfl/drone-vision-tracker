import time
from collections import deque
from types import SimpleNamespace

import pytest

from tools.pan_keyboard import PanControlWindow, PanLink, default_port, number_to_ticks


class FakeUno:
    def __init__(self):
        self.commands = []
        self.response = deque()
        self.closed = False
        self.bad_move = False
        self.ticks = 250

    def reset_input_buffer(self):
        self.response.clear()

    def write(self, data):
        cmd, token, *args = data.decode().strip().split(",")
        self.commands.append((cmd, *args[1:]))
        states = {
            "ARM": "ARMED",
            "MOVE": "MOVING",
            "HOLD": "HOLD",
            "DISARM": "DISARMED",
            "GOTO": "TARGET",
        }
        if cmd == "HELLO":
            reply = f"READY,PANKEY2,{token},OK,{self.ticks}\n"
        elif cmd == "MOVE" and self.bad_move:
            reply = f"ERR,{token},{args[0]},NOT_ARMED,250\n"
        else:
            reply = f"ACK,{token},{args[0]},{states[cmd]},{self.ticks}\n"
        self.response.extend(bytes([b]) for b in reply.encode())
        return len(data)

    def read(self, _size):
        return self.response.popleft() if self.response else b""

    def close(self):
        self.closed = True


def until(predicate):
    deadline = time.monotonic() + 1
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert predicate()


def make_link(monkeypatch, uno):
    monkeypatch.setattr(PanLink, "STARTUP_S", 0)
    link = PanLink("fake", serial_factory=lambda *_a, **_kw: uno)
    link.start()
    until(lambda: link.snapshot()[3])
    return link


def test_real_host_protocol_arms_only_on_request_and_holds_on_release(monkeypatch):
    uno = FakeUno()
    link = make_link(monkeypatch, uno)
    try:
        link.input(1)
        assert uno.commands == [("HELLO",)]
        link.operator("ARM", "245:255")
        until(lambda: link.snapshot()[2])
        link.input(1)
        until(lambda: ("MOVE", "1") in uno.commands)
        link.input(0)
        until(lambda: uno.commands[-1] == ("HOLD",))
        assert link.snapshot()[1] == 250
        until(lambda: not link.snapshot()[2])  # Tk lease expires if the GUI hangs.
        assert uno.commands[-1] == ("DISARM",)
    finally:
        link.close()
        link.thread.join(1)
    assert uno.closed


def test_rejected_move_disconnects_and_does_not_retry(monkeypatch):
    uno = FakeUno()
    uno.bad_move = True
    link = make_link(monkeypatch, uno)
    try:
        link.operator("ARM", "245:255")
        until(lambda: link.snapshot()[2])
        link.input(-1)
        until(lambda: not link.snapshot()[3])
        assert uno.commands.count(("MOVE", "-1")) == 1
        assert uno.commands[-1] == ("DISARM",)
    finally:
        link.close()
        link.thread.join(1)


def test_number_uses_the_existing_command_scale():
    assert [number_to_ticks(n) for n in ("0", "180", "360")] == [205, 307, 410]
    for invalid in ("", "-1", "361", "1.2", "１２", "999999999999"):
        with pytest.raises(ValueError):
            number_to_ticks(invalid)


def test_default_port_selects_only_one_usbmodem(monkeypatch):
    from serial.tools import list_ports

    monkeypatch.setattr(
        list_ports, "comports", lambda: [SimpleNamespace(device="/dev/cu.usbmodem11401")]
    )
    assert default_port() == "/dev/cu.usbmodem11401"
    monkeypatch.setattr(list_ports, "comports", lambda: [])
    assert default_port() == ""
    monkeypatch.setattr(
        list_ports,
        "comports",
        lambda: [SimpleNamespace(device=f"/dev/cu.usbmodem{i}") for i in (1, 2)],
    )
    assert default_port() == ""


def test_target_is_bounded_then_returns_to_hold_at_destination(monkeypatch):
    uno = FakeUno()
    link = make_link(monkeypatch, uno)
    try:
        with pytest.raises(ValueError, match="구동 허가"):
            link.move_to(252)
        link.operator("ARM", "245:255")
        until(lambda: link.snapshot()[2])
        with pytest.raises(ValueError, match="245~255"):
            link.move_to(256)
        link.move_to(252)
        until(lambda: ("GOTO", "252") in uno.commands)
        uno.ticks = 252

        def holding_at_target():
            link.heartbeat()
            return uno.commands[-1] == ("HOLD",) and link.snapshot()[1] == 252

        until(holding_at_target)
        assert ("GOTO", "256") not in uno.commands
    finally:
        link.close()
        link.thread.join(1)


def test_focus_within_window_allows_number_entry_but_external_focus_locks():
    panel = PanControlWindow.__new__(PanControlWindow)
    calls = []
    window = SimpleNamespace()
    focus = SimpleNamespace(winfo_toplevel=lambda: window)
    window.focus_get = lambda: focus
    panel.window = window
    panel.link = SimpleNamespace(snapshot=lambda: ("", 250, True, True))
    panel.disarm = lambda: calls.append("DISARM")
    panel.check_focus()
    assert not calls
    window.focus_get = lambda: None
    panel.check_focus()
    assert calls == ["DISARM"]


def test_number_submission_restores_keyboard_focus_and_invalid_input_holds():
    panel = PanControlWindow.__new__(PanControlWindow)
    commands = []
    panel.link = SimpleNamespace(
        move_to=lambda ticks: commands.append(("GOTO", ticks)),
        input=lambda direction: commands.append(("HOLD", direction)),
    )
    panel.number = SimpleNamespace(get=lambda: "8")
    panel.number_status = SimpleNamespace(set=lambda message: commands.append(("STATUS", message)))
    panel.pad = SimpleNamespace(focus_set=lambda: commands.append(("FOCUS",)))
    panel.keys = {"a"}
    panel.move_to_number()
    assert commands[:2] == [("GOTO", 209), ("FOCUS",)]
    assert not panel.keys
    panel.number.get = lambda: "361"
    panel.move_to_number()
    assert commands[-2][0] == "HOLD"
    assert commands[-1][0] == "STATUS"


def test_keys_and_focus_loss_send_stop_without_opposite_direction():
    panel = PanControlWindow.__new__(PanControlWindow)
    panel.keys = set()
    panel.reverse = SimpleNamespace(get=lambda: False)
    commands = []
    panel.link = SimpleNamespace(
        input=commands.append,
        snapshot=lambda: ("", 250, True, True),
        operator=lambda command, _value=None: commands.append(command),
    )
    right = SimpleNamespace(keysym="Right", state=0)
    left = SimpleNamespace(keysym="a", state=0)
    panel.press(right)
    panel.press(left)
    panel.release(right)
    panel.disarm()
    assert commands == [1, 0, -1, "DISARM"]
    assert not panel.keys
