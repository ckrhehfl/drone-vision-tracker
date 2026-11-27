"""No real ports: check command mapping, framing, freshness and worker lifecycle."""

import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools import vision_motor_panel
from tools.pan_tilt import PanTiltCommand
from tools.vision_motors import MotorProfile, MotorWorker, TicksLink, can_arm, nominal_angle


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


class TiltStepPort(Port):
    def __init__(self, ticks, clock):
        super().__init__(ticks)
        self.clock = clock
        self.goal = None
        self.apply_at = 0.0
        self.applied = []

    def write(self, data):
        action = data.split(b",", 1)[0]
        now = self.clock()
        if self.goal is not None and now >= self.apply_at:
            self.ticks, self.goal = self.goal, None
            self.applied.append((now, self.ticks))
        written = super().write(data)
        if action == b"SET_TICKS":
            self.goal = tuple(map(int, self.writes[-1][3:]))
            self.apply_at = now + 0.05
        elif action in (b"HOLD", b"DISARM"):
            self.goal = None
        return written


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
        {"pan_image_reversed": 1},
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


@pytest.mark.parametrize(
    "settings",
    [(79, 330, 1, 2), (80, 331, 1, 2), (80, 330, 0, 2), (80, 330, 1, 2.1)],
)
def test_tilt_only_rejects_out_of_range_limits_speed_or_direction(settings):
    with pytest.raises(ValueError):
        MotorProfile(*settings, tilt_only=True)


def test_tilt_only_link_rejects_an_outgoing_pan_change_without_sending_it():
    link, port, _ = connected((247, 254), MotorProfile(tilt_rate=2, tilt_only=True))
    link.request("ARM")
    with pytest.raises(ValueError, match="0번 고정"):
        link.request("SET_TICKS", 248, 255)
    assert [w[0] for w in port.writes] == ["HELLO", "ARM"]
    link.close()


@pytest.mark.parametrize(("command", "ticks"), [("HOLD", ()), ("SET_TICKS", (247, 255))])
def test_tilt_only_link_locks_on_an_unexpected_pan_ack_and_can_disarm(command, ticks):
    link, port, _ = connected((247, 254), MotorProfile(tilt_rate=2, tilt_only=True))
    link.request("ARM")
    port.ticks = (248, 254)
    with pytest.raises(RuntimeError, match="0번 고정"):
        link.request(command, *ticks)
    with pytest.raises(RuntimeError, match="FAULT"):
        link.request("SET_TICKS", 247, 255)
    port.ticks = (247, 254)
    link.close()
    assert port.closed and [w[0] for w in port.writes] == ["HELLO", "ARM", command, "DISARM"]


def test_tilt_only_reports_pan_mismatch_on_the_final_disarm_ack():
    link, port, _ = connected((247, 254), MotorProfile(tilt_rate=2, tilt_only=True))
    link.request("ARM")
    port.ticks = (248, 254)
    with pytest.raises(RuntimeError, match="0번 고정"):
        link.close()
    assert port.closed and port.writes[-1][0] == "DISARM"
    assert link.ticks == (248, 254)


@pytest.mark.parametrize(
    ("flip_vertical", "raw_direction", "model", "start", "first", "outbound_above"),
    [
        (False, 1, "local.pt", 80, 81, True),
        (False, 1, "local.pt", 330, 329, False),
        (False, 1, "local.pt", 255, 256, True),
        (True, 1, "local.pt", 330, 329, True),
        (True, 1, "local.pt", 255, 256, False),
        (True, -1, None, 205, 206, True),
    ],
)
def test_tilt_only_worker_keeps_retained_pan_during_vertical_tracking(
    monkeypatch, flip_vertical, raw_direction, model, start, first, outbound_above
):
    profile = MotorProfile(tilt_direction=raw_direction, tilt_rate=2, tilt_only=True)
    clock = Clock()
    monkeypatch.setattr(time, "monotonic", clock)
    monkeypatch.setattr(
        "tools.vision_motors.TicksLink",
        lambda port, profile: TicksLink(port, profile, clock=clock, sleep=clock.sleep),
    )
    port, session = TiltStepPort((247, start), clock), source(model=model)
    session.metrics.settings["flip_vertical"] = flip_vertical
    worker = MotorWorker("fake", profile, serial_factory=lambda _: port)
    above, below = (30, 20, 40, 30), (60, 70, 70, 80)
    outbound, returning = (above, below) if outbound_above else (below, above)
    delayed = False
    cycles = 0

    def wait(timeout):
        nonlocal delayed, cycles
        clock.sleep(timeout)
        cycles += 1
        assert cycles < 100
        if worker.state == "READY":
            session.manual_displayed_at = session.metrics.last_display = clock()
            worker.arm(session)
        elif worker.state == "ARMED":
            if not delayed:
                # Fresh observations can accumulate while the serial worker is stalled.
                delayed = True
                for _ in range(100):
                    clock.sleep(0.05)
                    worker.controller.update(clock(), (100, 100), outbound, received_at=clock())
            session.metrics.last_display = clock()
            if len(port.applied) == 2:
                session.stop.set()
            else:
                box = returning if port.applied else outbound
                worker.controller.update(clock(), (100, 100), box, received_at=clock())
        return False

    worker.stopping.wait = wait
    worker._run()
    assert worker.state == "CLOSED" and port.closed
    assert [w[3:] for w in port.writes if w[0] == "SET_TICKS"] == [
        ["247", str(first)],
        ["247", str(start)],
    ]
    assert [ticks for _, ticks in port.applied] == [(247, first), (247, start)]
    assert port.applied[1][0] - port.applied[0][0] >= 0.5
    assert port.writes[-1][0] == "DISARM"


@pytest.mark.parametrize("loss_delay", [None, 0.0, 0.08])
def test_tilt_only_does_not_chase_jitter_or_a_lost_observation(monkeypatch, loss_delay):
    clock = Clock()
    monkeypatch.setattr(time, "monotonic", clock)
    profile = MotorProfile(tilt_rate=2, tilt_only=True)
    port = TiltStepPort((247, 205), clock)
    link = TicksLink(port, profile, clock=clock, sleep=clock.sleep)
    link.connect()
    worker = MotorWorker("fake", profile)
    worker.ticks = link.request("ARM")
    worker.session = source()
    observed_tilt, state = 205.54, "TRACK"
    worker.controller = SimpleNamespace(
        tilt_snapshot=lambda: (
            PanTiltCommand(nominal_angle(247), nominal_angle(observed_tilt), state),
            -0.9 if observed_tilt > 205.5 else 0.9,
        ),
        align_tilt_output=lambda _: None,
    )
    worker.controller.dispatch_tracking = (
        lambda accepts, write: accepts(
            worker.controller.tilt_snapshot()[0], 0, worker.controller.tilt_snapshot()[1]
        )
        and write()
    )
    worker._follow_tilt_cycle(link)
    assert port.writes[-1][3:] == ["247", "206"]
    if loss_delay is not None:
        clock.sleep(loss_delay)
        state = "HOLD"
        worker._follow_tilt_cycle(link)
        assert port.writes[-1][0] == "HOLD"
        clock.sleep(1)
        worker._follow_tilt_cycle(link)
        expected = (247, 205 if loss_delay == 0 else 206)
        assert port.ticks == expected and port.goal is None
        assert len(port.applied) == int(loss_delay > 0)
    else:
        clock.sleep(0.12)
        worker._follow_tilt_cycle(link)
        assert port.ticks == (247, 206)
        for value in (205.45, 205.54) * 3:
            observed_tilt = value
            clock.sleep(0.05)
            worker._follow_tilt_cycle(link)
        assert [w[3:] for w in port.writes if w[0] == "SET_TICKS"] == [["247", "206"]]
        clock.sleep(0.5)
        observed_tilt = 205.45
        worker._follow_tilt_cycle(link)
        assert port.writes[-1][3:] == ["247", "205"]
        clock.sleep(0.12)
        worker._follow_tilt_cycle(link)
        assert port.applied[1][0] - port.applied[0][0] >= 0.5
    link.close()


@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("new_box", [(35, 80, 55, 90), (35, 45, 55, 55)])
def test_latest_tilt_error_overrides_an_older_integrated_target(monkeypatch, pending, new_box):
    clock = Clock()
    monkeypatch.setattr(time, "monotonic", clock)
    profile = MotorProfile(tilt_rate=2, tilt_only=True)
    port = TiltStepPort((247, 205), clock)
    link = TicksLink(port, profile, clock=clock, sleep=clock.sleep)
    link.connect()
    worker = MotorWorker("fake", profile)
    worker.ticks = link.request("ARM")
    worker.session = source()
    worker.controller = profile.controller(worker.ticks)
    for _ in range(26):
        clock.sleep(0.02)
        worker.controller.update(clock(), (100, 100), (35, 10, 55, 20))
    if pending:
        worker._follow_tilt_cycle(link)
        assert port.writes[-1][3:] == ["247", "206"]
    clock.sleep(0.02)
    reversed_command = worker.controller.update(clock(), (100, 100), new_box)
    assert profile.command_ticks(reversed_command) == (247, 206)
    worker._follow_tilt_cycle(link)
    assert port.writes[-1][0] == "HOLD" and port.goal is None and not port.applied
    assert len([w for w in port.writes if w[0] == "SET_TICKS"]) == int(pending)
    assert profile.command_ticks(worker.controller.snapshot()) == (247, 205)
    link.close()


@pytest.mark.parametrize(
    "change",
    ["clear", "loss", "target_lost", "expired", "preview_loss", "preview_delay", "already_sent"],
)
def test_tilt_write_rechecks_authorization_at_dispatch(monkeypatch, change):
    from tools import vision_motors

    clock = Clock()
    monkeypatch.setattr(time, "monotonic", clock)
    profile = MotorProfile(tilt_rate=2, tilt_only=True)
    port = TiltStepPort((247, 205), clock)
    link = TicksLink(port, profile, clock=clock, sleep=clock.sleep)
    link.connect()
    worker = MotorWorker("fake", profile)
    worker.ticks = link.request("ARM")
    worker.session = source(model=None)
    worker.controller = profile.controller(worker.ticks)
    for _ in range(12):
        clock.sleep(0.05)
        worker.controller.update(clock(), (100, 100), (35, 10, 55, 20))
    worker.session.metrics.last_display = clock()
    write = link._write
    live_preview = vision_motors._live_preview

    def preview(session):
        if link.sequence > 1:
            if change == "preview_loss":
                worker.controller.hold()
            elif change == "preview_delay":
                clock.sleep(0.11)
        return live_preview(session)

    def interleave(line, **kwargs):
        if not line.startswith("SET_TICKS"):
            return write(line, **kwargs)
        if change == "already_sent":
            sent = write(line, **kwargs)
            worker.stop()
            return sent
        if change == "clear":
            worker.stop()
        elif change == "loss":
            worker.controller.hold()
        elif change == "target_lost":
            worker.session.manual_target_lost = True
        elif change == "expired":
            clock.sleep(0.11)
        return write(line, **kwargs)

    monkeypatch.setattr(link, "_write", interleave)
    monkeypatch.setattr(vision_motors, "_live_preview", preview)
    worker._follow_tilt_cycle(link)
    assert (
        port.writes[-1][0]
        == {
            "clear": "ARM",
            "loss": "HOLD",
            "target_lost": "HOLD",
            "expired": "HOLD",
            "preview_loss": "HOLD",
            "preview_delay": "HOLD",
            "already_sent": "SET_TICKS",
        }[change]
    )
    link.close()
    moves = [w[3:] for w in port.writes if w[0] == "SET_TICKS"]
    assert moves == ([["247", "206"]] if change == "already_sent" else [])
    assert port.writes[-1][0] == "DISARM" and port.closed and port.goal is None


@pytest.mark.parametrize(
    "change", ["none", "stop", "loss", "expired", "new_target", "opposite", "center"]
)
@pytest.mark.parametrize("pan_image_reversed", [False, True])
def test_two_axis_write_rechecks_latest_observation_and_stop(
    monkeypatch, change, pan_image_reversed
):
    clock = Clock()
    monkeypatch.setattr(time, "monotonic", clock)
    profile = MotorProfile(pan_image_reversed=pan_image_reversed)
    rightward_tick_direction = 1 if pan_image_reversed else -1
    port = Port((247, 205))
    link = TicksLink(port, profile, clock=clock, sleep=clock.sleep)
    link.connect()
    worker = MotorWorker("fake", profile)
    worker.ticks = link.request("ARM")
    worker.session = source()
    worker.controller = profile.controller(worker.ticks)
    for _ in range(8):
        clock.sleep(0.04)
        worker.controller.update(clock(), (100, 100), (65, 10, 85, 20))
    assert worker.controller.snapshot().state == "TRACK"
    returning = (40, 40, 60, 60) if change == "center" else (15, 70, 35, 90)
    if change in {"center", "opposite"}:
        clock.sleep(0.01)
        worker.controller.update(clock(), (100, 100), returning)
    write = link._write

    def interleave(line, **kwargs):
        if line.startswith("SET_TICKS"):
            if change == "stop":
                worker.stop()
            elif change == "loss":
                worker.controller.hold()
            elif change == "expired":
                clock.sleep(0.11)
            elif change == "new_target":
                clock.sleep(0.05)
                worker.controller.update(clock(), (100, 100), (15, 70, 35, 90))
        return write(line, **kwargs)

    monkeypatch.setattr(link, "_write", interleave)
    worker._follow_both_cycle(link)
    action = {"none": "SET_TICKS", "stop": "ARM"}.get(change, "HOLD")
    assert port.writes[-1][0] == action
    assert len([w for w in port.writes if w[0] == "SET_TICKS"]) == (change == "none")
    if change == "none":
        pan, tilt = map(int, port.writes[-1][3:])
        assert (pan - 247) * rightward_tick_direction > 0 and tilt > 205
    if action == "HOLD":
        assert worker.controller.snapshot() == PanTiltCommand(
            nominal_angle(247), nominal_angle(205)
        )
        monkeypatch.setattr(link, "_write", write)
        for _ in range(12):
            clock.sleep(0.04)
            worker.controller.update(clock(), (100, 100), returning)
        worker._follow_both_cycle(link)
        assert port.writes[-1][0] == "SET_TICKS"
        pan, tilt = map(int, port.writes[-1][3:])
        if change == "center":
            assert (pan, tilt) == (247, 205)
        else:
            assert (pan - 247) * rightward_tick_direction < 0 and tilt < 205
    link.close()


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
        ("1,160,4,121,0,208,0,205", "PWM 재시작 대기"),
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


def source(*, video=None, model="local.pt", processed=1, displayed=1):
    return SimpleNamespace(
        video_path=video,
        model_path=model,
        manual_displayed_at=None,
        manual_target_lost=False,
        stop=threading.Event(),
        serial_write_lock=threading.Lock(),
        metrics=SimpleNamespace(
            snapshot=lambda: {"counts": {"process": processed}},
            delay_hold=lambda: None,
            settings={},
            last_display=time.monotonic() if displayed else None,
        ),
        pan_tilt=MotorProfile().controller((208, 205)),
    )


@pytest.mark.parametrize("kwargs", [{"video": "clip.avi"}, {"model": None}, {"processed": 0}])
def test_only_warmed_live_yolo_source_can_arm(kwargs):
    worker = MotorWorker("unused", MotorProfile())
    worker.state = "READY"
    session = source(**kwargs)
    assert not can_arm(session, worker.profile)
    with pytest.raises(ValueError):
        worker.arm(session)
    assert not worker.alive()


def test_yolo_arm_waits_for_first_processing_metric():
    session = source()
    session.metrics.snapshot = lambda: {"counts": {}}
    assert not can_arm(session, MotorProfile())


@pytest.mark.parametrize("kwargs", [{"video": "clip.avi"}, {"displayed": 0}])
def test_keyboard_arm_requires_displayed_live_camera(kwargs):
    assert not can_arm(source(model=None, **kwargs), MotorProfile(tilt_rate=2, manual_tilt=True))


def test_manual_live_box_only_enables_tilt_auto(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(time, "monotonic", clock)
    profile = MotorProfile(tilt_rate=2, tilt_only=True)
    session = source(model=None)
    assert not can_arm(session, profile)
    session.manual_displayed_at = clock()
    assert can_arm(session, profile)
    assert not can_arm(session, MotorProfile())
    session.manual_target_lost = True
    assert not can_arm(session, profile)
    assert can_arm(session, MotorProfile(tilt_rate=2, manual_tilt=True))
    session.manual_target_lost = False
    worker = MotorWorker("fake", profile)
    worker.state = "READY"
    worker.stop()
    with pytest.raises(ValueError):
        worker.arm(session)
    session.video_path = "clip.mp4"
    assert not can_arm(session, profile)
    session.video_path = None
    clock.sleep(0.51)
    assert not can_arm(session, profile)


@pytest.mark.parametrize("stale", [False, True])
@pytest.mark.parametrize("auto_direction", [-1, 1])
def test_manual_worker_jogs_only_tilt_one_tick_at_a_time(monkeypatch, stale, auto_direction):
    class SteppingPort(Port):
        goal = None
        apply_at = 0.0

        def write(self, data):
            action, *fields = data.decode().strip().split(",")
            if action == "HOLD" and self.goal is not None:
                if clock() >= self.apply_at:
                    self.ticks = self.goal
                self.goal = None
            sent = super().write(data)
            if action == "SET_TICKS":
                self.goal = tuple(map(int, fields[-2:]))
                self.apply_at = clock() + 0.05
                step_times.append(clock())
            return sent

    clock = Clock()
    monkeypatch.setattr(time, "monotonic", clock)
    monkeypatch.setattr(
        "tools.vision_motors.TicksLink",
        lambda port, profile: TicksLink(port, profile, clock=clock, sleep=clock.sleep),
    )
    profile = MotorProfile(200, 201, auto_direction, 2, manual_tilt=True)
    port, session = SteppingPort((247, 200)), source(model=None)
    worker = MotorWorker("fake", profile, serial_factory=lambda _: port)
    keys = [(0, -1), (0.15, 1), (0.25, -1), (0.60, -1), (0.85, 1), (0.95, -1)]
    step_times = []
    armed_at = None

    def wait(timeout):
        nonlocal armed_at
        assert clock() < 5
        if not stale or armed_at is None or clock() - armed_at < 1.12:
            session.metrics.last_display = clock()
        if worker.state == "READY":
            worker.arm(session)
        if worker.state == "ARMED":
            if armed_at is None:
                armed_at = clock()
            elapsed = clock() - armed_at
            if keys and elapsed >= keys[0][0]:
                worker.jog(keys.pop(0)[1])
            if elapsed > 1.2 and not stale:
                session.stop.set()
        clock.sleep(timeout)
        return False

    worker.stopping.wait = wait
    worker._run()
    assert worker.state == ("FAULT" if stale else "CLOSED") and port.closed
    assert [w[3:] for w in port.writes if w[0] == "SET_TICKS"] == [
        ["247", "201"],
        ["247", "200"],
    ]
    assert step_times[1] - step_times[0] >= 0.7
    assert port.writes[-1][0] == "DISARM"


@pytest.mark.parametrize(("key", "virtual_key", "delta"), [("Up", 0x26, 1), ("Down", 0x28, -1)])
@pytest.mark.parametrize("state_api", ["GetAsyncKeyState", "GetKeyState"])
def test_windows_preheld_key_waits_for_release(monkeypatch, key, virtual_key, delta, state_api):
    import ctypes

    down = {virtual_key: -0x8000}
    user32 = SimpleNamespace(
        GetAsyncKeyState=Mock(return_value=1), GetKeyState=Mock(return_value=1)
    )
    getattr(user32, state_api).side_effect = lambda code: down.get(code, 1)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=user32), raising=False)
    monkeypatch.setattr(vision_motor_panel, "sys", SimpleNamespace(platform="win32"), raising=False)
    errors = Mock()
    monkeypatch.setitem(sys.modules, "tkinter", SimpleNamespace(messagebox=errors))

    profile = MotorProfile(200, 210, 1, 2, manual_tilt=True)
    link, port, clock = connected((247, 205), profile)
    monkeypatch.setattr(time, "monotonic", clock)
    session = source(model=None)
    worker = MotorWorker("fake", profile)
    worker.state = "READY"
    panel = vision_motor_panel.MotorPanel.__new__(vision_motor_panel.MotorPanel)
    panel.worker = worker
    panel.get_session = lambda: session
    panel.is_closing = lambda: False
    panel.ready_to_move = Mock()
    panel.ready_to_move.get.return_value = True
    panel.jog_button = Mock()
    panel.jog_button.focus_get.return_value = panel.jog_button
    panel.jog_button.winfo_viewable.return_value = True
    panel._pressed_keys, panel._release_jobs = set(), {}

    panel.arm()
    errors.showerror.assert_not_called()
    assert worker.state == "ARMING"
    worker._jog_confirmed = worker.ticks = link.request("ARM")
    worker.session, worker.state = session, "ARMED"

    event = SimpleNamespace(keysym=key)
    for _ in range(2):  # Win32 repeats KeyPress without an intervening KeyRelease.
        panel.jog_key(event)
        worker._manual_cycle(link)
    assert not any(row[0] == "SET_TICKS" for row in port.writes)
    assert panel._pressed_keys == {key}

    down.clear()
    panel.release_key(event)
    clear_key, released = panel.jog_button.after_idle.call_args.args
    clear_key(released)
    down[virtual_key] = -0x8000
    panel.jog_key(event)
    worker._manual_cycle(link)
    assert [row[3:] for row in port.writes if row[0] == "SET_TICKS"] == [["247", str(205 + delta)]]
    link.close()


@pytest.mark.parametrize(
    ("stage", "command", "error"),
    [
        ("idle", ("HOLD",), "확인된 틱"),
        ("new step", ("SET_TICKS", 247, 201), "확인된 틱"),
        ("settling", ("HOLD",), "한 틱 출력 확인 실패"),
    ],
)
def test_manual_worker_rejects_unexpected_ticks(stage, command, error):
    profile = MotorProfile(200, 210, 1, 2, manual_tilt=True)
    worker = MotorWorker("fake", profile)
    worker._jog_confirmed = (247, 200)
    worker.session = source(model=None)
    if stage == "new step":
        worker._jog_keys.put_nowait((time.monotonic(), 1))
    elif stage == "settling":
        worker._jog_goal = (247, 201)
    calls = []

    def request(*args, dispatch=None):
        assert dispatch is None or dispatch(lambda: True)
        calls.append(args)
        return (247, 208)

    with pytest.raises(RuntimeError, match=error):
        worker._manual_cycle(SimpleNamespace(request=request))
    assert calls == [command]


def test_stopping_camera_discards_a_dequeued_manual_step():
    worker = MotorWorker("fake", MotorProfile(200, 210, 1, 2, manual_tilt=True))
    worker._jog_confirmed = (247, 200)
    worker.session = source(model=None)

    def take_key():
        worker.session.stop.set()
        return time.monotonic(), 1

    worker._jog_keys = SimpleNamespace(get_nowait=take_key)
    worker._manual_cycle(SimpleNamespace(request=lambda *_: pytest.fail("sent after stop")))


def test_manual_step_rechecks_display_freshness_after_dequeue(monkeypatch):
    clock = Clock()
    clock.now = 0.49
    monkeypatch.setattr(time, "monotonic", clock)
    worker = MotorWorker("fake", MotorProfile(200, 210, 1, 2, manual_tilt=True))
    worker._jog_confirmed = (247, 200)
    worker.session = source(model=None)
    worker.session.metrics.last_display = 0.0

    def delayed_key():
        clock.now = 0.62
        return 0.48, 1

    worker._jog_keys = SimpleNamespace(get_nowait=delayed_key)
    link = SimpleNamespace(request=lambda *_: pytest.fail("sent after the display went stale"))
    with pytest.raises(RuntimeError, match="화면 갱신이 끊겨"):
        worker._manual_cycle(link)


@pytest.mark.parametrize("change", ["stop", "preview_expired", "key_expired"])
def test_manual_key_dispatch_drops_cancelled_or_stale_motion(monkeypatch, change):
    clock = Clock()
    monkeypatch.setattr(time, "monotonic", clock)
    profile = MotorProfile(200, 210, 1, 2, manual_tilt=True)
    port = Port((247, 205))
    link = TicksLink(port, profile, clock=clock, sleep=clock.sleep)
    link.connect()
    worker = MotorWorker("fake", profile)
    worker.ticks = worker._jog_confirmed = link.request("ARM")
    worker.session = source(model=None)
    worker._jog_keys.put_nowait((clock(), 1))
    if change == "preview_expired":
        worker.session.metrics.last_display = clock() - 0.49
    write = link._write

    def interleave(line, **kwargs):
        if line.startswith("SET_TICKS"):
            if change == "stop":
                worker.stop()
            else:
                clock.sleep(0.02 if change == "preview_expired" else 0.16)
        return write(line, **kwargs)

    monkeypatch.setattr(link, "_write", interleave)
    worker._manual_cycle(link)
    assert worker._jog_goal is None
    link.close()
    assert port.closed and [message[0] for message in port.writes] == ["HELLO", "ARM", "DISARM"]


def test_camera_read_failure_and_manual_serial_handoff_share_stop_order(monkeypatch):
    from tools.camera_gui import CameraSession

    clock = Clock()
    monkeypatch.setattr(time, "monotonic", clock)
    with monkeypatch.context() as setup:
        setup.setattr(threading.Thread, "start", lambda thread: None)
        session = CameraSession(0, None, 0.65, "cpu")
    capture = SimpleNamespace(
        isOpened=lambda: True, set=lambda *_: None, read=lambda: (False, None), release=lambda: None
    )
    monkeypatch.setitem(
        sys.modules,
        "cv2",
        SimpleNamespace(
            CAP_ANY=0,
            CAP_AVFOUNDATION=1,
            CAP_PROP_FRAME_WIDTH=2,
            CAP_PROP_FRAME_HEIGHT=3,
            CAP_PROP_FPS=4,
            VideoCapture=lambda *_: capture,
        ),
    )
    write_started, stop_waiting = threading.Event(), threading.Event()

    def read_failure():
        if write_started.wait(2):
            session.capture()

    camera = threading.Thread(target=read_failure, daemon=True)
    lock = session.serial_write_lock

    class ObservedLock:
        def __enter__(self):
            if threading.current_thread() is camera:
                stop_waiting.set()
            lock.acquire()

        def __exit__(self, *_):
            lock.release()

    session.serial_write_lock = ObservedLock()
    profile = MotorProfile(200, 210, 1, 2, manual_tilt=True)
    port = Port((247, 205))
    link = TicksLink(port, profile, clock=clock, sleep=clock.sleep)
    link.connect()
    worker = MotorWorker("fake", profile)
    worker.ticks = worker._jog_confirmed = link.request("ARM")
    worker.session = session
    session.metrics.last_display = clock()
    worker._jog_keys.put_nowait((clock(), 1))
    write = port.write

    def observe_write(data):
        if data.startswith(b"SET_TICKS"):
            write_started.set()
            assert stop_waiting.wait(2) and not session.stop.is_set()
        return write(data)

    monkeypatch.setattr(port, "write", observe_write)
    camera.start()
    try:
        worker._manual_cycle(link)
        camera.join(timeout=2)
        assert not camera.is_alive() and session.stop.is_set()
        assert "카메라 영상이 끊겼습니다" in session.errors.get_nowait()
        worker._jog_keys.put_nowait((clock(), 1))
        worker._manual_cycle(link)
    finally:
        link.close()
    assert port.closed
    assert [row[0] for row in port.writes] == ["HELLO", "ARM", "SET_TICKS", "DISARM"]


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
