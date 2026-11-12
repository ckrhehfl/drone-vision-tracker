import sys
from types import SimpleNamespace

import pytest

from tools.pan0_follow import Pan0Profile, Pan0Serial, main, run
from tools.pan_tilt import PanTiltCommand

PROFILE = Pan0Profile(200, 205, 210, 1)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(0.001, seconds)


class Port:
    def __init__(self):
        self.rx = bytearray()
        self.writes = []
        self.drop = False
        self.bad = None
        self.closed = False
        self.output = "NA"
        self.profile = "200,205,210"

    def write(self, data):
        fields = data.decode().strip().split(",")
        self.writes.append(fields)
        command, session, seq = fields[:3]
        if command == "HELLO":
            session = seq
            reply = f"READY,1,{session},DISARMED,PAN0_TEST1\nPROFILE,PAN0_TEST1,{self.profile}"
        else:
            if command == "SET":
                self.output = fields[3]
            state = {"ARM": "ARMED", "SET": "TRACK", "HOLD": "HOLD", "DISARM": "DISARMED"}
            reply = f"ACK,{session},{seq},{state[command]},{self.output},NA"
        if not self.drop:
            self.rx.extend(((self.bad or reply) + "\n").encode())
        return len(data)

    def read(self, size):
        # Partial reads exercise framing, not just complete-line fakes.
        size = min(size, 3)
        data, self.rx = self.rx[:size], self.rx[size:]
        return data

    def reset_input_buffer(self):
        self.rx.clear()

    def close(self):
        self.closed = True


def connected():
    clock, port = Clock(), Port()
    link = Pan0Serial(port, PROFILE, clock=clock, sleep=clock.sleep)
    link.connect()
    return link, port, clock


@pytest.mark.parametrize(
    "values",
    [
        (79, 84, 89, 1),
        (325, 330, 335, 1),
        (200, 200, 210, 1),
        (199, 205, 210, 1),
        (250, 255, 260, 1),
        (200, 205, 210, 0),
        (200, True, 210, 1),
    ],
)
def test_reject_invalid_or_unbounded_installation_profile(values):
    with pytest.raises(ValueError):
        Pan0Profile(*values)


def test_connect_never_arms_or_outputs_and_profile_mismatch_refuses_arm():
    link, port, _ = connected()
    assert [w[0] for w in port.writes] == ["HELLO"]
    assert not link.armed
    with pytest.raises(RuntimeError, match="ARM"):
        link.send(PanTiltCommand(90, 0, "TRACK"))
    port = Port()
    port.profile = "201,205,210"
    clock = Clock()
    link = Pan0Serial(port, PROFILE, clock=clock, sleep=clock.sleep)
    with pytest.raises(RuntimeError, match="범위"):
        link.connect()
    assert [w[0] for w in port.writes] == ["HELLO"]


def test_start_command_hold_cadence_and_explicit_close():
    link, port, clock = connected()
    link.arm()
    assert port.output == "NA"
    link.request("SET", PROFILE.centidegrees(PROFILE.start), 0)
    assert link.send(PanTiltCommand(90, 0)) is None
    clock.sleep(0.05)
    link.send(PanTiltCommand(90, 0))
    assert port.writes[-1][0] == "HOLD"
    link.close()
    assert port.writes[-1][0] == "DISARM"
    assert port.closed and not link.armed


@pytest.mark.parametrize(
    "reply",
    [
        "ACK,DEADBEEF,1,ARMED,NA,NA",
        "ACK,UNKNOWN,0,ARMED,NA,NA",
        "ACK,DEADBEEF,1,ARMED,9000,9000",
        "ERR,DEADBEEF,1,NOT_ARMED",
        "X" * 97,
    ],
)
def test_invalid_ack_never_establishes_armed_state(reply):
    link, port, _ = connected()
    port.bad = reply
    with pytest.raises(RuntimeError):
        link.arm()
    assert not link.armed


def test_timeout_no_retry_no_rearm_and_cleanup_still_attempts_disarm():
    link, port, _ = connected()
    link.arm()
    port.drop = True
    with pytest.raises(TimeoutError):
        link.request("SET", 9000, 0)
    with pytest.raises(TimeoutError):
        link.close()
    assert [w[0] for w in port.writes] == ["HELLO", "ARM", "SET", "DISARM"]
    assert port.closed


def test_fault_blocks_motion_and_late_ack_does_not_hide_disarm():
    link, port, _ = connected()
    link.arm()
    port.drop = True
    with pytest.raises(TimeoutError):
        link.request("SET", 9000, 0)
    assert not link.armed
    before = len(port.writes)
    for command, values in [("ARM", ()), ("SET", (9000, 0)), ("HOLD", ())]:
        with pytest.raises(RuntimeError, match="FAULT"):
            link.request(command, *values)
    assert len(port.writes) == before
    port.rx.extend(f"ACK,{link.session},2,TRACK,9000,NA\n".encode())
    port.drop = False
    link.close()
    assert [w[0] for w in port.writes] == ["HELLO", "ARM", "SET", "DISARM"]
    assert port.closed and not link.armed


def test_unknown_request_is_rejected_before_serial_write():
    link, port, _ = connected()
    with pytest.raises(ValueError):
        link.request("UNSUPPORTED")
    assert [w[0] for w in port.writes] == ["HELLO"]


@pytest.mark.parametrize("bad", ["session", "future", "angle", "state", "sequence"])
def test_disarm_does_not_accept_invalid_or_future_ack(bad):
    link, port, _ = connected()
    link.arm()
    fields = ["ACK", link.session, "1", "ARMED", "NA", "NA"]
    if bad == "session":
        fields[1] = "00000000" if link.session != "00000000" else "FFFFFFFF"
    elif bad == "future":
        fields[2] = "3"
    elif bad == "angle":
        fields[4] = "1000"
    elif bad == "state":
        fields[3] = "BOGUS"
    else:
        fields[2] = "NaN"
    port.bad = ",".join(fields)
    with pytest.raises(RuntimeError):
        link.close()
    assert port.closed


def test_only_old_acks_cannot_extend_disarm_deadline():
    link, port, clock = connected()
    link.arm()
    port.drop = True
    with pytest.raises(TimeoutError):
        link.request("SET", 9000, 0)
    read = port.read

    def old_ack(size):
        if not port.rx:
            port.rx.extend(f"ACK,{link.session},2,TRACK,9000,NA\n".encode())
        return read(size)

    port.read = old_ack
    before = clock()
    with pytest.raises(TimeoutError):
        link.close()
    assert clock() - before < 0.16
    assert port.closed and [w[0] for w in port.writes] == ["HELLO", "ARM", "SET", "DISARM"]


def test_controller_demo_is_bounded_and_stops_before_return():
    clock = Clock()

    class Link:
        def __init__(self):
            self.commands = []

        def arm(self):
            self.commands.append("ARM")

        def request(self, command, *values):
            self.commands.append((command, values))

        def send(self, command):
            assert 86.4 <= command.pan_deg <= 93.6
            self.commands.append(command)

    link = Link()
    run(PROFILE, link=link, duration=6, clock=clock, sleep=clock.sleep)
    assert link.commands[:2] == ["ARM", ("SET", (9000, 0))]
    assert link.commands[-1] == ("DISARM", ())
    angles = [c.pan_deg for c in link.commands if isinstance(c, PanTiltCommand)]
    assert min(angles) < 90 < max(angles)


def test_default_command_never_imports_serial(monkeypatch):
    monkeypatch.setitem(sys.modules, "serial", None)
    monkeypatch.setattr("tools.pan0_follow.run", lambda *a, **kw: None)
    assert main([]) == 0


@pytest.mark.parametrize(
    "args",
    [
        ["--hardware"],
        ["--port", "unused"],
        ["--hardware", "--port", "unused", "--start-ticks", "205"],
        ["--duration", "nan"],
        ["--duration", "61"],
        ["--video", "missing.avi"],
    ],
)
def test_missing_hardware_confirmation_profile_or_video_pair_fails_before_serial(args, monkeypatch):
    monkeypatch.setitem(sys.modules, "serial", None)
    with pytest.raises(SystemExit):
        main(args)


def test_operator_cancel_closes_without_arm(monkeypatch):
    port = Port()
    clock = Clock()
    link = Pan0Serial(port, PROFILE, clock=clock, sleep=clock.sleep)
    monkeypatch.setitem(sys.modules, "serial", SimpleNamespace(Serial=lambda *a, **kw: port))
    monkeypatch.setattr("tools.pan0_follow.Pan0Serial", lambda *a: link)
    monkeypatch.setattr("builtins.input", lambda _: "cancel")
    assert (
        main(
            [
                "--hardware",
                "--port",
                "mock",
                "--min-ticks",
                "200",
                "--start-ticks",
                "205",
                "--max-ticks",
                "210",
                "--direction",
                "1",
            ]
        )
        == 1
    )
    assert port.writes == []


def test_video_uses_host_age_and_acknowledges_each_result(monkeypatch):
    import queue
    import threading

    clock = Clock()
    commands = []

    class Session:
        def __init__(self, *a, **kw):
            self.errors, self.results = queue.Queue(), queue.Queue()
            self.stop, self.display_ack = threading.Event(), threading.Event()
            self.video_displayed = 0
            self.threads = []
            self.metrics = SimpleNamespace(report=lambda: {})
            self.results.put((clock(), (100, 100), (70, 45, 80, 55), 0, None, "YOLO"))
            self.results.put((clock() + 0.05, (100, 100), (70, 45, 80, 55), 0, None, "YOLO"))
            # Media timestamps can be fresh while the real inference result is late.
            self.results.put((clock() - 1, (100, 100), (70, 45, 80, 55), 0, None, "YOLO"))
            sessions.append(self)

        def request_stop(self):
            self.stop.set()

        def finish(self):
            pass

        def alive(self):
            return False

    class Link:
        def arm(self):
            pass

        def request(self, *a):
            pass

        def send(self, command):
            commands.append(command)

    sessions = []
    monkeypatch.setattr("tools.camera_gui.CameraSession", Session)
    run(
        PROFILE,
        link=Link(),
        video="unused",
        model="unused",
        duration=0.4,
        clock=clock,
        sleep=clock.sleep,
    )
    assert [c.state for c in commands[:3]] == ["HOLD", "TRACK", "HOLD"]
    assert all(c.state == "HOLD" for c in commands[2:])
    assert sessions[0].results.empty() and sessions[0].display_ack.is_set()


@pytest.mark.parametrize("failure", ["race", "during_cleanup", "metrics", "none", "duration"])
def test_video_exit_reports_late_errors_after_cleanup(failure, tmp_path, monkeypatch):
    import queue
    import threading

    clock, sessions = Clock(), []
    original_run = run

    class Session:
        def __init__(self, *args, **kwargs):
            self.errors = queue.Queue()
            self.stop, self.display_ack = threading.Event(), threading.Event()
            self.error = None
            self.cleaned = False
            self.results = SimpleNamespace(get_nowait=self.result)
            self.threads = [SimpleNamespace(join=self.join)]
            self.metrics = SimpleNamespace(
                report=lambda: {
                    "status": "failed" if self.error else "completed",
                    "error": self.error,
                }
            )
            sessions.append(self)

        def result(self):
            if failure == "race":
                self.error = "decode failed during result acknowledgement"
                self.errors.put(self.error)
                self.stop.set()
            elif failure != "duration":
                self.stop.set()
            raise queue.Empty

        def join(self, timeout):
            if failure == "during_cleanup":
                self.error = "worker failed during join"
                self.errors.put(self.error)

        def request_stop(self):
            self.stop.set()

        def alive(self):
            return False

        def finish(self):
            self.cleaned = True
            if failure == "metrics":
                self.error = "worker failure in metrics"

    monkeypatch.setattr("tools.camera_gui.CameraSession", Session)
    monkeypatch.setattr(
        "tools.pan0_follow.run",
        lambda *a, **kw: original_run(*a, **kw, clock=clock, sleep=clock.sleep),
    )
    video, model = tmp_path / "video", tmp_path / "model"
    video.touch()
    model.touch()
    result = main(["--video", str(video), "--model", str(model), "--duration", "0.15"])
    assert result == (0 if failure in {"none", "duration"} else 1)
    assert sessions[0].stop.is_set() and sessions[0].cleaned


def test_non_ascii_disarm_still_cleans_video_and_closes_port(tmp_path, monkeypatch):
    import queue
    import threading
    import time

    class BadDisarmPort(Port):
        def write(self, data):
            count = super().write(data)
            if data.startswith(b"DISARM,"):
                self.rx = bytearray(b"\xff\n")
            return count

    session = SimpleNamespace(
        errors=queue.Queue(),
        results=queue.Queue(),
        stop=threading.Event(),
        display_ack=SimpleNamespace(set=lambda: session.stop.set()),
        video_displayed=0,
        threads=[],
        request_stop=lambda: session.stop.set(),
        alive=lambda: False,
        metrics=SimpleNamespace(report=lambda: {"status": "completed"}),
        finish=lambda: cleaned.append(True),
    )
    session.results.put((time.monotonic(), (100, 100), (45, 45, 55, 55)))
    cleaned = []
    clock, port = Clock(), BadDisarmPort()
    link = Pan0Serial(port, PROFILE, clock=clock, sleep=clock.sleep)
    monkeypatch.setattr("tools.camera_gui.CameraSession", lambda *a, **kw: session)
    monkeypatch.setitem(sys.modules, "serial", SimpleNamespace(Serial=lambda *a, **kw: port))
    monkeypatch.setattr("tools.pan0_follow.Pan0Serial", lambda *a: link)
    monkeypatch.setattr("builtins.input", lambda _: "PAN0 200/205/210 1")
    video, model = tmp_path / "video", tmp_path / "model"
    video.touch()
    model.touch()
    assert (
        main(
            [
                "--hardware",
                "--port",
                "mock",
                "--min-ticks",
                "200",
                "--start-ticks",
                "205",
                "--max-ticks",
                "210",
                "--direction",
                "1",
                "--video",
                str(video),
                "--model",
                str(model),
            ]
        )
        == 1
    )
    assert cleaned == [True] and port.closed
    assert [w[0] for w in port.writes] == ["HELLO", "ARM", "SET", "DISARM", "DISARM"]


@pytest.mark.parametrize("case", ["old_capture", "stale_after_arm", "error", "timeout", "stop"])
def test_video_warmup_precedes_arm_and_preserves_first_result(case, monkeypatch):
    import queue
    import threading

    clock, events, commands, acknowledgements, sessions = Clock(), [], [], [], []

    class Session:
        def __init__(self, *args, **kwargs):
            clock.sleep(0.3)
            self.errors, self.stop = queue.Queue(), threading.Event()
            self.results = SimpleNamespace(get_nowait=self.result)
            self.display_ack = SimpleNamespace(
                set=lambda: acknowledgements.append(self.video_displayed)
            )
            self.metrics = SimpleNamespace(report=lambda: {"status": "completed"})
            self.threads, self.delivered, self.cleaned = [], 0, False
            sessions.append(self)

        def result(self):
            if case == "stop":
                self.stop.set()
                raise queue.Empty
            if case == "timeout" or clock() < 0.5:
                if case == "error":
                    self.errors.put("model warmup failed")
                raise queue.Empty
            if self.delivered == 3:
                self.stop.set()
                raise queue.Empty
            self.delivered += 1
            if self.delivered == 1:
                events.append("ready")
                captured = 0 if case == "old_capture" else clock()
                events.append(("first_capture", captured))
            else:
                captured = clock()
            return captured, (100, 100), (70, 45, 80, 55)

        def request_stop(self):
            self.stop.set()

        def alive(self):
            return False

        def finish(self):
            self.cleaned = True

    class Link:
        def arm(self):
            assert "ready" in events
            events.append("ARM")
            clock.sleep(0.2)

        def request(self, command, *args):
            events.append(command)

        def send(self, command):
            commands.append(command)

    monkeypatch.setattr("tools.camera_gui.CameraSession", Session)
    if case == "error":
        with pytest.raises(RuntimeError, match="model warmup failed"):
            run(
                PROFILE,
                link=Link(),
                video="unused",
                model="unused",
                duration=1.2,
                clock=clock,
                sleep=clock.sleep,
            )
    else:
        run(
            PROFILE,
            link=Link(),
            video="unused",
            model="unused",
            duration=1.2,
            clock=clock,
            sleep=clock.sleep,
        )
    if case in {"error", "timeout", "stop"}:
        assert "ARM" not in events and "SET" not in events and not acknowledgements
    else:
        assert events.index("ready") < events.index("ARM") < events.index("SET")
        original = next(item[1] for item in events if isinstance(item, tuple))
        assert acknowledgements[0] == original and len(acknowledgements) == 3
        assert [command.state for command in commands[:3]] == ["HOLD", "HOLD", "TRACK"]
    assert sessions[0].cleaned and sessions[0].stop.is_set()
