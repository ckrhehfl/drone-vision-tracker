"""Camera GUI controls for explicit motor connection and commissioning."""

from tools.vision_motors import MotorProfile, MotorWorker, can_arm


class MotorPanel:
    def __init__(self, parent, get_session):
        import tkinter as tk
        from tkinter import ttk

        self.get_session, self.worker = get_session, None
        self.port = tk.StringVar()
        self.tilt_min, self.tilt_max = tk.StringVar(value="80"), tk.StringVar(value="330")
        self.tilt_rate = tk.StringVar(value="5")
        self.direction = tk.StringVar(value="틱 증가 → 위")
        self.power_off, self.ready_to_move = tk.BooleanVar(), tk.BooleanVar()
        self.status = tk.StringVar(value="모터 미연결 · 기본 영상 기능 사용 가능")
        self.frame = ttk.LabelFrame(parent, text="카메라 모터", padding=12)
        self.frame.columnconfigure(0, weight=1)
        ttk.Label(self.frame, text="0번 좌우 · 1번 상하 / 기본 80~330틱", wraplength=290).grid(
            row=0, sticky="w", pady=(0, 8)
        )
        ports = ttk.Frame(self.frame)
        ports.grid(row=1, sticky="ew")
        ports.columnconfigure(0, weight=1)
        self.port_select = ttk.Combobox(ports, textvariable=self.port, width=20)
        self.port_select.grid(row=0, column=0, sticky="ew")
        self.refresh_button = ttk.Button(ports, text="포트 찾기", command=self.refresh)
        self.refresh_button.grid(row=0, column=1, padx=(4, 0))
        settings = ttk.Frame(self.frame)
        settings.grid(row=2, sticky="ew", pady=8)
        self.settings = [(self.port_select, "normal")]
        for row, (label, variable, values) in enumerate(
            (
                ("1번 하한 틱", self.tilt_min, None),
                ("1번 상한 틱", self.tilt_max, None),
                ("1번 시험 속도 틱/초", self.tilt_rate, None),
                ("1번 방향", self.direction, ("틱 증가 → 위", "틱 증가 → 아래")),
            )
        ):
            ttk.Label(settings, text=label).grid(row=row, column=0, sticky="w", pady=3)
            widget = (
                ttk.Combobox(
                    settings, textvariable=variable, values=values, state="readonly", width=16
                )
                if values
                else ttk.Entry(settings, textvariable=variable, width=10)
            )
            widget.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=3)
            self.settings.append((widget, "readonly" if values else "normal"))
        self.off_check = ttk.Checkbutton(
            self.frame,
            text="전용 펌웨어·기구 지지 확인\n서보 V+ OFF · USB 유지",
            variable=self.power_off,
        )
        self.off_check.grid(row=3, sticky="w")
        self.connect_button = ttk.Button(self.frame, text="모터 연결 확인", command=self.connect)
        self.connect_button.grid(row=4, sticky="ew", pady=5)
        ttk.Label(
            self.frame,
            text="연결 확인 후 V+를 켜면 마지막 명령 위치로\n"
            "움직일 수 있습니다. 먼저 정착을 확인하세요.",
            style="Muted.TLabel",
            wraplength=290,
        ).grid(row=5, sticky="w", pady=4)
        self.ready_check = ttk.Checkbutton(
            self.frame,
            text="V+ ON 후 정착 확인\n두 축 운용 범위·방향 확인",
            variable=self.ready_to_move,
        )
        self.ready_check.grid(row=6, sticky="w")
        self.arm_button = ttk.Button(
            self.frame, text="카메라 추종 구동 허가", command=self.arm, state="disabled"
        )
        self.arm_button.grid(row=7, sticky="ew", pady=5)
        self.stop_button = ttk.Button(
            self.frame, text="모터 구동 해제·연결 종료", command=self.stop, state="disabled"
        )
        self.stop_button.grid(row=8, sticky="ew", pady=5)
        ttk.Label(self.frame, textvariable=self.status, wraplength=290).grid(row=9, sticky="w")
        ttk.Label(
            self.frame,
            text="구동은 실시간 YOLO 카메라에서만 허용합니다.\n"
            "틱은 명령값이며 구동 해제는 전원 차단이 아닙니다.",
            style="Muted.TLabel",
            wraplength=290,
        ).grid(row=10, sticky="w", pady=(8, 0))

    def refresh(self):
        from tkinter import messagebox

        try:
            from serial.tools import list_ports

            self.port_select.configure(values=tuple(p.device for p in list_ports.comports()))
        except (ImportError, OSError) as exc:
            messagebox.showerror("포트 목록 확인", str(exc))

    def connect(self):
        from tkinter import messagebox

        try:
            if self.alive():
                return
            if not self.power_off.get() or not self.port.get().strip():
                raise ValueError("포트를 선택하고 전용 펌웨어·기구 지지·V+ OFF를 확인하세요.")
            if self.direction.get() not in ("틱 증가 → 위", "틱 증가 → 아래"):
                raise ValueError("1번 축의 확인한 방향을 선택하세요.")
            profile = MotorProfile(
                int(self.tilt_min.get()),
                int(self.tilt_max.get()),
                1 if self.direction.get() == "틱 증가 → 위" else -1,
                float(self.tilt_rate.get()),
            )
            self.worker = MotorWorker(self.port.get().strip(), profile)
            self.ready_to_move.set(False)
            self.power_off.set(False)
            self.worker.start()
        except (OSError, ValueError) as exc:
            messagebox.showerror("모터 연결 확인", str(exc))

    def arm(self):
        from tkinter import messagebox

        try:
            if self.worker is None or not self.ready_to_move.get():
                raise ValueError("전원 인가 후 정착·운용 범위·방향을 확인하세요.")
            self.worker.arm(self.get_session())
            self.ready_to_move.set(False)
        except ValueError as exc:
            messagebox.showerror("모터 구동 확인", str(exc))

    def stop(self):
        if self.worker is not None:
            self.worker.stop()

    def alive(self):
        return self.worker is not None and self.worker.alive()

    def poll(self):
        alive = self.alive()
        state = self.worker.state if self.worker is not None else "CLOSED"
        may_arm = state == "READY" and can_arm(self.get_session())
        view_key = (
            (alive, state, may_arm, self.worker.error, self.worker.ticks)
            if self.worker
            else (False,)
        )
        if view_key == getattr(self, "_view_key", None):
            return
        self._view_key = view_key
        self.connect_button.configure(state="disabled" if alive else "normal")
        self.refresh_button.configure(state="disabled" if alive else "normal")
        self.off_check.configure(state="disabled" if alive else "normal")
        self.ready_check.configure(state="normal" if state == "READY" else "disabled")
        self.arm_button.configure(state="normal" if may_arm else "disabled")
        self.stop_button.configure(state="normal" if alive else "disabled")
        for widget, normal in self.settings:
            widget.configure(state="disabled" if alive else normal)
        if self.worker is not None:
            labels = {
                "CONNECTING": "연결 확인 중 · 구동 잠금",
                "READY": "연결 확인 완료 · 구동 잠금",
                "ARMING": "구동 허가 확인 중",
                "ARMED": "카메라 추종 구동 중",
                "CLOSED": "연결 종료",
                "FAULT": "모터 오류 · 구동 잠금",
            }
            text = labels[state]
            if self.worker.error:
                text += f" · {self.worker.error}"
            if self.worker.ticks:
                pan, tilt = self.worker.ticks
                text += f" · 마지막 출력 명령 0번 {pan} / 1번 {tilt}틱"
            self.status.set(text)
