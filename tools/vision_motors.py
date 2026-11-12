"""Opt-in, two-axis camera commissioning; importing this module opens no device."""

import math
import secrets
import threading
import time
from dataclasses import dataclass

from tools.pan_tilt import AxisConfig, PanTiltCommand, PanTiltController

MIN_TICKS, MAX_TICKS = 80, 330


def nominal_angle(ticks):
    return (MAX_TICKS - ticks) * 0.72


@dataclass(frozen=True)
class MotorProfile:
    tilt_min: int = MIN_TICKS
    tilt_max: int = MAX_TICKS
    tilt_direction: int = 1  # Increasing ticks moves the installed camera upward.
    tilt_rate: float = 5  # Commissioning ticks/s, not measured shaft speed.

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

    def validate_ticks(self, ticks):
        if (
            len(ticks) != 2
            or any(type(t) is not int for t in ticks)
            or not MIN_TICKS <= ticks[0] <= MAX_TICKS
            or not self.tilt_min <= ticks[1] <= self.tilt_max
        ):
            raise ValueError("현재/목표 PWM 명령이 확인한 축별 범위 밖입니다.")

    def controller(self, ticks, *, on_delay_hold=None):
        self.validate_ticks(ticks)
        controller = PanTiltController(
            AxisConfig(0, 90, 180, 1, 7.2, 7.2),
            AxisConfig(
                nominal_angle(self.tilt_max),
                nominal_angle((self.tilt_min + self.tilt_max) / 2),
                nominal_angle(self.tilt_min),
                self.tilt_direction,
                self.tilt_rate * 0.72,
                self.tilt_rate * 0.72,
            ),
            clock=time.monotonic,
            on_delay_hold=on_delay_hold,
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


def can_arm(session):
    return bool(
        session is not None
        and session.model_path
        and session.video_path is None
        and not session.stop.is_set()
        and session.metrics.snapshot()["counts"]["process"] > 0
    )


class TicksLink:
    """One outstanding request; no retry, reconnect or automatic re-ARM."""

    def __init__(self, port, profile, *, clock=time.monotonic, sleep=time.sleep):
        self.port, self.profile, self.clock, self.sleep = port, profile, clock, sleep
        self.token, self.sequence = secrets.token_hex(4).upper(), 0
        self.buffer = bytearray()
        self.connected = self.armed = self.faulted = False
        self.ticks = None

    def _write(self, line):
        data = (line + "\n").encode("ascii")
        if self.port.write(data) != len(data):
            raise RuntimeError("모터 명령 부분 전송: 재전송하지 않습니다.")

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
        self.connected = True

    def request(self, command, *values):
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
        self.sequence += 1
        try:
            self._write(",".join(map(str, (command, self.token, self.sequence, *values))))
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
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def arm(self, session):
        if self.state != "READY" or not can_arm(session):
            raise ValueError("실시간 YOLO 카메라의 첫 처리 결과를 기다린 뒤 구동하세요.")
        self._requested_session, self.state = session, "ARMING"

    def stop(self):
        self.stopping.set()
        if self.controller is not None:
            self.controller.stop()

    def alive(self):
        return self.thread.is_alive()

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
                    if not can_arm(source):
                        raise RuntimeError("구동 전 영상 입력이 종료되거나 바뀌었습니다.")
                    expected = link.ticks
                    controller = self.profile.controller(
                        expected, on_delay_hold=source.metrics.delay_hold
                    )
                    retained = link.request("ARM")
                    if retained != expected:
                        raise RuntimeError("구동 전 PWM 명령이 바뀌었습니다.")
                    if source.stop.is_set() or self.stopping.is_set():
                        break
                    controller.set_paused(False)
                    source.pan_tilt.stop()
                    source.pan_tilt = controller
                    self.controller, self.session, self.state = controller, source, "ARMED"
                    source.metrics.settings["motor_profile"] = "VISION_TICKS1"
                if self.session is not None:
                    if self.session.stop.is_set() or self.stopping.is_set():
                        break
                    command = self.controller.snapshot()
                    if command.state == "TRACK":
                        self.ticks = link.request("SET_TICKS", *self.profile.command_ticks(command))
                    else:
                        self.ticks = link.request("HOLD")
                self.stopping.wait(max(0, 0.05 - (time.monotonic() - started)))
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            self.error = str(exc)
        finally:
            if self.controller is not None:
                self.controller.stop()
            if link is not None:
                try:
                    link.close()
                    self.ticks = link.ticks
                except (OSError, RuntimeError, ValueError) as exc:
                    self.error = f"{self.error or ''} 모터 정리 응답 미확인: {exc}".strip()
            self.state = "FAULT" if self.error else "CLOSED"
