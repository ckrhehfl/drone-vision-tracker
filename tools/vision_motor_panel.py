"""Camera GUI controls for explicit motor connection and commissioning."""

import sys

from tools.vision_motors import MAX_TICKS, MIN_TICKS, MotorProfile, MotorWorker, can_arm

AUTO_BOTH = "0·1번 자동 추종"
AUTO_TILT = "1번 상하 자동 · 0번 명령 유지"
MANUAL_TILT = "1번 키보드 수동 · 0번 명령 유지"
AUTO_CONFIRMATION = "V+ ON 후 정착 확인\n두 축 운용 범위·방향 확인"
TILT_CONFIRMATION = "V+ ON 후 정착 확인\n0번 고정·1번 운용 범위·방향 확인"
MANUAL_CONFIRMATION = "V+ ON 후 정착 확인\n수동 시험 범위·회전 경로 확인"
TILT_MODE_DEFAULTS = {
    AUTO_TILT: (str(MIN_TICKS), str(MAX_TICKS), "2", "틱 증가 → 위"),
    MANUAL_TILT: ("200", "210", "2", "틱 증가 → 위"),
}


def _held_windows_jog_keys():
    if sys.platform != "win32":
        return ()
    from ctypes import c_int, c_short, windll

    physical_state = windll.user32.GetAsyncKeyState
    queued_state = windll.user32.GetKeyState
    for get_state in (physical_state, queued_state):
        get_state.argtypes = (c_int,)
        get_state.restype = c_short
    # The GUI thread may still see a held key while its release is queued.
    return tuple(
        key
        for key, virtual_key in (("Up", 0x26), ("Down", 0x28))
        if (physical_state(virtual_key) | queued_state(virtual_key)) & 0x8000
    )


class MotorPanel:
    def __init__(self, parent, get_session, is_closing):
        import tkinter as tk
        from tkinter import ttk

        self.get_session, self.worker = get_session, None
        self.is_closing = is_closing
        self.port = tk.StringVar()
        self.tilt_min, self.tilt_max = tk.StringVar(value="80"), tk.StringVar(value="330")
        self.tilt_rate = tk.StringVar(value="5")
        self.direction = tk.StringVar(value="틱 증가 → 위")
        self.mode = tk.StringVar(value=AUTO_BOTH)
        self._selected_mode = AUTO_BOTH
        self._mode_settings = {}
        self._pressed_keys = set()
        self._release_jobs = {}
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
        # Tk's native macOS dropdown can keep the other controls in a menu event loop.
        self.port_select = ttk.Spinbox(
            ports, textvariable=self.port, values=("",), width=20, wrap=True
        )
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
                ("1번 원본 기준 방향", self.direction, ("틱 증가 → 위", "틱 증가 → 아래")),
            )
        ):
            ttk.Label(settings, text=label).grid(row=row, column=0, sticky="w", pady=3)
            widget = (
                ttk.Spinbox(
                    settings,
                    textvariable=variable,
                    values=values,
                    state="readonly",
                    width=16,
                    wrap=True,
                )
                if values
                else ttk.Entry(settings, textvariable=variable, width=10)
            )
            widget.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=3)
            self.settings.append((widget, "readonly" if values else "normal"))
        self.mode_select = ttk.Spinbox(
            settings,
            textvariable=self.mode,
            values=(AUTO_BOTH, AUTO_TILT, MANUAL_TILT),
            state="readonly",
            wrap=True,
            command=self.select_mode,
        )
        self.mode_select.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.settings.append((self.mode_select, "readonly"))
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
            text=AUTO_CONFIRMATION,
            variable=self.ready_to_move,
        )
        self.ready_check.grid(row=6, sticky="w")
        self.arm_button = ttk.Button(
            self.frame, text="카메라 모터 구동 허가", command=self.arm, state="disabled"
        )
        self.arm_button.grid(row=7, sticky="ew", pady=5)
        self.jog_button = ttk.Button(
            self.frame, text="키보드 조작 ↑ +1틱 / ↓ −1틱 · Esc 종료", state="disabled"
        )
        self.jog_button.configure(command=self.jog_button.focus_set)
        self.jog_button.grid(row=8, sticky="ew", pady=5)
        for key in ("Up", "Down"):
            self.jog_button.bind(f"<KeyPress-{key}>", self.jog_key)
            self.jog_button.bind(f"<KeyRelease-{key}>", self.release_key)
        self.frame.winfo_toplevel().bind("<Escape>", self.exit_jog, add="+")
        self.jog_button.bind("<FocusOut>", self.exit_jog)
        parent.bind("<Unmap>", self.exit_jog, add="+")
        self.stop_button = ttk.Button(
            self.frame, text="모터 구동 해제·연결 종료", command=self.stop, state="disabled"
        )
        self.stop_button.grid(row=9, sticky="ew", pady=5)
        ttk.Label(self.frame, textvariable=self.status, wraplength=290).grid(row=10, sticky="w")
        ttk.Label(
            self.frame,
            text="수동 지정으로 자동 추종하려면 1번 상하 자동을 선택하고 대상을 드래그하세요.\n"
            "틱은 명령값이며 구동 해제는 전원 차단이 아닙니다.",
            style="Muted.TLabel",
            wraplength=290,
        ).grid(row=11, sticky="w", pady=(8, 0))

    def refresh(self):
        if self.is_closing():
            return
        from tkinter import messagebox

        try:
            from serial.tools import list_ports

            ports = tuple(port.device for port in list_ports.comports())
            self.port_select.configure(values=ports or (self.port.get(),))
        except (ImportError, OSError) as exc:
            messagebox.showerror("포트 목록 확인", str(exc))

    def select_mode(self):
        mode = self.mode.get()
        if mode == self._selected_mode:
            return
        settings = (self.tilt_min, self.tilt_max, self.tilt_rate, self.direction)
        if self._selected_mode != MANUAL_TILT:
            self._mode_settings[self._selected_mode] = tuple(setting.get() for setting in settings)
        self._selected_mode = mode
        confirmations = {
            AUTO_BOTH: AUTO_CONFIRMATION,
            AUTO_TILT: TILT_CONFIRMATION,
            MANUAL_TILT: MANUAL_CONFIRMATION,
        }
        self.ready_check.configure(text=confirmations[mode])
        values = self._mode_settings.get(mode) or TILT_MODE_DEFAULTS[mode]
        for setting, value in zip(settings, values, strict=True):
            setting.set(value)

    def connect(self):
        if self.is_closing() or self.alive():
            return
        from tkinter import messagebox

        try:
            if not self.power_off.get() or not self.port.get().strip():
                raise ValueError("포트를 선택하고 전용 펌웨어·기구 지지·V+ OFF를 확인하세요.")
            if self.direction.get() not in ("틱 증가 → 위", "틱 증가 → 아래"):
                raise ValueError("1번 축의 확인한 방향을 선택하세요.")
            mode = self.mode.get()
            if mode not in (AUTO_BOTH, AUTO_TILT, MANUAL_TILT):
                raise ValueError("카메라 모터 조작 모드를 선택하세요.")
            session = self.get_session()
            if session is not None:
                if session.video_path is not None:
                    raise ValueError("실시간 카메라에서 모터를 연결하세요.")
                if mode == AUTO_BOTH and not session.model_path:
                    raise ValueError(
                        "수동 지정에는 1번 상하 자동 또는 1번 키보드 수동을 선택하세요."
                    )
            profile = MotorProfile(
                int(self.tilt_min.get()),
                int(self.tilt_max.get()),
                1 if self.direction.get() == "틱 증가 → 위" else -1,
                float(self.tilt_rate.get()),
                tilt_only=mode == AUTO_TILT,
                manual_tilt=mode == MANUAL_TILT,
            )
            self.worker = MotorWorker(self.port.get().strip(), profile)
            self._view_key = None
            self.clear_jog_keys()
            self.ready_to_move.set(False)
            self.power_off.set(False)
            self.set_profile_locked(True)
            self.worker.start()
        except (OSError, ValueError) as exc:
            messagebox.showerror("모터 연결 확인", str(exc))

    def arm(self):
        if self.is_closing():
            return
        from tkinter import messagebox

        try:
            if self.worker is None or not self.ready_to_move.get():
                raise ValueError("전원 인가 후 정착·운용 범위·방향을 확인하세요.")
            held_keys = ()
            if self.worker.profile.manual_tilt:
                self.jog_button.configure(state="normal")
                self.jog_button.focus_set()
                if (
                    not self.jog_button.winfo_viewable()
                    or self.jog_button.focus_get() != self.jog_button
                ):
                    raise ValueError("카메라 창을 활성화한 뒤 구동을 허가하세요.")
                held_keys = _held_windows_jog_keys()
            self.worker.arm(self.get_session())
            self._pressed_keys.update(held_keys)
            self.ready_to_move.set(False)
        except (OSError, ValueError) as exc:
            self.jog_button.configure(state="disabled")
            messagebox.showerror("모터 구동 확인", str(exc))

    def stop(self):
        self.clear_jog_keys()
        if self.worker is not None:
            self.worker.stop()

    def stop_tilt_tracking(self):
        if self.alive() and self.worker.profile.tilt_only:
            self.ready_to_move.set(False)
            self.stop()

    def clear_jog_keys(self):
        for job in self._release_jobs.values():
            self.jog_button.after_cancel(job)
        self._release_jobs.clear()
        self._pressed_keys.clear()

    def jog_key(self, event):
        if self.is_closing():
            return "break"
        if not self.jog_button.winfo_viewable():
            self.exit_jog(event)
            return "break"
        key = event.keysym
        pending_release = self._release_jobs.pop(key, None)
        delta = {"Up": 1, "Down": -1}.get(key)
        if pending_release is not None:
            self.jog_button.after_cancel(pending_release)
            self._pressed_keys.add(key)
        elif delta is not None and self.worker is not None and key not in self._pressed_keys:
            self._pressed_keys.add(key)
            self.worker.jog(delta)
        return "break"

    def release_key(self, event):
        key = event.keysym
        if key not in self._release_jobs:
            # Adjacent release and press events can repeat a key held before this button had focus.
            self._release_jobs[key] = self.jog_button.after_idle(self.clear_key, key)
        return "break"

    def clear_key(self, key):
        self._release_jobs.pop(key, None)
        self._pressed_keys.discard(key)

    def exit_jog(self, _event):
        if (
            self.worker is not None
            and self.worker.state in ("ARMING", "ARMED")
            and self.worker.profile.manual_tilt
        ):
            self.stop()
            return "break"
        self.clear_jog_keys()
        return None

    def alive(self):
        return self.worker is not None and self.worker.alive()

    def set_profile_locked(self, locked):
        for widget, normal in self.settings:
            widget.configure(state="disabled" if locked else normal)

    def poll(self):
        alive = self.alive()
        closing = self.is_closing()
        locked = alive or closing
        state = self.worker.state if self.worker is not None else "CLOSED"
        session = self.get_session()
        needs_selection = (
            state in ("READY", "ARMED")
            and session is not None
            and not session.model_path
            and self.worker.profile.tilt_only
            and session.manual_target_lost
        )
        may_arm = (
            not closing
            and state == "READY"
            and not self.worker.stopping.is_set()
            and can_arm(session, self.worker.profile)
        )
        jog_active = state in ("ARMING", "ARMED") and self.worker.profile.manual_tilt
        if jog_active and not self.jog_button.winfo_viewable():
            self.exit_jog(None)
        view_key = (
            (closing, alive, state, may_arm, needs_selection, self.worker.error, self.worker.ticks)
            if self.worker
            else (closing,)
        )
        if view_key == getattr(self, "_view_key", None):
            return
        self._view_key = view_key
        self.connect_button.configure(state="disabled" if locked else "normal")
        self.refresh_button.configure(state="disabled" if locked else "normal")
        self.off_check.configure(state="disabled" if locked else "normal")
        self.ready_check.configure(
            state="normal" if state == "READY" and not closing else "disabled"
        )
        self.arm_button.configure(state="normal" if may_arm else "disabled")
        self.jog_button.configure(state="normal" if jog_active and not closing else "disabled")
        self.stop_button.configure(state="normal" if alive else "disabled")
        self.set_profile_locked(locked)
        if self.worker is not None:
            labels = {
                "CONNECTING": "연결 확인 중 · 구동 잠금",
                "READY": "연결 확인 완료 · 구동 잠금",
                "ARMING": "구동 허가 확인 중",
                "ARMED": "1번 키보드 구동 중" if jog_active else "카메라 추종 구동 중",
                "CLOSED": "연결 종료",
                "FAULT": "모터 오류 · 구동 잠금",
            }
            text = labels[state]
            if needs_selection:
                text += " · 대상을 다시 지정하세요"
            if self.worker.error:
                text += f" · {self.worker.error}"
            if self.worker.ticks:
                pan, tilt = self.worker.ticks
                text += f" · 마지막 출력 명령 0번 {pan} / 1번 {tilt}틱"
            self.status.set(text)
