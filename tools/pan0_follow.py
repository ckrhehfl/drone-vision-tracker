"""Pan-only commissioning of the existing controller; mock output is the default."""

import argparse
import math
import queue
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from tools.pan_tilt import AxisConfig, PanTiltController


@dataclass(frozen=True)
class Pan0Profile:
    minimum: int
    start: int
    maximum: int
    direction: int

    def __post_init__(self):
        if (
            any(
                type(v) is not int for v in (self.minimum, self.start, self.maximum, self.direction)
            )
            or not 80 <= self.minimum < self.start < self.maximum <= 330
            or self.maximum - self.minimum > 10
            or self.minimum >> 8 != self.maximum >> 8
            or self.direction not in (-1, 1)
        ):
            raise ValueError(
                "PAN0: 시작값을 포함하는 동일 상위 바이트의 최대 10틱 폭과 방향 ±1 필요"
            )

    @staticmethod
    def centidegrees(ticks):
        # Existing temporary pan command scale; this is not a measured mechanical angle.
        return (330 - ticks) * 72

    def controller(self):
        return PanTiltController(
            AxisConfig(
                self.centidegrees(self.maximum) / 100,
                self.centidegrees(self.start) / 100,
                self.centidegrees(self.minimum) / 100,
                self.direction,
                7.2,
                7.2,
            ),
            AxisConfig(-1, 0, 1, 1, 1, 1),
        )


class Pan0Serial:
    """One request/ACK at a time, no retries, reconnects or automatic ARM."""

    def __init__(self, port, profile, *, clock=time.monotonic, sleep=time.sleep):
        self.port, self.profile = port, profile
        self.clock, self.sleep = clock, sleep
        self.session = secrets.token_hex(4).upper()
        self.sequence = 0
        self.buffer = bytearray()
        self.armed = False
        self.connected = False
        self._faulted = False
        self.last_goal = -math.inf

    def _write(self, line):
        data = (line + "\n").encode("ascii")
        if self.port.write(data) != len(data):
            raise RuntimeError("Serial 부분 전송: 재시도하지 않고 종료")

    def _line(self, deadline):
        while self.clock() < deadline:
            while b"\n" in self.buffer:
                line, _, rest = self.buffer.partition(b"\n")
                self.buffer[:] = rest
                if len(line) > 96:
                    raise RuntimeError("Serial 응답 길이 초과")
                try:
                    return line.decode("ascii").removesuffix("\r").split(",")
                except UnicodeDecodeError as exc:
                    raise RuntimeError("Serial 비ASCII 응답") from exc
            if len(self.buffer) > 96:
                raise RuntimeError("Serial 응답 길이 초과")
            self.buffer.extend(self.port.read(96))
            self.sleep(0.002)
        raise TimeoutError("Serial ACK 시간 초과: 재ARM 없이 종료")

    def connect(self):
        # Opening an Uno can reset it. Only HELLO is sent after its boot interval.
        self.sleep(2)
        self.port.reset_input_buffer()
        self._write(f"HELLO,1,{self.session}")
        deadline = self.clock() + 1
        ready = ["READY", "1", self.session, "DISARMED", "PAN0_TEST1"]
        if self._line(deadline) != ready:
            raise RuntimeError("PAN0 보정/프로토콜 확인 실패: ARM하지 않음")
        expected = [
            "PROFILE",
            "PAN0_TEST1",
            str(self.profile.minimum),
            str(self.profile.start),
            str(self.profile.maximum),
        ]
        if self._line(deadline) != expected:
            raise RuntimeError("펌웨어 시험 범위가 호스트와 다름: ARM하지 않음")
        self.connected = True

    def request(self, command, *values):
        states = {"ARM": "ARMED", "SET": "TRACK", "HOLD": "HOLD", "DISARM": "DISARMED"}
        if command not in states:
            raise ValueError("지원하지 않는 PAN0 명령")
        if not self.connected or self.sequence == 0xFFFFFFFF:
            raise RuntimeError("유효한 새 연결 필요")
        if self._faulted and command != "DISARM":
            raise RuntimeError("Serial FAULT: 운영자 확인을 받는 새 연결 필요")
        if command in {"SET", "HOLD"} and not self.armed:
            raise RuntimeError("운영자의 새 ARM 필요")
        self.sequence += 1
        try:
            self._write(",".join(map(str, (command, self.session, self.sequence, *values))))
            if command in {"SET", "HOLD"}:
                self.last_goal = self.clock()
            deadline = self.clock() + 0.15
            while True:
                reply = self._line(deadline)
                if (
                    len(reply) != 6
                    or reply[:2] != ["ACK", self.session]
                    or not reply[2].isascii()
                    or not reply[2].isdigit()
                    or not 1 <= int(reply[2]) <= 0xFFFFFFFF
                    or reply[3] not in states.values()
                    or reply[5] != "NA"
                ):
                    raise RuntimeError(f"PAN0 ACK 불일치: {reply}")
                if reply[4] != "NA":
                    if not reply[4].isascii() or not reply[4].isdigit():
                        raise RuntimeError("잘못된 PAN0 출력 명령값")
                    if (
                        not self.profile.centidegrees(self.profile.maximum)
                        <= int(reply[4])
                        <= self.profile.centidegrees(self.profile.minimum)
                    ):
                        raise RuntimeError("PAN0 출력 명령값 범위 초과")
                # Only cleanup may pass a late prior ACK; the original deadline stays fixed.
                if command == "DISARM" and int(reply[2]) < self.sequence:
                    continue
                if reply[2:4] != [str(self.sequence), states[command]]:
                    raise RuntimeError(f"PAN0 ACK 불일치: {reply}")
                if reply[4] == "NA" and command in {"SET", "HOLD"}:
                    raise RuntimeError("PAN0 출력 이력이 없음")
                self.armed = command != "DISARM"
                return reply
        except (OSError, RuntimeError, ValueError):
            self._faulted = True
            self.armed = False
            raise

    def arm(self):
        self.request("ARM")

    def send(self, command):
        if self.clock() - self.last_goal < 0.05:
            return None
        if command.state == "HOLD":
            return self.request("HOLD")
        if not math.isfinite(command.pan_deg):
            raise ValueError("PAN0 목표가 유한수가 아님")
        angle = round(command.pan_deg * 100)
        if (
            not self.profile.centidegrees(self.profile.maximum)
            <= angle
            <= self.profile.centidegrees(self.profile.minimum)
        ):
            raise ValueError("PAN0 목표 범위 초과")
        return self.request("SET", angle, 0)

    def close(self):
        try:
            if self.connected:
                # Also attempt DISARM after an ACK timeout; firmware watchdog is the backup.
                self.request("DISARM")
        finally:
            self.armed = self.connected = False
            self.port.close()


def run(
    profile,
    *,
    link=None,
    video=None,
    model=None,
    duration=6,
    clock=time.monotonic,
    sleep=time.sleep,
):
    controller = profile.controller()
    session = None
    pending_result = None
    try:
        start = clock()
        if video is not None:
            from tools.camera_gui import CameraSession

            session = CameraSession(None, model, 0.35, "cpu", video_path=video)
            # Keep the first result intact while imports/model warmup finish, before any ARM.
            while pending_result is None:
                try:
                    error = session.errors.get_nowait()
                except queue.Empty:
                    error = None
                if error:
                    raise RuntimeError(error)
                if session.stop.is_set() or clock() - start >= duration:
                    return
                try:
                    pending_result = session.results.get_nowait()
                except queue.Empty:
                    sleep(0.01)
            if session.stop.is_set() or clock() - start >= duration:
                return
        if link is not None:
            link.arm()
            link.request("SET", profile.centidegrees(profile.start), 0)
        previous_state = None
        while clock() - start < duration:
            now = clock()
            if session is None:
                # Fresh synthetic observations exercise PR #31's real controller, not raw PWM jogs.
                elapsed = now - start
                x = 70 if elapsed < duration / 3 else 20 if elapsed < duration * 2 / 3 else 45
                command = controller.update(now, (100, 100), (x, 45, x + 10, 55), now=now)
            else:
                try:
                    error = session.errors.get_nowait()
                except queue.Empty:
                    error = None
                if error:
                    raise RuntimeError(error)
                result, pending_result = pending_result, None
                if result is None:
                    try:
                        result = session.results.get_nowait()
                    except queue.Empty:
                        result = None
                if result is not None:
                    captured, shape, box = result[:3]
                    # Hardware freshness uses host dispatch time, never offline media timestamps.
                    command = controller.update(
                        captured, shape, box[:4] if box is not None else None, now=now
                    )
                    session.video_displayed = captured
                    session.display_ack.set()
                else:
                    command = controller.snapshot(now)
                if session.stop.is_set():
                    controller.stop()
                    break
            if link is not None:
                link.send(command)
            state = (command.state, round(330 - command.pan_deg * 100 / 72))
            if state != previous_state:
                print(
                    f"{state[0]} pan0_command_ticks={state[1]} (실제 위치 피드백 아님)", flush=True
                )
                previous_state = state
            sleep(max(0, 0.05 - (clock() - now)))
    finally:
        controller.stop()
        if link is not None:
            try:
                link.request("DISARM")
            except (OSError, RuntimeError) as exc:
                print(f"DISARM 미확인: {exc}", flush=True)
        if session is not None:
            session.request_stop()
            for thread in session.threads:
                thread.join(timeout=2)
            if session.alive():
                raise RuntimeError("영상 worker 종료 미확인: 성공으로 기록하지 않음")
            session.finish()
            report = session.metrics.report()
            print(report, flush=True)
            try:
                error = session.errors.get_nowait()
            except queue.Empty:
                error = None
            if error or report.get("status") == "failed":
                raise RuntimeError(error or report.get("error") or "영상 worker 실패")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hardware", action="store_true", help="명시적인 PAN0 실물 시험")
    parser.add_argument("--port")
    parser.add_argument("--min-ticks", type=int)
    parser.add_argument("--start-ticks", type=int)
    parser.add_argument("--max-ticks", type=int)
    parser.add_argument("--direction", type=int, choices=(-1, 1))
    parser.add_argument("--video", type=Path)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--duration", type=float, default=6)
    args = parser.parse_args(argv)
    values = (args.min_ticks, args.start_ticks, args.max_ticks, args.direction)
    if args.hardware and (not args.port or any(value is None for value in values)):
        parser.error("실물 시험은 포트·최소/시작/최대 틱·방향을 모두 명시해야 합니다")
    if not args.hardware and args.port:
        parser.error("--port는 --hardware와 함께 명시해야 합니다")
    if any(value is not None for value in values) and any(value is None for value in values):
        parser.error("시험 범위와 방향을 함께 지정하세요")
    if not math.isfinite(args.duration) or not 0 < args.duration <= 60:
        parser.error("시험 시간은 0초 초과 60초 이하입니다")
    if bool(args.video) != bool(args.model):
        parser.error("동영상은 --video와 --model을 함께 지정하세요")
    if args.video and (not args.video.is_file() or not args.model.is_file()):
        parser.error("기존 로컬 동영상/모델 파일이 필요합니다")
    try:
        profile = Pan0Profile(*(values if values[0] is not None else (200, 205, 210, 1)))
    except ValueError as exc:
        parser.error(str(exc))
    link = None
    result = 0
    try:
        if args.hardware:
            expected = (
                f"PAN0 {profile.minimum}/{profile.start}/{profile.maximum} {profile.direction}"
            )
            print("PAN0_TEST1 펌웨어 업로드·PCA 전원 재투입·기구 지지·시작 위치/방향을 확인하세요.")
            print("포트를 열면 Uno가 리셋됩니다. 이전 펌웨어가 설치돼 있으면 연결하지 마세요.")
            print("Ctrl+C는 DISARM 요청이며 물리적 비상정지가 아닙니다. 전원 차단을 준비하세요.")
            if input(f"0번만 시험하려면 '{expected}' 입력: ").strip() != expected:
                raise RuntimeError("운영자 확인 취소: ARM하지 않음")
            import serial

            link = Pan0Serial(
                serial.Serial(args.port, 115200, timeout=0, write_timeout=0.1), profile
            )
            link.connect()
        else:
            print("MOCK ONLY: Serial을 열지 않습니다. 200/205/210은 모의 기본값입니다.")
        run(profile, link=link, video=args.video, model=args.model, duration=args.duration)
    except (OSError, RuntimeError, ValueError, KeyboardInterrupt, EOFError) as exc:
        print(f"시험 종료: {exc}")
        result = 1
    finally:
        if link is not None:
            try:
                link.close()
            except (OSError, RuntimeError) as exc:
                print(f"DISARM 응답 미확인: {exc}; 펌웨어 watchdog/현장 전원 차단 필요")
                result = 1
    return result


if __name__ == "__main__":
    raise SystemExit(main())
