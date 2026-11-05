"""Explicit operator-controlled channel-zero test; never opens a port on import."""

import secrets
import threading
import time


def default_port():
    from serial.tools.list_ports import comports

    ports = [port.device for port in comports() if port.device.startswith("/dev/cu.usbmodem")]
    return ports[0] if len(ports) == 1 else ""


def number_to_ticks(text):
    if not (text.isascii() and text.isdecimal() and len(text) <= 3 and int(text) <= 360):
        raise ValueError("0~360 사이의 정수를 입력하세요.")
    return 205 + 205 * int(text) // 360


class PanLink:
    """One serial owner, one command awaiting ACK, no reconnect or queued movement."""

    STARTUP_S = 2

    def __init__(self, port, serial_factory=None):
        self.port = port
        self.serial_factory = serial_factory
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.closed = threading.Event()
        self.direction = 0
        self.target = None
        self.bounds = None
        self.lease = 0.0
        self.request = None
        self.state = "연결 중"
        self.ticks = -1
        self.armed = False
        self.connected = False
        self.session = secrets.token_hex(4).upper()
        self.seq = 0
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def snapshot(self):
        with self.lock:
            return self.state, self.ticks, self.armed, self.connected

    def operator(self, command, value=None):
        with self.lock:
            self.direction = 0
            self.target = None
            self.request = command, value
        self.wake.set()

    def input(self, direction):
        if direction not in (-1, 0, 1):
            raise ValueError("invalid direction")
        with self.lock:
            changed = direction != self.direction or self.target is not None
            self.direction = direction
            self.target = None
            self.lease = time.monotonic()
        if changed:
            self.wake.set()

    def move_to(self, ticks):
        with self.lock:
            if not self.armed or self.request is not None or self.bounds is None:
                raise ValueError("먼저 구동 허가를 누르세요.")
            low, high = self.bounds
            if not low <= ticks <= high:
                raise ValueError(f"입력은 {ticks}틱입니다. 허가한 범위는 {low}~{high}틱입니다.")
            self.direction = 0
            self.target = ticks
            self.lease = time.monotonic()
        self.wake.set()

    def heartbeat(self):
        with self.lock:
            self.lease = time.monotonic()

    def close(self):
        self.closed.set()
        self.wake.set()

    def exchange(self, device, command, value=None):
        self.seq += 1
        message = f"{command},{self.session},{self.seq}"
        if value is not None:
            message += f",{value}"
        data = (message + "\n").encode("ascii")
        if device.write(data) != len(data):
            raise RuntimeError("시리얼 전송 실패")
        fields = self.read_line(device, time.monotonic() + 0.18).split(",")
        if len(fields) != 5 or fields[:3] != ["ACK", self.session, str(self.seq)]:
            raise RuntimeError("응답 오류: " + ",".join(fields))
        expected = {
            "ARM": "ARMED",
            "DISARM": "DISARMED",
            "INIT": "INITIALIZED",
            "HOLD": "HOLD",
            "MOVE": "MOVING",
            "GOTO": "TARGET",
        }[command]
        ticks = int(fields[4])
        if fields[3] != expected or not (
            205 <= ticks <= 410 or command == "DISARM" and ticks == -1
        ):
            raise RuntimeError("잘못된 장치 응답")
        with self.lock:
            self.ticks = ticks
            self.armed = command in ("ARM", "MOVE", "HOLD", "GOTO")
            if command == "ARM":
                self.bounds = tuple(map(int, value.split(":")))
            elif not self.armed:
                self.bounds = self.target = None
            if command == "GOTO" and ticks == value == self.target:
                self.target = None
            self.state = fields[3]

    def read_line(self, device, deadline):
        data = bytearray()
        while not self.closed.is_set() and time.monotonic() < deadline:
            chunk = device.read(1)
            if not chunk:
                continue
            if chunk == b"\n":
                return data.rstrip(b"\r").decode("ascii")
            data.extend(chunk)
            if len(data) > 96:
                raise RuntimeError("장치 응답 길이 초과")
        raise RuntimeError("장치 응답 시간 초과")

    def run(self):
        device = None
        try:
            factory = self.serial_factory
            if factory is None:
                from serial import Serial

                factory = Serial
            device = factory(self.port, 115200, timeout=0.02, write_timeout=0.15)
            if self.closed.wait(self.STARTUP_S):
                return
            device.reset_input_buffer()
            device.write(f"HELLO,{self.session}\n".encode("ascii"))
            fields = self.read_line(device, time.monotonic() + 1).split(",")
            if len(fields) != 5 or fields[:4] != ["READY", "PANKEY2", self.session, "OK"]:
                raise RuntimeError("숫자 입력을 지원하는 0번 시험 펌웨어가 준비되지 않았습니다")
            ticks = int(fields[4])
            if ticks != -1 and not 205 <= ticks <= 410:
                raise RuntimeError("장치 초기 명령값 오류")
            with self.lock:
                self.ticks = ticks
                self.connected = True
                self.state = "연결됨 · 구동 잠금"
            while not self.closed.is_set():
                self.wake.clear()
                with self.lock:
                    request, self.request = self.request, None
                    direction = self.direction
                    target = self.target
                    stale = time.monotonic() - self.lease > 0.15
                    armed = self.armed
                if request is not None:
                    self.exchange(device, *request)
                elif armed:
                    if stale:
                        self.exchange(device, "DISARM")
                    elif target is not None:
                        self.exchange(device, "GOTO", target)
                    else:
                        self.exchange(
                            device,
                            "MOVE" if direction else "HOLD",
                            direction if direction else None,
                        )
                self.wake.wait(0.1)
        except Exception as exc:
            with self.lock:
                self.state = str(exc)
        finally:
            if device is not None:
                # Best effort only; firmware watchdog remains the independent stop.
                try:
                    self.seq += 1
                    device.write(f"DISARM,{self.session},{self.seq}\n".encode("ascii"))
                except Exception:
                    pass
                finally:
                    try:
                        device.close()
                    except OSError:
                        pass
            with self.lock:
                self.armed = self.connected = False
                self.direction = 0
                self.bounds = self.target = None


class PanControlWindow:
    def __init__(self, parent):
        import tkinter as tk
        from tkinter import ttk

        self.window = tk.Toplevel(parent)
        self.window.title("0번 모터 수동 시험")
        self.window.geometry("540x510")
        self.link = None
        self.keys = set()
        self.was_armed = False
        self.bounds_loaded = False
        self.port = tk.StringVar(value=default_port())
        self.low = tk.StringVar()
        self.high = tk.StringVar()
        self.status = tk.StringVar(value="연결 후 기구 지지를 확인하고 구동 허가를 누르세요")
        self.number = tk.StringVar()
        self.number_status = tk.StringVar(value="0~360 명령 눈금 · 실제 각도 아님")
        self.reverse = tk.BooleanVar(value=False)
        body = ttk.Frame(self.window, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="0번 축 · A/← 왼쪽, D/→ 오른쪽", font=("Arial", 16, "bold")).pack()
        row = ttk.Frame(body)
        row.pack(fill="x", pady=12)
        ttk.Entry(row, textvariable=self.port).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="연결", command=self.connect).pack(side="left")
        row = ttk.Frame(body)
        row.pack(fill="x")
        self.init_button = ttk.Button(
            row, text="초기 출력 설정", command=self.initialize, state="disabled"
        )
        self.init_button.pack(side="left")
        self.arm_button = ttk.Button(row, text="구동 허가", command=self.arm, state="disabled")
        self.arm_button.pack(side="left")
        ttk.Button(row, text="정지·잠금", command=self.disarm).pack(side="left")
        bounds = ttk.Frame(body)
        bounds.pack(fill="x", pady=6)
        ttk.Label(bounds, text="확인한 시험 범위(틱) ").pack(side="left")
        ttk.Entry(bounds, textvariable=self.low, width=6).pack(side="left")
        ttk.Label(bounds, text=" ~ ").pack(side="left")
        ttk.Entry(bounds, textvariable=self.high, width=6).pack(side="left")
        ttk.Checkbutton(
            body, text="좌우 방향 반전", variable=self.reverse, command=self.disarm
        ).pack(anchor="w", pady=6)
        position = ttk.Frame(body)
        position.pack(fill="x")
        ttk.Label(position, text="목표 숫자(0~360) ").pack(side="left")
        number_input = ttk.Entry(position, textvariable=self.number, width=8)
        number_input.pack(side="left")
        number_input.bind("<Return>", self.move_to_number)
        self.number_button = ttk.Button(
            position, text="이동", command=self.move_to_number, state="disabled"
        )
        self.number_button.pack(side="left")
        ttk.Label(body, textvariable=self.number_status, wraplength=490).pack(anchor="w")
        self.pad = tk.Canvas(body, height=125, background="#182c43", takefocus=1)
        self.pad.pack(fill="x", pady=8)
        self.pad.create_text(
            245,
            60,
            text="◀ A / ←       D / → ▶\n이 영역을 선택한 뒤 키를 누르세요",
            fill="white",
            font=("Arial", 15),
            justify="center",
        )
        self.pad.bind("<ButtonPress-1>", lambda _e: self.pad.focus_set())
        self.pad.bind("<KeyPress>", self.press)
        self.pad.bind("<KeyRelease>", self.release)
        self.pad.bind("<FocusOut>", self.stop_keys)
        self.window.bind("<FocusOut>", self.schedule_focus_check)
        self.window.bind("<Escape>", lambda _e: self.disarm())
        ttk.Label(body, textvariable=self.status, wraplength=490).pack(anchor="w")
        ttk.Label(
            body,
            text="시험 PWM 205~410틱(약 1~2ms) · 실제 각도 미측정\n"
            "키 해제는 명령 유지이며 전원 차단이 아닙니다.",
            wraplength=490,
        ).pack(anchor="w", pady=8)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.after(50, self.poll)

    def connect(self):
        if self.link is not None and self.link.thread.is_alive():
            return
        self.link = PanLink(self.port.get().strip())
        self.bounds_loaded = False
        self.keys.clear()
        self.link.start()

    def command(self, command, value=None):
        if self.link is not None and self.link.snapshot()[3]:
            self.link.operator(command, value)

    def initialize(self):
        from tkinter import simpledialog

        if self.link is None or self.link.snapshot()[1] != -1:
            return
        ticks = simpledialog.askinteger(
            "초기 출력",
            "직접 확인한 시작 PWM 값을 입력하세요(205~410틱).\n"
            "현재 위치와 다르면 움직일 수 있으니 기구를 지지하세요.",
            parent=self.window,
            minvalue=205,
            maxvalue=410,
        )
        if ticks is not None:
            self.command("INIT", ticks)

    def arm(self):
        from tkinter import messagebox

        if self.link is None or not self.link.snapshot()[3]:
            return
        ticks = self.link.snapshot()[1]
        try:
            low, high = int(self.low.get()), int(self.high.get())
            if not (205 <= low < high <= 410 and low <= ticks <= high):
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "시험 범위",
                "현재 출력 명령을 포함한 205~410틱 범위를 입력하세요.",
                parent=self.window,
            )
            return
        self.keys.clear()
        self.command("ARM", f"{low}:{high}")

    def disarm(self):
        self.keys.clear()
        self.command("DISARM")

    def move_to_number(self, _event=None):
        if self.link is None:
            self.number_status.set("먼저 연결하고 구동 허가를 누르세요.")
            return
        try:
            ticks = number_to_ticks(self.number.get().strip())
            self.link.move_to(ticks)
        except ValueError as exc:
            self.keys.clear()
            self.link.input(0)
            self.number_status.set(str(exc))
            return
        self.keys.clear()
        self.pad.focus_set()
        self.number_status.set(f"목표 출력 {ticks}틱 · Esc로 정지·잠금")

    def stop_keys(self, _event):
        if self.keys:
            self.keys.clear()
            self.update_input()

    def has_window_focus(self):
        focus = self.window.focus_get()
        return focus is not None and focus.winfo_toplevel() == self.window

    def schedule_focus_check(self, _event):
        self.window.after_idle(self.check_focus)

    def check_focus(self):
        if self.link is not None and self.link.snapshot()[2] and not self.has_window_focus():
            self.disarm()

    def press(self, event):
        key = event.keysym.lower()
        if key in ("a", "d", "left", "right") and not event.state & 0x0C:
            self.keys.add(key)
            self.update_input()
            return "break"

    def release(self, event):
        key = event.keysym.lower()
        if key in self.keys:
            self.keys.remove(key)
            self.update_input()

    def update_input(self):
        right = bool(self.keys & {"d", "right"})
        left = bool(self.keys & {"a", "left"})
        direction = (int(right) - int(left)) * (-1 if self.reverse.get() else 1)
        if self.link is not None:
            self.link.input(direction)

    def poll(self):
        if self.link is not None:
            self.link.heartbeat()
            state, ticks, armed, connected = self.link.snapshot()
            self.init_button.configure(state="normal" if connected and ticks == -1 else "disabled")
            self.arm_button.configure(
                state="normal" if connected and not armed and ticks >= 205 else "disabled"
            )
            self.number_button.configure(state="normal" if connected and armed else "disabled")
            if not self.bounds_loaded and 205 <= ticks <= 410:
                self.low.set(str(max(205, ticks - 5)))
                self.high.set(str(min(410, ticks + 5)))
                self.bounds_loaded = True
            self.status.set(f"{state} · 출력 명령 {ticks if ticks >= 0 else '미설정'}틱")
            if armed and not self.has_window_focus():
                self.disarm()
            elif armed and not self.was_armed:
                self.keys.clear()
                self.pad.focus_set()
            self.was_armed = armed
        self.window.after(50, self.poll)

    def close(self):
        if self.link is not None:
            self.link.close()
        self.window.destroy()


def main():
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    panel = PanControlWindow(root)
    panel.window.protocol("WM_DELETE_WINDOW", lambda: (panel.close(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
