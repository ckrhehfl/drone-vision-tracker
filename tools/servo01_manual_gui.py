"""Manual controls for the two-channel servo01_manual_safe sketch."""

import json
import os
import tempfile
from pathlib import Path

from tools.servo0_manual_gui import (
    HOLD_INTERVAL,
    LEFT_TICKS,
    RIGHT_TICKS,
    ManualControl,
    Servo0Link,
    Servo0Window,
    angle_to_ticks,
)

TILT_MIN_TICKS = 80
TILT_MAX_TICKS = 330
PAN_CENTER_TICKS = (RIGHT_TICKS + LEFT_TICKS) // 2
TILT_CENTER_TICKS = (TILT_MIN_TICKS + TILT_MAX_TICKS) // 2
HOME_PREFERENCE = Path(__file__).resolve().parents[1] / "config/local/servo01-auto-home.json"


def load_auto_home(path=HOME_PREFERENCE):
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        return isinstance(record, dict) and record.get("auto_home") is True
    except (OSError, ValueError):
        return False


def save_auto_home(enabled, path=HOME_PREFERENCE):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as file:
            temporary = Path(file.name)
            json.dump({"auto_home": enabled}, file)
            file.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class Servo01Link(Servo0Link):
    sketch = "SERVO01_PAIR"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.boot_sketch = None
        self.channel = 0
        self.pending_channel = None
        self.initialized = [False, False]
        self.channel_ticks = [None, None]

    def valid_current_ticks(self, ticks):
        channel = self.pending_channel if self.pending == "CHANNEL" else self.channel
        if channel == 1:
            return TILT_MIN_TICKS <= ticks <= TILT_MAX_TICKS
        return RIGHT_TICKS <= ticks <= LEFT_TICKS

    def select(self, channel):
        if channel not in (0, 1):
            raise ValueError("잘못된 채널입니다.")
        if self.armed and not self.initialized[channel]:
            raise ValueError("먼저 해당 모터의 초기 출력을 설정하세요.")
        self.pending_channel = channel
        expected = "current" if self.initialized[channel] else None
        return self.send("CHANNEL", "SELECTED", expected, channel)

    def initialize_tilt(self, ticks):
        if self.channel != 1 or self.ticks is not None or self.armed:
            raise ValueError("1번 모터를 잠금 상태에서 초기화하세요.")
        if not isinstance(ticks, int) or not TILT_MIN_TICKS <= ticks <= TILT_MAX_TICKS:
            raise ValueError(f"1번 초기 출력은 {TILT_MIN_TICKS}~{TILT_MAX_TICKS}틱이어야 합니다.")
        return self.send("INIT", "INITIALIZED", ticks, ticks)

    def pair(self, pan, tilt):
        if not self.armed or pan not in (-1, 0, 1) or tilt not in (-1, 0, 1):
            raise ValueError("두 축의 이동 명령이 올바르지 않습니다.")
        values = "".join(
            "+" if direction > 0 else "-" if direction < 0 else "0" for direction in (pan, tilt)
        )
        return self.send("PAIR", "PAIR", "current", values)

    def home(self):
        if self.sketch != "SERVO01_HOME" or not self.armed or not all(self.initialized):
            raise ValueError("두 축 영점 복귀를 지원하는 스케치와 초기 출력이 필요합니다.")
        return self.send("HOME", "HOME", "current")

    def write(self, message):
        last_sent = self.last_sent
        result = super().write(message)
        if message.startswith("STOP,"):
            self.last_sent = last_sent
        return result

    def interrupt_axis(self, channel):
        if self.ready and self.armed and self.device is not None:
            return self.write(f"STOP,{self.session},{channel}")
        return False

    def handle_line(self, line):
        command = self.pending
        previous_armed = self.armed
        fields = line.split(",")
        if len(fields) >= 2 and fields[1] in {"SERVO01_PAIR", "SERVO01_HOME"}:
            if fields[0] == "BOOT":
                if command != "startup":
                    self.fail("Uno가 재시작되었습니다. 다시 연결하고 구동을 허가하세요.")
                    return
                self.boot_sketch = self.sketch = fields[1]
            elif fields[0] == "READY" and command == "HELLO":
                if self.boot_sketch is not None and self.boot_sketch != fields[1]:
                    self.fail("스케치 시작 및 준비 응답이 다릅니다.")
                    return
                self.sketch = fields[1]
        if command in {"PAIR", "DISARM", "SET", "HOME"} and fields[0] == "ACK":
            if (
                len(fields) != 5
                or fields[1] != self.session
                or fields[2] != str(self.pending_sequence)
                or fields[3]
                != {"PAIR": "PAIR", "DISARM": "DISARMED", "SET": "TARGET", "HOME": "HOME"}[command]
            ):
                self.fail("두 축의 명령 응답이 올바르지 않습니다.")
                return
            try:
                values = [int(value) for value in fields[4].split(":")]
            except ValueError:
                values = []
            bounds = ((RIGHT_TICKS, LEFT_TICKS), (TILT_MIN_TICKS, TILT_MAX_TICKS))
            if len(values) != 2 or any(
                not (
                    bounds[channel][0] <= ticks <= bounds[channel][1]
                    if self.initialized[channel]
                    else ticks == -1
                )
                for channel, ticks in enumerate(values)
            ):
                self.fail("두 축의 PWM 응답이 올바르지 않습니다.")
                return
            self.channel_ticks = [None if value == -1 else value for value in values]
            self.ticks = self.channel_ticks[self.channel]
            self.armed = command != "DISARM"
            self.pending = None
            return
        super().handle_line(line)
        if self.ready and command and self.pending is None:
            if command == "CHANNEL":
                self.channel = self.pending_channel
                self.pending_channel = None
                self.armed = previous_armed
            self.initialized[self.channel] = self.ticks is not None
            self.channel_ticks[self.channel] = self.ticks


class Servo01Control(ManualControl):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reverse_tilt = False
        self.homing = False
        self.home_sent = False

    def begin_home(self):
        if not self.link.armed or not all(self.link.initialized):
            raise ValueError("먼저 두 축의 초기 출력과 구동 허가가 필요합니다.")
        self.keys.clear()
        self.goal = None
        self.link.interrupt_motion()
        self.homing, self.home_sent = True, False

    def cancel_home(self):
        if self.homing:
            self.homing = False
            self.link.interrupt_motion()

    def stop(self):
        self.homing = False
        super().stop()

    def press(self, key):
        if not self.link.armed or self.lock_requested:
            return
        if key in self.keys:
            return
        self.cancel_home()
        if self.goal is not None:
            self.goal = None
            self.link.interrupt_motion()
        self.keys.add(key)
        channel = 0 if key in {"a", "d", "left", "right"} else 1
        if self.directions()[channel] == 0:
            self.link.interrupt_axis(channel)

    def directions(self):
        pan = int(bool(self.keys & {"a", "left"})) - int(bool(self.keys & {"d", "right"}))
        tilt = int(bool(self.keys & {"w", "up"})) - int(bool(self.keys & {"s", "down"}))
        if self.reverse_tilt:
            tilt = -tilt
        return (pan if self.link.initialized[0] else 0, tilt if self.link.initialized[1] else 0)

    def release(self, key):
        if key in self.keys:
            self.keys.remove(key)
            channel = 0 if key in {"a", "d", "left", "right"} else 1
            self.link.interrupt_axis(channel)

    def release_all(self):
        if self.keys:
            self.keys.clear()
            self.link.interrupt_motion()

    def move_to(self, angle):
        if not self.link.initialized[0]:
            raise ValueError("먼저 0번 모터의 초기 출력을 설정하세요.")
        self.cancel_home()
        super().move_to(angle)

    def poll(self):
        self.link.poll()
        if not self.link.ready:
            self.keys.clear()
            self.goal = None
            self.homing = False
            self.lock_requested = False
            return
        if self.lock_requested:
            self.keys.clear()
            if not self.link.pending:
                if self.link.armed:
                    self.link.disarm()
                else:
                    self.lock_requested = False
            return
        if not self.link.armed or self.link.pending:
            return
        now = self.clock()
        if self.homing:
            if self.home_sent and self.link.channel_ticks == [PAN_CENTER_TICKS, TILT_CENTER_TICKS]:
                self.homing = False
                self.link.hold()
            elif now - self.link.last_sent >= HOLD_INTERVAL:
                self.home_sent = self.link.home()
        elif self.goal is not None:
            if self.link.channel != 0:
                self.link.select(0)
            elif self.link.ticks == angle_to_ticks(self.goal) and not self.goal_unsent:
                self.goal = None
                self.link.hold()
            elif self.goal_unsent or now - self.link.last_sent >= HOLD_INTERVAL:
                self.link.move_to(self.goal)
                self.goal_unsent = False
        elif now - self.link.last_sent >= HOLD_INTERVAL:
            self.link.pair(*self.directions())


class Servo01Window(Servo0Window):
    link_class = Servo01Link
    control_class = Servo01Control
    title = "0·1번 모터 수동 조작"
    heading = "0번: A/D, ←/→    ·    1번: W/S, ↑/↓"
    sketch_name = "servo01_manual_safe"
    pad_text = "W / ↑\nA / ←             D / →\nS / ↓"
    footer = (
        "0번·1번 80~330틱 시험 · 두 축 동시 이동 · 1번 안전 끝점 미확인\n"
        "1번의 실제 위쪽 방향을 확인하세요. 0~180 숫자는 0번 명령 눈금입니다.\n"
        "키를 놓거나 잠근 뒤에도 마지막 PWM 목표까지 움직일 수 있습니다."
    )

    def __init__(self, parent, *, embedded=False):
        import tkinter as tk
        from tkinter import ttk

        self.pending_init = None
        self.auto_start_pending = False
        self.home_flow = None
        self.auto_home_saved = load_auto_home()
        super().__init__(parent, embedded=embedded)
        if not embedded:
            self.window.geometry("760x560")
        ttk.Button(self.controls_row, text="1번 초기 출력", command=self.initialize_tilt).pack(
            side="left"
        )
        self.auto_home = tk.BooleanVar(value=self.auto_home_saved)
        ttk.Checkbutton(
            self.pad.master,
            text="영점 자동 복귀 (각 205틱)",
            variable=self.auto_home,
            command=self.toggle_auto_home,
        ).pack(anchor="w", before=self.controls_row)
        ttk.Label(
            self.pad.master,
            text="체크 즉시 · 이후 연결 때 자동 복귀 및 구동 허가\n"
            "첫 출력이나 서보 전원 연결 시 바로 움직일 수 있습니다.\n"
            "실제 축 위치는 감지하지 않습니다.",
            wraplength=270 if embedded else 510,
        ).pack(anchor="w", before=self.controls_row)
        self.reverse = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self.pad.master,
            text="1번 W/S 방향 반전",
            variable=self.reverse,
            command=self.set_reverse,
        ).pack(anchor="w")

    def set_reverse(self):
        self.stop()
        if self.control is not None:
            self.control.reverse_tilt = self.reverse.get()

    def connect(self):
        previous_link = getattr(self, "link", None)
        super().connect()
        if self.control is not None:
            self.control.reverse_tilt = self.reverse.get()
            if getattr(self, "link", None) is not previous_link:
                self.auto_start_pending = self.auto_home.get()

    def stop(self):
        self.pending_init = None
        self.auto_start_pending = False
        self.home_flow = None
        super().stop()

    def toggle_auto_home(self):
        enabled = self.auto_home.get()
        self.stop()
        try:
            save_auto_home(enabled)
        except OSError as exc:
            self.auto_home.set(self.auto_home_saved)
            self.notice.set(f"영점 설정 저장 실패 · 다음 연결에는 이전 설정 유지: {exc}")
            return
        self.auto_home_saved = enabled
        if not enabled:
            self.notice.set("영점 자동 복귀를 껐습니다.")
        elif self.link is not None and self.link.device is not None:
            if self.link.ready:
                self.start_auto_home()
            else:
                self.auto_start_pending = True
                self.notice.set("연결 준비 후 두 축을 205틱으로 보냅니다.")
        else:
            self.notice.set("연결 후 두 축을 205틱으로 보냅니다.")

    def start_auto_home(self):
        self.auto_start_pending = False
        self.pending_init = None
        self.control.stop()
        if self.link.sketch != "SERVO01_HOME":
            self.notice.set("영점 기능을 사용하려면 최신 servo01_manual_safe 스케치가 필요합니다.")
            return
        self.home_flow = "prepare"
        self.notice.set("두 축 영점 복귀 준비 중 · 각 205틱")

    def advance_auto_home(self):
        if getattr(self, "auto_start_pending", False) and self.link is not None:
            if self.link.ready:
                self.start_auto_home()
            elif self.link.device is None:
                self.auto_start_pending = False
        if getattr(self, "home_flow", None) is None:
            return
        if self.link is None or not self.link.ready:
            self.home_flow = None
            self.auto_start_pending = False
            return
        if self.control.lock_requested:
            return
        try:
            if self.home_flow == "arming" and self.link.armed:
                self.control.begin_home()
                self.home_flow = "moving"
                self.notice.set("두 축을 205틱으로 복귀 중")
            elif self.home_flow == "moving" and not self.control.homing:
                self.home_flow = None
                if self.control.goal is not None or self.control.keys:
                    return
                if self.link.armed and self.link.channel_ticks == [
                    PAN_CENTER_TICKS,
                    TILT_CENTER_TICKS,
                ]:
                    self.notice.set("두 축 205틱 명령 완료 · 실제 위치는 측정되지 않습니다.")
                else:
                    self.notice.set("영점 복귀가 중단됐습니다.")
            elif self.link.pending:
                return
            elif self.home_flow == "prepare":
                if self.link.armed:
                    self.control.stop()
                    return
                for channel in (0, 1):
                    if not self.link.initialized[channel]:
                        if self.link.channel != channel:
                            self.link.select(channel)
                        elif channel == 0:
                            self.link.initialize(90)
                        else:
                            self.link.initialize_tilt(TILT_CENTER_TICKS)
                        return
                if self.control.arm():
                    self.home_flow = "arming"
            elif self.home_flow == "arming":
                self.home_flow = None
                self.notice.set("영점 구동 허가가 취소됐습니다.")
        except ValueError as exc:
            self.stop()
            self.notice.set(str(exc))

    def begin_initialize(self, channel):
        from tkinter import simpledialog

        if getattr(self, "home_flow", None) is not None or getattr(
            self, "auto_start_pending", False
        ):
            self.notice.set("영점 복귀가 끝나거나 잠긴 뒤 초기 출력을 설정하세요.")
            return
        if (
            self.link is None
            or not self.link.ready
            or self.link.armed
            or self.link.pending
            or self.pending_init is not None
            or self.link.initialized[channel]
        ):
            self.notice.set("연결 후 잠금 상태에서 아직 설정하지 않은 모터를 선택하세요.")
            return
        if channel == 0:
            value = simpledialog.askinteger(
                "0번 초기 출력",
                "0~180 명령값을 선택하세요. 첫 출력 때 움직일 수 있습니다.",
                minvalue=0,
                maxvalue=180,
                parent=self.window,
            )
        else:
            value = simpledialog.askinteger(
                "1번 초기 출력",
                f"{TILT_MIN_TICKS}~{TILT_MAX_TICKS}틱을 선택하세요. "
                "현재 위치와 다르면 첫 출력 때 움직일 수 있습니다.",
                minvalue=TILT_MIN_TICKS,
                maxvalue=TILT_MAX_TICKS,
                parent=self.window,
            )
        if value is None:
            return
        if self.embedded:
            self.pad.focus_set()
        self.pending_init = (channel, value)
        if self.link.channel != channel:
            self.link.select(channel)
        self.notice.set("초기 출력 준비 중")

    def initialize(self):
        self.begin_initialize(0)

    def initialize_tilt(self):
        self.begin_initialize(1)

    def arm(self):
        if getattr(self, "home_flow", None) is not None or getattr(
            self, "auto_start_pending", False
        ):
            self.notice.set("영점 복귀가 끝날 때까지 기다리거나 잠그세요.")
            return
        super().arm()

    def interrupt_auto_home(self):
        if getattr(self, "home_flow", None) is None and not getattr(
            self, "auto_start_pending", False
        ):
            return False
        if self.home_flow == "moving" and self.control is not None:
            self.control.cancel_home()
            self.home_flow = None
            self.auto_start_pending = False
        else:
            self.stop()
        self.notice.set("영점 복귀를 중단했습니다.")
        return True

    def move_to_number(self, event=None):
        interrupted = self.interrupt_auto_home()
        if interrupted and (
            self.control is None or not self.link.armed or self.control.lock_requested
        ):
            self.notice.set("영점 복귀 중단 · 구동 허가 후 숫자 목표를 입력하세요.")
            return
        super().move_to_number(event)

    def press(self, event):
        key = event.keysym.lower()
        if key in {"a", "d", "left", "right", "w", "s", "up", "down"} and not event.state & 0x0C:
            if self.control is not None:
                self.interrupt_auto_home()
                channel = 0 if key in {"a", "d", "left", "right"} else 1
                if self.control.link.ready and not self.control.link.initialized[channel]:
                    button = "초기 출력" if channel == 0 else "1번 초기 출력"
                    self.notice.set(f"{channel}번 미설정 · 잠금 후 '{button}'을 설정하세요.")
                self.control.press(key)
            return "break"

    def format_status(self, state, ticks):
        values = [value if value is not None else "미설정" for value in self.link.channel_ticks]
        return f"{state} · 마지막 PWM 명령: 0번 {values[0]}틱 · 1번 {values[1]}틱"

    def poll(self):
        super().poll()
        self.advance_auto_home()
        if getattr(self, "home_flow", None) is not None:
            return
        if self.pending_init is None:
            return
        if self.link is None or not self.link.ready:
            self.pending_init = None
            return
        if self.link.pending:
            return
        channel, value = self.pending_init
        self.pending_init = None
        if self.link.channel != channel or self.link.armed:
            self.notice.set("초기 출력 설정이 취소됐습니다.")
            return
        try:
            if channel == 0:
                self.link.initialize(value)
            else:
                self.link.initialize_tilt(value)
            self.notice.set(f"{channel}번 초기 PWM 명령을 보냈습니다.")
        except ValueError as exc:
            self.notice.set(str(exc))


def main():
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    panel = Servo01Window(root)
    panel.window.protocol("WM_DELETE_WINDOW", lambda: (panel.close(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
