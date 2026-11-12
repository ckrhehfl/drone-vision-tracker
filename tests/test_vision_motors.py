"""No real ports: check command mapping, framing, freshness and worker lifecycle."""

import threading
import time
from types import SimpleNamespace

import pytest

from tools.vision_motors import MotorProfile, MotorWorker, TicksLink, can_arm


class Clock:
    now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Port:
    def __init__(self, ticks=(208, 205)):
        self.ticks, self.rx, self.writes = ticks, bytearray(), []
        self.closed, self.drop, self.bad = False, None, None
        self.magic = "VISION_TICKS1"
        self.state = "DISARMED"
        self.diagnostic = "1,32,4,121,0,208,0,205"
        self.diagnostic_error = None

    def reset_input_buffer(self):
        self.rx.clear()

    def write(self, data):
        fields = data.decode().strip().split(",")
        self.writes.append(fields)
        command, token = fields[:2]
        if command == "HELLO":
            token = fields[2]
            response = (
                f"READY,1,{token},{self.state},{self.magic},{self.ticks[0]},{self.ticks[1]}\n"
                f"LIMITS,{self.magic},80,330"
            )
        elif command == "DIAG":
            response = (
                f"ERR,{token},{fields[2]},{self.diagnostic_error}"
                if self.diagnostic_error
                else f"DIAG,{token},{fields[2]},{self.diagnostic}"
            )
        else:
            sequence = fields[2]
            state = {"ARM": "ARMED", "SET_TICKS": "TRACK", "HOLD": "HOLD", "DISARM": "DISARMED"}[
                command
            ]
            response = f"ACK,{token},{sequence},{state},{self.ticks[0]},{self.ticks[1]}"
        if self.drop != command:
            self.rx.extend(((self.bad or response) + "\n").encode())
        return len(data)

    def read(self, count):
        count = min(count, 7)
        data = bytes(self.rx[:count])
        del self.rx[:count]
        return data

    def close(self):
        self.closed = True


def connected(ticks=(208, 205), profile=None):
    clock, port = Clock(), Port(ticks)
    link = TicksLink(port, profile or MotorProfile(), clock=clock, sleep=clock.sleep)
    link.connect()
    return link, port, clock


@pytest.mark.parametrize(
    "kwargs",
    [
        {"tilt_min": 79},
        {"tilt_max": 331},
        {"tilt_min": 330},
        {"tilt_min": True},
        {"tilt_direction": 0},
        {"tilt_direction": True},
        {"tilt_rate": 0},
        {"tilt_rate": 11},
        {"tilt_rate": float("nan")},
        {"tilt_rate": True},
    ],
)
def test_invalid_camera_axis_adjustments_are_rejected(kwargs):
    with pytest.raises(ValueError):
        MotorProfile(**kwargs)


@pytest.mark.parametrize("ticks", [(80, 330), (330, 80), (208, 205)])
def test_retained_position_is_seeded_without_centre_goal(ticks):
    profile = MotorProfile()
    controller = profile.controller(ticks)
    assert profile.command_ticks(controller.snapshot()) == ticks
    link, port, _ = connected(ticks)
    assert port.writes[0][0] == "HELLO" and len(port.writes) == 1 and not link.armed
    link.request("ARM")
    assert [w[0] for w in port.writes] == ["HELLO", "ARM"]


def test_only_camera_axis_adjustment_changes_and_installed_sign_is_correct():
    baseline = MotorProfile().controller((208, 205))
    adjusted = MotorProfile(100, 300, 1, 2).controller((208, 205))
    assert adjusted.pan == baseline.pan
    assert adjusted.tilt != baseline.tilt
    clock = Clock()
    adjusted._clock = clock
    adjusted.update(0, (100, 100), (45, 20, 55, 30))
    clock.sleep(0.05)
    command = adjusted.update(clock(), (100, 100), (45, 20, 55, 30))
    assert command.tilt_deg < baseline.transport.latest.tilt_deg
    assert command.pan_deg == baseline.transport.latest.pan_deg
    assert MotorProfile(100, 300, 1, 2).command_ticks(command)[1] >= 205


def test_stale_observation_loss_and_stop_remain_hold_with_delay_metrics():
    clock, delayed = Clock(), []
    controller = MotorProfile().controller((208, 205), on_delay_hold=lambda: delayed.append(True))
    controller._clock = clock
    controller.update(0, (100, 100), (70, 20, 80, 30))
    clock.sleep(0.05)
    assert controller.update(clock(), (100, 100), (70, 20, 80, 30)).state == "TRACK"
    clock.sleep(0.101)
    assert controller.snapshot().state == "HOLD" and delayed == [True]
    assert controller.snapshot().state == "HOLD" and delayed == [True]
    controller.stop()
    assert controller.update(clock(), (100, 100), (70, 20, 80, 30)).state == "HOLD"


def test_old_or_unknown_firmware_refuses_before_arm():
    port, clock = Port(), Clock()
    port.magic = "PAN0_TEST1"
    link = TicksLink(port, MotorProfile(), clock=clock, sleep=clock.sleep)
    with pytest.raises(RuntimeError):
        link.connect()
    assert [w[0] for w in port.writes] == ["HELLO"] and not link.armed


@pytest.mark.parametrize(
    "values,reason",
    [
        ("1,32,12,121,0,82,0,330", "표식 잔류"),
        ("0,32,4,121,0,208,0,205", "비활성 빌드"),
        ("1,32,4,30,0,208,0,205", "클록"),
        ("1,0,4,121,0,208,0,205", "클록"),
        ("1,32,0,121,0,208,0,205", "출력 모드"),
        ("1,32,4,121,0,208,0,340", "인계 조건 밖"),
        ("1,32,4,121,0,4301,0,205", "인계 조건 밖"),
        ("1,32,4,121,1,208,0,205", "인계 조건 밖"),
        ("1,32,4,121,0,208,0,205", "부팅 후 상태 변경"),
        ("1,32,4,121,0,208,0", "응답 불일치"),
        ("1,NaN,4,121,0,208,0,205", "응답 불일치"),
        ("1,32,4,121,0,208,0,-1", "응답 불일치"),
        ("1,256,4,121,0,208,0,205", "값 범위 오류"),
        ("1,32,4,121,0,65536,0,205", "값 범위 오류"),
        ("2,32,4,121,0,208,0,205", "값 범위 오류"),
    ],
)
def test_locked_connection_reads_reason_without_unlock_or_motion(values, reason):
    port, clock = Port((-1, -1)), Clock()
    port.state, port.diagnostic = "LOCKED", values
    link = TicksLink(port, MotorProfile(), clock=clock, sleep=clock.sleep)
    with pytest.raises(RuntimeError, match=reason):
        link.connect()
    link.close()
    assert [w[0] for w in port.writes] == ["HELLO", "DIAG"]
    assert port.closed and not link.connected and not link.armed and link.ticks is None


@pytest.mark.parametrize("error", ["DIAG_READ_FAILED", "BAD_FORMAT"])
def test_diagnostic_read_failure_or_older_firmware_stays_locked(error):
    port, clock = Port((-1, -1)), Clock()
    port.state, port.diagnostic_error = "LOCKED", error
    link = TicksLink(port, MotorProfile(), clock=clock, sleep=clock.sleep)
    with pytest.raises(
        RuntimeError, match="읽기 실패" if error == "DIAG_READ_FAILED" else "응답 불일치"
    ):
        link.connect()
    link.close()
    assert [w[0] for w in port.writes] == ["HELLO", "DIAG"] and port.closed


def test_worker_surfaces_locked_diagnostic_and_closes_without_arm(monkeypatch):
    port, clock = Port((-1, -1)), Clock()
    port.state, port.diagnostic = "LOCKED", "1,32,12,121,0,82,0,330"
    monkeypatch.setattr(
        "tools.vision_motors.TicksLink",
        lambda port, profile: TicksLink(port, profile, clock=clock, sleep=clock.sleep),
    )
    worker = MotorWorker("fake", MotorProfile(), serial_factory=lambda _: port)
    monkeypatch.setattr(worker.stopping, "wait", lambda _: False)
    worker._run()
    assert worker.state == "FAULT" and "표식 잔류" in worker.error
    assert worker.ticks is None and port.closed
    assert [w[0] for w in port.writes] == ["HELLO", "DIAG"]


@pytest.mark.parametrize("bad", ["foreign", "future", "angle", "sequence", "nonascii"])
def test_bad_motor_ack_latches_fault_and_never_rearms(bad):
    link, port, _ = connected()
    token = "00000000" if link.token != "00000000" else "FFFFFFFF"
    replies = {
        "foreign": f"ACK,{token},1,ARMED,208,205",
        "future": f"ACK,{link.token},2,ARMED,208,205",
        "angle": f"ACK,{link.token},1,ARMED,208,331",
        "sequence": f"ACK,{link.token},NaN,ARMED,208,205",
        "nonascii": "응답",
    }
    port.bad = replies[bad]
    with pytest.raises((RuntimeError, ValueError)):
        link.request("ARM")
    count = len(port.writes)
    for command, values in [("ARM", ()), ("SET_TICKS", (208, 205)), ("HOLD", ())]:
        with pytest.raises(RuntimeError, match="FAULT"):
            link.request(command, *values)
    assert len(port.writes) == count and not link.armed


def test_timeout_and_late_ack_allow_only_disarm_with_fixed_deadline():
    link, port, clock = connected()
    link.request("ARM")
    port.drop = "SET_TICKS"
    with pytest.raises(TimeoutError):
        link.request("SET_TICKS", 200, 210)
    port.rx.extend(f"ACK,{link.token},2,TRACK,208,205\n".encode())
    before = clock()
    link.close()
    assert clock() - before < 0.15 and port.closed
    assert [w[0] for w in port.writes] == ["HELLO", "ARM", "SET_TICKS", "DISARM"]


def source(*, video=None, model="local.pt", processed=1):
    return SimpleNamespace(
        video_path=video,
        model_path=model,
        stop=threading.Event(),
        metrics=SimpleNamespace(
            snapshot=lambda: {"counts": {"process": processed}},
            delay_hold=lambda: None,
            settings={},
        ),
        pan_tilt=MotorProfile().controller((208, 205)),
    )


@pytest.mark.parametrize("kwargs", [{"video": "clip.avi"}, {"model": None}, {"processed": 0}])
def test_only_warmed_live_yolo_source_can_arm(kwargs):
    worker = MotorWorker("unused", MotorProfile())
    worker.state = "READY"
    session = source(**kwargs)
    assert not can_arm(session)
    with pytest.raises(ValueError):
        worker.arm(session)
    assert not worker.alive()


@pytest.mark.parametrize("mode", ["connect_only", "arm_hold_stop", "stop_before_arm", "timeout"])
def test_worker_connection_arm_stop_and_fault_are_separate(mode, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(time, "monotonic", clock)
    monkeypatch.setattr(
        "tools.vision_motors.TicksLink",
        lambda port, profile: TicksLink(port, profile, clock=clock, sleep=clock.sleep),
    )
    port, session = Port(), source()
    worker = MotorWorker("fake", MotorProfile(), serial_factory=lambda _: port)

    class Signal:
        stopped = False
        waits = 0

        def is_set(self):
            return self.stopped

        def set(self):
            self.stopped = True

        def wait(self, timeout):
            self.waits += 1
            if self.waits == 1:  # Boot wait only; no port timing in the fake.
                return False
            if worker.state == "READY":
                if mode == "connect_only":
                    self.stopped = True
                else:
                    worker.arm(session)
                    if mode == "stop_before_arm":
                        session.stop.set()
                    if mode == "timeout":
                        port.drop = "HOLD"
            elif worker.state == "ARMED":
                captured = time.monotonic() - 0.06
                for timestamp in (captured, captured + 0.01):
                    command = worker.controller.update(
                        timestamp, (100, 100), (70, 20, 80, 30), received_at=timestamp
                    )
                    assert command.state == "HOLD"
                clock.sleep(0.01)
                assert (
                    worker.controller.update(
                        clock(), (100, 100), (70, 20, 80, 30), received_at=clock()
                    ).state
                    == "HOLD"
                )
                clock.sleep(0.02)
                assert (
                    worker.controller.update(
                        clock(), (100, 100), (70, 20, 80, 30), received_at=clock()
                    ).state
                    == "TRACK"
                )
                session.stop.set()
            assert self.waits < 6
            clock.sleep(timeout)
            return self.stopped

    worker.stopping = Signal()
    worker._run()
    commands = [w[0] for w in port.writes]
    assert port.closed and commands[-1] == "DISARM"
    assert "SET_TICKS" not in commands
    if mode == "connect_only":
        assert commands == ["HELLO", "DISARM"]
    elif mode == "stop_before_arm":
        assert commands == ["HELLO", "DISARM"] and worker.state == "FAULT"
    else:
        assert commands == ["HELLO", "ARM", "HOLD", "DISARM"]
        assert worker.state == ("FAULT" if mode == "timeout" else "CLOSED")


def test_worker_constructor_does_not_open_serial_or_start_a_thread():
    worker = MotorWorker("fake", MotorProfile(), serial_factory=lambda _: pytest.fail("opened"))
    assert not worker.alive() and worker.controller is None
