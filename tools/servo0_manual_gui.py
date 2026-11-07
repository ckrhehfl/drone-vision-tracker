"""Manual channel-zero controls for the servo0_manual_safe Arduino sketch."""

import secrets
import time

LEFT_TICKS = 330
RIGHT_TICKS = 80
JOG_TICKS = 10
JOG_INTERVAL = 0.25
HOLD_INTERVAL = 0.12
ACK_TIMEOUT = 0.3


def angle_to_ticks(angle):
    if not isinstance(angle, int) or not 0 <= angle <= 180:
        raise ValueError("숫자는 0~180 정수로 입력하세요.")
    return LEFT_TICKS - (angle * (LEFT_TICKS - RIGHT_TICKS) + 90) // 180


class Servo0Link:
    """One session and one pending serial command, with no queued movement."""

    sketch = "SERVO0_SAFE"

    def valid_current_ticks(self, ticks):
        return RIGHT_TICKS <= ticks <= LEFT_TICKS

    def __init__(self, device, clock=time.monotonic):
        self.device = device
        self.clock = clock
        self.started = clock()
        self.deadline = self.started + 5
        self.pending = "startup"
        self.expected_state = ""
        self.expected_ticks = None
        self.pending_sequence = 0
        self.sequence = 0
        self.session = secrets.token_hex(4).upper()
        self.last_sent = self.started
        self.ticks = None
        self.ready = False
        self.armed = False
        self.error = ""
        self.buffer = bytearray()

    def close(self):
        self.ready = False
        self.armed = False
        if self.device is not None:
            try:
                self.device.close()
            finally:
                self.device = None

    def fail(self, message):
        self.error = message
        self.close()

    def send(self, command, state, expected_ticks, value=None):
        if self.device is None or not self.ready or self.pending:
            raise ValueError("장치 준비 또는 이전 명령 응답을 기다리세요.")
        if self.sequence >= 0xFFFFFFFF:
            self.fail("명령 순서가 끝났습니다. 다시 연결하세요.")
            return False
        self.sequence += 1
        message = f"{command},{self.session},{self.sequence}"
        if value is not None:
            message += f",{value}"
        if not self.write(message):
            return False
        self.pending = command
        self.pending_sequence = self.sequence
        self.expected_state = state
        self.expected_ticks = expected_ticks
        self.deadline = self.clock() + ACK_TIMEOUT
        return True

    def write(self, message):
        data = (message + "\n").encode("ascii")
        try:
            if self.device.write(data) != len(data):
                raise OSError("시리얼 전송이 완료되지 않았습니다.")
        except (OSError, ValueError) as exc:
            self.fail(str(exc))
            return False
        self.last_sent = self.clock()
        return True

    def jog(self, direction):
        if not self.armed or direction not in (-1, 1) or self.ticks is None:
            raise ValueError("이동 방향 또는 현재 출력 명령이 올바르지 않습니다.")
        return self.send("STEP", "STEP", "current", "+" if direction == 1 else "-")

    def initialize(self, angle):
        if self.ticks is not None or self.armed:
            raise ValueError("초기 출력은 이전 PWM 명령이 없을 때만 설정합니다.")
        return self.send("INIT", "INITIALIZED", angle_to_ticks(angle), angle)

    def arm(self):
        if self.ticks is None:
            raise ValueError("먼저 초기 출력을 직접 설정하세요.")
        return self.send("ARM", "ARMED", self.ticks)

    def disarm(self):
        return self.send("DISARM", "DISARMED", "current")

    def hold(self):
        return self.send("HOLD", "HOLD", "current")

    def interrupt_motion(self):
        if self.ready and self.armed and self.device is not None:
            return self.write(f"STOP,{self.session}")
        return False

    def move_to(self, angle):
        if not self.armed:
            raise ValueError("먼저 구동 허가를 누르세요.")
        angle_to_ticks(angle)
        return self.send("SET", "TARGET", "current", angle)

    def handle_line(self, line):
        if "I2C error" in line:
            self.fail(line)
            return
        if line == f"BOOT,{self.sketch}":
            if self.pending != "startup":
                self.fail("Uno가 재시작되었습니다. 다시 연결하고 구동을 허가하세요.")
            return
        fields = line.split(",")
        if fields[0] == "READY":
            if (
                self.pending != "HELLO"
                or len(fields) != 5
                or fields[:4] != ["READY", self.sketch, self.session, "OK"]
            ):
                self.fail("안전 스케치의 준비 응답이 올바르지 않습니다.")
                return
            try:
                ticks = int(fields[4])
            except ValueError:
                self.fail("초기 PWM 응답이 올바르지 않습니다.")
                return
            if ticks != -1 and not self.valid_current_ticks(ticks):
                self.fail("초기 PWM이 80~330틱 범위를 벗어났습니다.")
                return
            self.ticks = None if ticks == -1 else ticks
            self.pending = None
            self.ready = True
        elif fields[0] in {"ACK", "ERR"}:
            if (
                len(fields) != 5
                or fields[0] != "ACK"
                or fields[1] != self.session
                or fields[2] != str(self.pending_sequence)
                or fields[3] != self.expected_state
                or not self.pending
            ):
                self.fail("스케치의 명령 응답이 올바르지 않습니다.")
                return
            try:
                ticks = int(fields[4])
            except ValueError:
                self.fail("PWM 응답이 올바르지 않습니다.")
                return
            expected = self.expected_ticks if self.expected_ticks is not None else -1
            if self.expected_ticks == "current":
                valid = self.valid_current_ticks(ticks)
            else:
                valid = ticks == expected
            if not valid:
                self.fail("스케치의 출력 응답이 요청한 값과 다릅니다.")
                return
            self.ticks = None if ticks == -1 else ticks
            self.armed = self.pending in {"ARM", "HOLD", "STEP", "SET"}
            self.pending = None

    def poll(self):
        if self.device is None:
            return
        try:
            now = self.clock()
            if self.pending and now >= self.deadline:
                self.fail("스케치 응답 시간 초과. 다시 연결하세요.")
                return
            data = self.device.read(512)
            self.buffer.extend(data)
            while b"\n" in self.buffer:
                line, _, remainder = self.buffer.partition(b"\n")
                self.buffer = bytearray(remainder)
                self.handle_line(line.rstrip(b"\r").decode("ascii", errors="replace"))
                if self.device is None:
                    return
            if len(self.buffer) > 512:
                self.fail("시리얼 응답이 너무 깁니다.")
                return
            now = self.clock()
            if self.pending == "startup" and now - self.started >= 2:
                if self.write(f"HELLO,{self.session}"):
                    self.pending = "HELLO"
                    self.deadline = self.clock() + 2
        except (OSError, ValueError) as exc:
            self.fail(str(exc))


class ManualControl:
    def __init__(self, link, clock=time.monotonic):
        self.link = link
        self.clock = clock
        self.keys = set()
        self.lock_requested = False
        self.needs_hold = False
        self.goal = None
        self.goal_unsent = False
        self.next_step = 0.0

    def arm(self):
        if not self.link.ready or self.link.pending or self.link.armed:
            return False
        self.keys.clear()
        self.lock_requested = False
        return self.link.arm()

    def stop(self):
        self.keys.clear()
        self.goal = None
        self.lock_requested = True
        self.link.interrupt_motion()

    def press(self, key):
        if self.link.armed and not self.lock_requested:
            if self.goal is not None:
                self.goal = None
                self.needs_hold = True
                self.link.interrupt_motion()
            self.keys.add(key)
            left = bool(self.keys & {"a", "left"})
            right = bool(self.keys & {"d", "right"})
            if left and right:
                self.needs_hold = True
                self.link.interrupt_motion()

    def release(self, key):
        if key in self.keys:
            self.keys.remove(key)
            self.needs_hold = True
            self.link.interrupt_motion()

    def release_all(self):
        if self.keys:
            self.keys.clear()
            self.needs_hold = True
            self.link.interrupt_motion()

    def move_to(self, angle):
        angle_to_ticks(angle)
        if not self.link.armed or self.lock_requested:
            raise ValueError("먼저 구동 허가를 누르세요.")
        if self.keys or self.goal is not None:
            self.link.interrupt_motion()
        self.keys.clear()
        self.needs_hold = False
        self.goal = angle
        self.goal_unsent = True

    def poll(self):
        self.link.poll()
        if not self.link.ready:
            self.keys.clear()
            self.goal = None
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
        left = bool(self.keys & {"a", "left"})
        right = bool(self.keys & {"d", "right"})
        direction = int(left) - int(right)
        if self.link.armed and not self.link.pending:
            now = self.clock()
            if self.needs_hold:
                self.link.hold()
                self.needs_hold = False
            elif direction and now >= self.next_step:
                self.link.jog(direction)
                self.next_step = now + JOG_INTERVAL
            elif self.goal is not None:
                if self.link.ticks == angle_to_ticks(self.goal) and not self.goal_unsent:
                    self.goal = None
                    self.link.hold()
                elif self.goal_unsent or now - self.link.last_sent >= HOLD_INTERVAL:
                    self.link.move_to(self.goal)
                    self.goal_unsent = False
            elif now - self.link.last_sent >= HOLD_INTERVAL:
                self.link.hold()


class Servo0Window:
    link_class = Servo0Link
    control_class = ManualControl
    title = "0번 모터 수동 조작"
    heading = "0번 모터 · A/← 왼쪽, D/→ 오른쪽"
    sketch_name = "servo0_manual_safe"
    pad_text = "◀ A / ←       D / → ▶\n이 영역을 선택한 뒤 키를 누르세요"
    footer = (
        "80~330틱 임시 범위 · 숫자는 명령 눈금이며 입력한 목표로 단계 이동합니다.\n"
        "통신 지연 시 키 해제·잠금 뒤에도 PWM이 잠시 바뀔 수 있으며,\n"
        "모터는 마지막 PWM 목표까지 움직일 수 있습니다."
    )

    def __init__(self, parent, *, embedded=False):
        import tkinter as tk
        from tkinter import ttk

        self.embedded = embedded
        self.closed = False
        self.window = parent.winfo_toplevel() if embedded else tk.Toplevel(parent)
        if not embedded:
            self.window.title(self.title)
            self.window.geometry("580x500")
        self.link = None
        self.control = None
        try:
            from serial.tools.list_ports import comports

            ports = [port.device for port in comports() if "usbmodem" in port.device]
        except (ImportError, OSError):
            ports = []
        self.port = tk.StringVar(value=ports[0] if len(ports) == 1 else "")
        self.number = tk.StringVar()
        self.safe_firmware = tk.BooleanVar(value=False)
        self.power_reset = tk.BooleanVar(value=False)
        self.reported_fault = False
        self.status = tk.StringVar(value="포트를 확인하고 연결을 누르세요.")
        self.notice = tk.StringVar()
        body = ttk.Frame(parent if embedded else self.window, padding=8 if embedded else 16)
        self.body = body
        body.pack(fill="both", expand=True)
        wraplength = 270 if embedded else 510
        ttk.Label(body, text=self.heading, wraplength=wraplength).pack(anchor="w")
        ttk.Checkbutton(
            body,
            text=(
                f"{self.sketch_name} 업로드 확인"
                if embedded
                else f"안전 스케치 {self.sketch_name}를 Uno에 업로드했습니다."
            ),
            variable=self.safe_firmware,
        ).pack(anchor="w")
        ttk.Checkbutton(
            body,
            text=(
                "서보 전원 분리 후 로직 재시작 확인"
                if embedded
                else "서보 전원을 끄고 Uno와 PCA9685 로직 전원을 다시 켰습니다."
            ),
            variable=self.power_reset,
        ).pack(anchor="w")
        row = ttk.Frame(body)
        row.pack(fill="x", pady=6)
        ttk.Entry(row, textvariable=self.port, width=12 if embedded else 20).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(row, text="연결", command=self.connect).pack(side="left")
        if embedded:
            row = ttk.Frame(body)
            row.pack(fill="x", pady=2)
        self.controls_row = row
        ttk.Button(row, text="초기 출력", command=self.initialize).pack(side="left")
        if embedded:
            row = ttk.Frame(body)
            row.pack(fill="x", pady=2)
        ttk.Button(row, text="구동 허가", command=self.arm).pack(side="left")
        ttk.Button(row, text="추가 명령 잠금", command=self.stop).pack(side="left")
        ttk.Label(
            body,
            text="기존 servo0_manual 스케치는 연결 시 움직일 수 있습니다. "
            "연결 전 안전 스케치를 업로드하세요.",
            wraplength=wraplength,
        ).pack(anchor="w")
        row = ttk.Frame(body)
        row.pack(fill="x", pady=8)
        ttk.Label(row, text="0번 목표 " if embedded else "목표 숫자 0~180 ").pack(side="left")
        number = ttk.Entry(row, textvariable=self.number, width=5 if embedded else 8)
        number.pack(side="left")
        number.bind("<Return>", self.move_to_number)
        ttk.Button(row, text="이동", command=self.move_to_number).pack(side="left")
        ttk.Label(body, textvariable=self.notice, wraplength=wraplength).pack(anchor="w")
        self.pad = tk.Canvas(
            body,
            width=260 if embedded else 480,
            height=90 if embedded else 110,
            background="#182c43",
            takefocus=1,
        )
        self.pad.pack(fill="x", pady=6)
        pad_label = self.pad.create_text(0, 0, text=self.pad_text, fill="white")
        self.pad.bind(
            "<Configure>",
            lambda event: self.pad.coords(pad_label, event.width / 2, event.height / 2),
        )
        self.pad.bind("<ButtonPress-1>", lambda _event: self.pad.focus_set())
        self.pad.bind("<KeyPress>", self.press)
        self.pad.bind("<KeyRelease>", self.release)
        self.pad.bind("<FocusOut>", self.release_all)
        self.focus_binding = self.window.bind("<FocusOut>", self.schedule_focus_check, add="+")
        self.escape_binding = self.window.bind("<Escape>", lambda _event: self.stop(), add="+")
        if embedded:
            ttk.Label(
                body, text="파란 영역을 클릭한 뒤 영문 키로 조작", wraplength=wraplength
            ).pack(anchor="w")
        ttk.Label(body, textvariable=self.status, wraplength=wraplength).pack(anchor="w")
        ttk.Label(
            body,
            text=self.footer,
            wraplength=wraplength,
        ).pack(anchor="w")
        if not embedded:
            self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.poll_id = self.window.after(25, self.poll)

    def connect(self):
        if self.link is not None and self.link.device is not None:
            return
        if not self.safe_firmware.get():
            self.status.set("안전 스케치 업로드를 확인한 뒤 연결하세요.")
            return
        if not self.power_reset.get():
            self.status.set("서보 전원을 끄고 Uno와 PCA9685 로직 전원을 다시 켜세요.")
            return
        try:
            from serial import Serial

            device = Serial(self.port.get().strip(), 115200, timeout=0, write_timeout=0.2)
        except ImportError:
            self.status.set(
                "pyserial이 없습니다. .venv/bin/python -m pip install pyserial 을 실행하세요."
            )
            return
        except (OSError, ValueError) as exc:
            self.status.set(f"연결 실패: {exc}")
            return
        self.link = self.link_class(device)
        self.control = self.control_class(self.link)
        self.reported_fault = False
        self.status.set("스케치 재시작 및 상태 확인 중")

    def initialize(self):
        from tkinter import simpledialog

        if self.link is None or not self.link.ready or self.link.ticks is not None:
            self.notice.set("이전 PWM이 없는 연결 상태에서 초기 출력을 설정하세요.")
            return
        angle = simpledialog.askinteger(
            "초기 출력",
            "0~180 명령값을 직접 선택하세요. 현재 위치와 다르면 즉시 움직일 수 있습니다.",
            minvalue=0,
            maxvalue=180,
            parent=self.window,
        )
        if angle is not None:
            try:
                self.link.initialize(angle)
                self.notice.set("초기 PWM 명령을 보냈습니다.")
            except ValueError as exc:
                self.notice.set(str(exc))

    def arm(self):
        try:
            if self.control is not None and self.control.arm():
                self.notice.set("")
                self.pad.focus_set()
            else:
                self.notice.set("장치 상태와 이전 명령 응답을 확인하세요.")
        except ValueError as exc:
            self.notice.set(str(exc))

    def stop(self):
        if self.control is not None:
            self.control.stop()

    def release_all(self, _event=None):
        if self.control is not None:
            self.control.release_all()

    def schedule_focus_check(self, _event):
        self.window.after_idle(self.check_focus)

    def check_focus(self):
        import tkinter as tk

        try:
            focus = self.window.focus_get()
            if focus is not None and focus.winfo_toplevel() == self.window:
                if not self.embedded:
                    return
                while focus is not None and focus is not self.body:
                    focus = focus.master
                if focus is self.body:
                    return
        except (KeyError, tk.TclError):
            pass
        self.stop()

    def press(self, event):
        key = event.keysym.lower()
        if key in {"a", "d", "left", "right"} and not event.state & 0x0C:
            if self.control is not None:
                self.control.press(key)
            return "break"

    def release(self, event):
        if self.control is not None:
            self.control.release(event.keysym.lower())

    def move_to_number(self, _event=None):
        if self.control is None or not self.link.armed or self.control.lock_requested:
            self.notice.set("연결 후 구동 허가를 누르세요.")
            return
        try:
            text = self.number.get().strip()
            if not text.isascii() or not text.isdecimal():
                raise ValueError("숫자는 0~180 정수로 입력하세요.")
            self.control.move_to(int(text))
            self.notice.set("목표 명령을 보냈습니다.")
        except ValueError as exc:
            self.notice.set(str(exc))

    def poll(self):
        if getattr(self, "closed", False):
            return
        if self.control is not None:
            self.control.poll()
            if self.link.error:
                if not self.reported_fault:
                    self.power_reset.set(False)
                    self.reported_fault = True
                self.status.set(f"잠금: {self.link.error}")
            elif self.link.ready:
                state = (
                    "구동 허가" if self.link.armed and not self.control.lock_requested else "잠금"
                )
                ticks = self.link.ticks if self.link.ticks is not None else "미설정"
                self.status.set(self.format_status(state, ticks))
        self.poll_id = self.window.after(25, self.poll)

    def format_status(self, state, ticks):
        return f"{state} · 마지막 PWM 명령 {ticks}틱"

    def close(self):
        if self.closed:
            return
        self.stop()
        if self.link is not None:
            if self.link.ready and self.link.armed and not self.link.pending:
                self.link.disarm()
            self.link.close()
        self.closed = True
        self.window.after_cancel(self.poll_id)
        self.window.unbind("<FocusOut>", self.focus_binding)
        self.window.unbind("<Escape>", self.escape_binding)
        if self.embedded:
            self.body.destroy()
        else:
            self.window.destroy()


def main():
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    panel = Servo0Window(root)
    panel.window.protocol("WM_DELETE_WINDOW", lambda: (panel.close(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
