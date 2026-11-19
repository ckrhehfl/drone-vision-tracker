"""Opt-in, two-axis camera commissioning; importing this module opens no device."""

import math
import secrets
import threading
import time
from dataclasses import dataclass
from queue import Empty, Full, Queue

from tools.pan_tilt import AxisConfig, PanTiltCommand, PanTiltController

MIN_TICKS, MAX_TICKS = 80, 330
MANUAL_DISPLAY_MAX_AGE = 0.5
STEP_SETTLE_DELAY = 0.1


def nominal_angle(ticks):
    return (MAX_TICKS - ticks) * 0.72


@dataclass(frozen=True)
class MotorProfile:
    tilt_min: int = MIN_TICKS
    tilt_max: int = MAX_TICKS
    tilt_direction: int = 1  # Raw-image direction for an increase in ticks.
    tilt_rate: float = 5  # Commissioning ticks/s, not measured shaft speed.
    tilt_only: bool = False
    manual_tilt: bool = False

    def __post_init__(self):
        if (
            type(self.tilt_min) is not int
            or type(self.tilt_max) is not int
            or not MIN_TICKS <= self.tilt_min < self.tilt_max <= MAX_TICKS
            or type(self.tilt_direction) is not int
            or self.tilt_direction not in (-1, 1)
            or type(self.tilt_rate) not in (int, float)
            or not math.isfinite(self.tilt_rate)
            or not 0 < self.tilt_rate <= 10
        ):
            raise ValueError("1번 범위는 80~330틱 안, 시험 속도는 0 초과 10틱/초 이하입니다.")
        if self.tilt_only and self.tilt_rate > 2:
            raise ValueError("1번 상하 자동은 최대 2틱/초입니다.")
        if self.manual_tilt and (self.tilt_only or self.tilt_rate > 2):
            raise ValueError("키보드 시험은 자동 조작과 함께 선택할 수 없으며 최대 2틱/초입니다.")

    def validate_ticks(self, ticks):
        if (
            len(ticks) != 2
            or any(type(t) is not int for t in ticks)
            or not MIN_TICKS <= ticks[0] <= MAX_TICKS
            or not self.tilt_min <= ticks[1] <= self.tilt_max
        ):
            raise ValueError("현재/목표 PWM 명령이 확인한 축별 범위 밖입니다.")

    def controller(self, ticks, *, on_delay_hold=None, flip_vertical=False):
        self.validate_ticks(ticks)
        controller = PanTiltController(
            AxisConfig(0, 90, 180, 1, 7.2, 7.2),
            AxisConfig(
                nominal_angle(self.tilt_max),
                nominal_angle((self.tilt_min + self.tilt_max) / 2),
                nominal_angle(self.tilt_min),
                -self.tilt_direction if flip_vertical else self.tilt_direction,
                self.tilt_rate * 0.72,
                self.tilt_rate * 0.72,
            ),
            clock=time.monotonic,
            on_delay_hold=on_delay_hold,
            track_pan=not self.tilt_only,
        )
        # Seed the retained commands, including endpoints; never move to an arithmetic centre.
        controller.transport.send(PanTiltCommand(*(nominal_angle(t) for t in ticks)))
        controller.nominal_ticks = True
        return controller

    def command_ticks(self, command):
        angles = (command.pan_deg, command.tilt_deg)
        if not all(math.isfinite(a) for a in angles):
            raise ValueError("유효하지 않은 모터 목표")
        ticks = tuple(round(MAX_TICKS - a / 0.72) for a in angles)
        self.validate_ticks(ticks)
        return ticks


def _live_preview(session):
    displayed = session.metrics.last_display
    return displayed is not None and 0 <= time.monotonic() - displayed <= MANUAL_DISPLAY_MAX_AGE


def can_arm(session, profile):
    if session is None or session.video_path is not None or session.stop.is_set():
        return False
    if profile.manual_tilt:
        return _live_preview(session)
    if session.model_path:
        return session.metrics.snapshot()["counts"].get("process", 0) > 0
    if not profile.tilt_only or session.manual_target_lost:
        return False
    observed = session.manual_displayed_at
    return observed is not None and 0 <= time.monotonic() - observed <= MANUAL_DISPLAY_MAX_AGE


class TicksLink:
    """One outstanding request; no retry, reconnect or automatic re-ARM."""

    def __init__(self, port, profile, *, clock=time.monotonic, sleep=time.sleep):
        self.port, self.profile, self.clock, self.sleep = port, profile, clock, sleep
        self.token, self.sequence = secrets.token_hex(4).upper(), 0
        self.buffer = bytearray()
        self.connected = self.armed = self.faulted = False
        self.ticks = None
        self._fixed_pan = None

    def _write(self, line, *, dispatch=None):
        data = (line + "\n").encode("ascii")

        def write():
            if self.port.write(data) != len(data):
                raise RuntimeError("모터 명령 부분 전송: 재전송하지 않습니다.")
            return True

        return write() if dispatch is None else dispatch(write)

    def _line(self, deadline):
        while self.clock() < deadline:
            if b"\n" in self.buffer:
                line, _, rest = self.buffer.partition(b"\n")
                self.buffer[:] = rest
                if len(line) > 96:
                    raise RuntimeError("모터 응답 길이 초과")
                try:
                    return line.decode("ascii").removesuffix("\r").split(",")
                except UnicodeDecodeError as exc:
                    raise RuntimeError("모터 비ASCII 응답") from exc
            if len(self.buffer) > 96:
                raise RuntimeError("모터 응답 길이 초과")
            self.buffer.extend(self.port.read(96))
            self.sleep(0.002)
        raise TimeoutError("모터 응답 시간 초과: 자동 재구동하지 않습니다.")

    def _ticks(self, fields):
        if len(fields) != 2 or any(not t.isascii() or not t.isdigit() for t in fields):
            raise RuntimeError("잘못된 모터 명령 이력")
        ticks = tuple(int(t) for t in fields)
        self.profile.validate_ticks(ticks)
        return ticks

    def connect(self):
        # The worker has already waited for the Uno reset with servo V+ confirmed OFF.
        self.port.reset_input_buffer()
        self._write(f"HELLO,1,{self.token}")
        deadline = self.clock() + 1
        reply = self._line(deadline)
        if (
            len(reply) != 7
            or reply[:3] != ["READY", "1", self.token]
            or reply[3] not in {"DISARMED", "LOCKED"}
            or reply[4] != "VISION_TICKS1"
        ):
            raise RuntimeError("전용 통합 펌웨어/잔류 PWM 확인 실패: 구동하지 않습니다.")
        if self._line(deadline) != ["LIMITS", "VISION_TICKS1", "80", "330"]:
            raise RuntimeError("모터 범위 계약 불일치")
        if reply[3] == "LOCKED":
            self.sequence += 1
            self._write(f"DIAG,{self.token},{self.sequence}")
            diagnostic = self._line(self.clock() + 1)
            if diagnostic == ["ERR", self.token, str(self.sequence), "DIAG_READ_FAILED"]:
                raise RuntimeError(
                    "모터 구동 잠금: 드라이버 읽기 실패. 논리전원·I²C 배선을 확인하세요."
                )
            if (
                len(diagnostic) != 11
                or diagnostic[:3] != ["DIAG", self.token, str(self.sequence)]
                or any(not v.isascii() or not v.isdigit() for v in diagnostic[3:])
            ):
                raise RuntimeError("모터 구동 잠금: 진단 응답 불일치. 전용 펌웨어를 확인하세요.")
            enabled, mode1, mode2, prescale, on0, off0, on1, off1 = map(int, diagnostic[3:])
            if (
                enabled not in (0, 1)
                or any(v > 255 for v in (mode1, mode2, prescale))
                or any(v > 65535 for v in (on0, off0, on1, off1))
            ):
                raise RuntimeError("모터 구동 잠금: 진단 값 범위 오류")
            reasons = []
            if not enabled:
                reasons.append("하드웨어 비활성 빌드")
            if mode1 & 0x70 != 0x20 or prescale != 121:
                reasons.append("드라이버 클록 설정 불일치")
            if mode2 == 12:
                reasons.append("이전 구동/불확실한 쓰기 표식 잔류")
            elif mode2 != 4:
                reasons.append("드라이버 출력 모드 불일치")
            if on0 != 0 or on1 != 0 or not all(80 <= v <= 330 for v in (off0, off1)):
                reasons.append("잔류 PWM 명령이 80~330틱 인계 조건 밖")
            detail = ", ".join(reasons) or "부팅 후 상태 변경 또는 이전 읽기 실패"
            raise RuntimeError(
                f"모터 구동 잠금: {detail}. 외부전원 OFF 유지·별도 복구가 필요합니다."
            )
        self.ticks = self._ticks(reply[5:])
        if self.profile.tilt_only:
            self._fixed_pan = self.ticks[0]
        self.connected = True

    def request(self, command, *values, dispatch=None):
        states = {"ARM": "ARMED", "SET_TICKS": "TRACK", "HOLD": "HOLD", "DISARM": "DISARMED"}
        if command not in states or (values and command != "SET_TICKS"):
            raise ValueError("지원하지 않는 모터 명령")
        if not self.connected or self.sequence == 0xFFFFFFFF:
            raise RuntimeError("새 운영자 연결 확인이 필요합니다.")
        if self.faulted and command != "DISARM":
            raise RuntimeError("모터 FAULT: 이동 명령이 잠겼습니다.")
        if command == "ARM" and self.armed:
            raise RuntimeError("이미 구동 허가 상태입니다.")
        if command in {"SET_TICKS", "HOLD"} and not self.armed:
            raise RuntimeError("운영자의 구동 허가가 필요합니다.")
        if command == "SET_TICKS":
            self.profile.validate_ticks(values)
            if self._fixed_pan is not None and values[0] != self._fixed_pan:
                raise ValueError("0번 고정 출력 명령과 다른 목표를 차단했습니다.")
        self.sequence += 1
        try:
            line = ",".join(map(str, (command, self.token, self.sequence, *values)))
            if not self._write(line, dispatch=dispatch):
                self.sequence -= 1
                return None
            deadline = self.clock() + 0.15
            while True:
                reply = self._line(deadline)
                if (
                    len(reply) != 6
                    or reply[:2] != ["ACK", self.token]
                    or not reply[2].isascii()
                    or not reply[2].isdigit()
                    or not 1 <= int(reply[2]) <= 0xFFFFFFFF
                    or reply[3] not in states.values()
                ):
                    raise RuntimeError(f"모터 ACK 불일치: {reply}")
                ticks = self._ticks(reply[4:])
                if command == "DISARM" and int(reply[2]) < self.sequence:
                    continue
                if reply[2:4] != [str(self.sequence), states[command]]:
                    raise RuntimeError(f"모터 ACK 불일치: {reply}")
                self.ticks, self.armed = ticks, command != "DISARM"
                if self._fixed_pan is not None and ticks[0] != self._fixed_pan:
                    raise RuntimeError("0번 고정 출력 명령이 달라 구동을 종료합니다.")
                return ticks
        except (OSError, RuntimeError, ValueError):
            self.faulted, self.armed = True, False
            raise

    def close(self):
        try:
            if self.connected:
                self.request("DISARM")
        finally:
            self.armed = self.connected = False
            self.port.close()


def open_serial(port):
    import serial

    return serial.Serial(port, 115200, timeout=0, write_timeout=0.1)


class MotorWorker:
    """Serial runs outside Tk and inference; only the controller's latest target is consumed."""

    def __init__(self, port, profile, *, serial_factory=open_serial):
        self.port, self.profile, self.serial_factory = port, profile, serial_factory
        self.stopping = threading.Event()
        self.state, self.error, self.ticks = "CONNECTING", None, None
        self.controller = self.session = self._requested_session = None
        self._jog_keys = Queue(maxsize=1)
        self._jog_confirmed = self._jog_goal = None
        self._jog_check_at = self._jog_ready_at = 0.0
        self._tilt_step = None
        self._tilt_check_at = self._tilt_ready_at = 0.0
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def arm(self, session):
        if self.state != "READY" or self.stopping.is_set() or not can_arm(session, self.profile):
            raise ValueError(
                "실시간 영상이 준비된 뒤 구동하세요. 수동 지정 추종은 관측 박스가 필요합니다."
            )
        self._requested_session, self.state = session, "ARMING"

    def jog(self, delta):
        if (
            self.profile.manual_tilt
            and self.state == "ARMED"
            and not self.stopping.is_set()
            and delta in (-1, 1)
        ):
            try:
                self._jog_keys.put_nowait((time.monotonic(), delta))
            except Full:
                pass

    def stop(self):
        self.stopping.set()
        if self.controller is not None:
            self.controller.stop()

    def alive(self):
        return self.thread.is_alive()

    def _write_if_active(self, write):
        return not self.session.stop.is_set() and not self.stopping.is_set() and write()

    def _follow_both_cycle(self, link):
        command = self.controller.snapshot()
        if command.state == "TRACK":
            ticks = link.request(
                "SET_TICKS",
                *self.profile.command_ticks(command),
                dispatch=lambda write: self.controller.dispatch_tracking(
                    lambda latest, _: latest == command, lambda: self._write_if_active(write)
                ),
            )
            if ticks is not None:
                self.ticks = ticks
                return
        ticks = link.request("HOLD", dispatch=self._write_if_active)
        if ticks is not None:
            self.ticks = ticks

    def _tilt_target(self, tilt, command, tilt_adjustment):
        if (
            command.state != "TRACK"
            or tilt_adjustment == 0
            or (command.tilt_deg - nominal_angle(tilt)) * tilt_adjustment < 0
        ):
            return None
        return self.profile.command_ticks(command)[1]

    def _follow_tilt_cycle(self, link):
        if self.stopping.is_set() or self.session.stop.is_set():
            return
        manual_source = self.session.model_path is None
        if manual_source and not _live_preview(self.session):
            raise RuntimeError("실시간 화면 갱신이 끊겨 수동 지정 추종을 종료합니다.")
        pan, tilt = self.ticks
        target = None
        if not manual_source or not self.session.manual_target_lost:
            target = self._tilt_target(tilt, *self.controller.tilt_snapshot())
        now = time.monotonic()
        if self._tilt_step is not None:
            step = self._tilt_step
            continuing = target is not None and (target - tilt) * (step - tilt) > 0
            if continuing and now < self._tilt_check_at:
                return
            self.ticks = link.request("HOLD")
            if self.ticks[1] != step and (continuing or self.ticks[1] != tilt):
                raise RuntimeError("1번 상하 자동 한 틱 출력 확인 실패")
            self._tilt_step = None
            self._tilt_ready_at = time.monotonic() + 1 / self.profile.tilt_rate
            self.controller.align_tilt_output(nominal_angle(self.ticks[1]))
            return
        if target is not None and target != tilt and now >= self._tilt_ready_at:
            step = tilt + (1 if target > tilt else -1)

            def dispatch(write):
                if manual_source and not _live_preview(self.session):
                    return False

                def same_direction(command, adjustment):
                    if manual_source and self.session.manual_target_lost:
                        return False
                    current = self._tilt_target(tilt, command, adjustment)
                    return current is not None and (current - tilt) * (step - tilt) > 0

                return self.controller.dispatch_tracking(
                    same_direction, lambda: self._write_if_active(write)
                )

            response = link.request("SET_TICKS", pan, step, dispatch=dispatch)
            if response is not None:
                self.ticks = response
                self._tilt_step = step
                self._tilt_check_at = time.monotonic() + STEP_SETTLE_DELAY
            else:
                target = None
        if self._tilt_step is None:
            if self.stopping.is_set() or self.session.stop.is_set():
                return
            self.ticks = link.request("HOLD")
        if self.ticks[1] != tilt:
            raise RuntimeError("1번 상하 자동의 이전 출력 명령이 달라 구동을 종료합니다.")
        if target is None:
            self.controller.align_tilt_output(nominal_angle(tilt))

    def _manual_cycle(self, link):
        if self.stopping.is_set() or self.session.stop.is_set():
            return
        if not _live_preview(self.session):
            raise RuntimeError("실시간 화면 갱신이 끊겨 키보드 시험을 종료합니다.")
        try:
            pressed_at, delta = self._jog_keys.get_nowait()
        except Empty:
            pressed_at, delta = 0.0, 0
        now = time.monotonic()
        if self._jog_goal is not None:
            if now >= self._jog_check_at:
                self.ticks = link.request("HOLD")
                if self.ticks != self._jog_goal:
                    raise RuntimeError("1번 한 틱 출력 확인 실패")
                self._jog_confirmed = self.ticks
                self._jog_goal = None
                self._jog_ready_at = time.monotonic() + 1 / self.profile.tilt_rate
            return
        pan, previous_tilt = self._jog_confirmed
        tilt = previous_tilt + delta
        if (
            delta
            and 0 <= now - pressed_at <= 0.15
            and pressed_at >= self._jog_ready_at
            and self.profile.tilt_min <= tilt <= self.profile.tilt_max
        ):
            if self.stopping.is_set() or self.session.stop.is_set():
                return
            if not _live_preview(self.session):
                raise RuntimeError("실시간 화면 갱신이 끊겨 키보드 시험을 종료합니다.")
            self.ticks = link.request("SET_TICKS", pan, tilt)
            self._jog_goal = (pan, tilt)
            # Wait for one 50 ms firmware step before HOLD can cancel the new target.
            self._jog_check_at = time.monotonic() + STEP_SETTLE_DELAY
        else:
            self.ticks = link.request("HOLD")
        if self.ticks != self._jog_confirmed:
            raise RuntimeError("확인된 틱과 응답이 달라 키보드 시험을 종료합니다.")

    def _run(self):
        link = None
        try:
            link = TicksLink(self.serial_factory(self.port), self.profile)
            if self.stopping.wait(2):
                return
            link.connect()
            self.ticks, self.state = link.ticks, "READY"
            while not self.stopping.is_set():
                started = time.monotonic()
                if self.state == "ARMING":
                    source = self._requested_session
                    if not can_arm(source, self.profile):
                        raise RuntimeError("구동 전 영상 입력이 종료되거나 바뀌었습니다.")
                    expected = link.ticks
                    controller = None
                    if not self.profile.manual_tilt:
                        controller = self.profile.controller(
                            expected,
                            on_delay_hold=source.metrics.delay_hold,
                            flip_vertical=source.metrics.settings.get("flip_vertical", False),
                        )
                    retained = link.request(
                        "ARM",
                        dispatch=lambda write, source=source: (
                            not source.stop.is_set() and not self.stopping.is_set() and write()
                        ),
                    )
                    if retained is None:
                        break
                    if retained != expected:
                        raise RuntimeError("구동 전 PWM 명령이 바뀌었습니다.")
                    if source.stop.is_set() or self.stopping.is_set():
                        break
                    if source.pan_tilt is not None:
                        source.pan_tilt.stop()
                    if controller is not None:
                        controller.set_paused(False)
                        source.pan_tilt = controller
                    else:
                        self._jog_confirmed = retained
                    self.controller, self.session, self.state = controller, source, "ARMED"
                    source.metrics.settings["motor_profile"] = "VISION_TICKS1"
                if self.session is not None:
                    if self.session.stop.is_set() or self.stopping.is_set():
                        break
                    if self.profile.manual_tilt:
                        self._manual_cycle(link)
                    elif self.profile.tilt_only:
                        self._follow_tilt_cycle(link)
                    else:
                        self._follow_both_cycle(link)
                self.stopping.wait(max(0, 0.05 - (time.monotonic() - started)))
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            self.error = str(exc)
        finally:
            if self.controller is not None:
                self.controller.stop()
            if link is not None:
                try:
                    link.close()
                except (OSError, RuntimeError, ValueError) as exc:
                    self.error = f"{self.error or ''} 모터 정리 오류: {exc}".strip()
                self.ticks = link.ticks
            self.state = "FAULT" if self.error else "CLOSED"
