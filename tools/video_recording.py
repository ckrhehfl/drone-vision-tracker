"""Capture training clips locally. Only the display copy is resized."""

import math
import threading
import time
from contextlib import ExitStack
from pathlib import Path
from tempfile import NamedTemporaryFile

DEFAULT_RECORDING_FPS = 30


class CameraRecording:
    def __init__(self, source, fps=DEFAULT_RECORDING_FPS):
        self.source = source
        self.fps = fps
        self.stop = threading.Event()
        self.frame = None
        self.destination = self.saved = None
        self.error = None
        self.started_at = self.stopped_at = None
        self.thread = threading.Thread(target=self.run, name="training-video-capture", daemon=True)

    def request_stop(self):
        if not self.stop.is_set():
            self.stopped_at = time.monotonic()
            self.stop.set()

    def run(self):
        try:
            import cv2

            with ExitStack() as files:
                capture = files.enter_context(self.source)
                writer, previous, written = None, None, 0
                with ExitStack() as encoder:
                    while not self.stop.is_set():
                        try:
                            ok, raw = capture.read()
                        except cv2.error:
                            ok = False
                        if self.stop.is_set():
                            break
                        if not ok:
                            self.error = "카메라 영상이 끊겼습니다."
                            break
                        frame = cv2.flip(raw, 0)
                        self.frame = frame
                        if self.destination is None:
                            continue
                        now = time.monotonic()
                        height, width = frame.shape[:2]
                        if writer is None:
                            size = (width, height)
                            if width % 2 or height % 2:
                                raise ValueError(
                                    "MP4 녹화에는 가로·세로가 짝수인 해상도가 필요합니다."
                                )
                            with NamedTemporaryFile(
                                dir=self.destination.parent,
                                prefix=f"{self.destination.stem[:24]}-",
                                suffix=".mp4",
                                delete=False,
                            ) as reserved:
                                clip = Path(reserved.name)
                            cleanup = files.enter_context(ExitStack())
                            cleanup.callback(clip.unlink, missing_ok=True)
                            writer = cv2.VideoWriter(
                                str(clip), cv2.VideoWriter_fourcc(*"mp4v"), self.fps, size
                            )
                            encoder.callback(writer.release)
                            if not writer.isOpened():
                                raise OSError(
                                    "MP4 녹화를 시작할 수 없습니다. 저장 위치를 확인하세요."
                                )
                            self.started_at = now
                        if (width, height) != size:
                            self.error = "촬영 중 카메라 해상도가 바뀌었습니다."
                            break
                        # Keep camera time even when its delivered FPS differs from the MP4 rate.
                        due = int((now - self.started_at) * self.fps) + 1
                        if due - written > 2 * self.fps:
                            self.error = "카메라 입력이 2초 이상 지연되어 녹화를 마쳤습니다."
                            break
                        if due > written:
                            for _ in range(written + 1, due):
                                writer.write(previous)
                            writer.write(frame)
                            written = due
                        previous = frame
                    if written and self.stopped_at is not None and self.error is None:
                        end = math.ceil((self.stopped_at - self.started_at) * self.fps)
                        if end - written > 2 * self.fps:
                            self.error = "정지 전에 카메라 입력이 2초 이상 지연됐습니다."
                        else:
                            for _ in range(written, end):
                                writer.write(previous)
                                written += 1
                if written:
                    check = cv2.VideoCapture(str(clip))
                    try:
                        if (
                            not check.isOpened()
                            or check.get(cv2.CAP_PROP_FRAME_COUNT) != written
                            or not check.set(cv2.CAP_PROP_POS_FRAMES, written - 1)
                            or not check.grab()
                        ):
                            raise OSError("저장한 MP4의 마지막 프레임을 확인할 수 없습니다.")
                    finally:
                        check.release()
                    try:
                        # Publishing must never replace a file that appeared during recording.
                        self.destination.hardlink_to(clip)
                    except OSError:
                        cleanup.pop_all()
                        self.saved = clip
                    else:
                        self.saved = self.destination
                elif self.destination is not None and self.error is None:
                    self.error = "저장할 프레임이 없습니다."
        except Exception as exc:
            self.error = str(exc)


class RecordingPage:
    def __init__(self, parent, app, camera_input):
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        self.camera_input = camera_input
        self.worker = self.last_clip = self.photo = self.displayed = None
        self.waiting_for_camera = False
        self.next_preview = 0.0
        self.state = tk.StringVar(value="●  대기")
        self.notice = tk.StringVar(value="미리보기와 MP4에 같은 상하 반전을 적용합니다.")
        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(1, weight=1)
        self.frame.rowconfigure(0, weight=1)
        controls = ttk.Frame(self.frame, style="Card.TFrame", padding=16)
        controls.grid(row=0, column=0, sticky="new", padx=(0, 16))
        ttk.Label(controls, text="촬영 카메라", style="Section.TLabel").pack(anchor="w")
        self.camera = ttk.Spinbox(controls, textvariable=app.camera, width=24, wrap=True)
        self.camera.pack(fill="x", pady=(10, 4))
        self.refresh = ttk.Button(controls, text="목록 새로고침", command=app.refresh_cameras)
        self.refresh.pack(fill="x")
        ttk.Label(controls, text="카메라 입력 모드").pack(anchor="w", pady=(10, 4))
        self.mode_select = app.camera_mode_selector(controls)
        self.mode_select.pack(fill="x")
        self.preview_button = ttk.Button(controls, text="미리보기", command=self.preview)
        self.preview_button.pack(fill="x", pady=(16, 4))
        self.record_button = ttk.Button(
            controls, text="녹화 시작", style="Primary.TButton", command=self.record
        )
        self.record_button.pack(fill="x", pady=4)
        self.stop_button = ttk.Button(controls, text="정지 · 저장", command=self.stop)
        self.stop_button.pack(fill="x", pady=4)
        ttk.Label(
            controls,
            text="화면을 이동하거나 앱을 닫아도\n촬영한 영상은 저장됩니다.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(6, 14))
        self.import_button = ttk.Button(
            controls, text="촬영 영상 학습용으로 가져오기", command=self.import_clip
        )
        self.import_button.pack(fill="x")
        self.view = tk.Label(
            self.frame,
            text="카메라 미리보기",
            background="#101d30",
            foreground="#d9e5f4",
            width=1,
            height=1,
            font=("Arial", 16, "bold"),
        )
        self.view.grid(row=0, column=1, sticky="nsew")

    def update_controls(self):
        worker, app = self.worker, self.app
        active = worker is not None and not worker.stop.is_set()
        idle = app.input_idle and not app.scanning_cameras
        ready = idle and bool(app.cameras)
        self.camera.configure(values=tuple(app.cameras), state="readonly" if ready else "disabled")
        self.mode_select.configure(state="readonly" if ready else "disabled")
        self.refresh.configure(state="normal" if idle else "disabled")
        can_handoff = (
            app.session is not None
            and not app.session.stop.is_set()
            and bool(app.cameras)
            and not app.closing
        )
        self.preview_button.configure(
            state="normal" if ready or can_handoff else "disabled",
            text="추적 정지 후 미리보기" if app.session is not None else "미리보기",
        )
        can_record = active and worker.frame is not None and worker.destination is None
        self.record_button.configure(state="normal" if can_record else "disabled")
        self.stop_button.configure(state="normal" if active else "disabled")
        self.import_button.configure(
            state="normal" if app.input_idle and self.last_clip else "disabled"
        )

    def preview(self):
        from tkinter import messagebox

        app = self.app
        if app.session is not None and not app.closing:
            self.waiting_for_camera = True
            self.state.set("●  추적 종료 중")
            self.notice.set("입력이 해제되면 촬영 미리보기가 시작됩니다.")
            app.stop()
            self.update_controls()
            return
        if not app.input_idle or app.scanning_cameras:
            return
        if app.training_window is not None and app.training_window.process is not None:
            messagebox.showerror(
                "학습 작업 실행 중", "학습 작업을 마친 뒤 촬영하세요.", parent=self.frame
            )
            return
        device = app.cameras.get(app.camera.get())
        if device is None:
            return
        try:
            resolution, fps = app.selected_camera_mode(default_fps=DEFAULT_RECORDING_FPS)
        except ValueError as exc:
            messagebox.showerror("설정 확인", str(exc), parent=self.frame)
            return
        self.worker = CameraRecording(self.camera_input(device, resolution, fps), fps)
        self.displayed = None
        self.state.set("●  카메라 연결 중")
        self.notice.set("카메라 영상을 기다리고 있습니다.")
        self.worker.thread.start()
        app.update_input_controls()

    def record(self):
        from tkinter import filedialog, messagebox

        worker = self.worker
        if worker is None or worker.stop.is_set() or worker.frame is None or worker.destination:
            return
        path = filedialog.asksaveasfilename(
            parent=self.frame,
            title="학습용 촬영 영상 저장",
            defaultextension=".mp4",
            confirmoverwrite=False,
            filetypes=[("MP4 동영상", "*.mp4")],
            initialfile=f"촬영-{time.strftime('%Y%m%d-%H%M%S')}.mp4",
        )
        if (
            not path
            or worker is not self.worker
            or worker.stop.is_set()
            or not worker.thread.is_alive()
        ):
            return
        destination = Path(path)
        if destination.suffix.lower() != ".mp4":
            messagebox.showerror(
                "동영상 저장", "파일 이름의 확장자를 .mp4로 지정하세요.", parent=self.frame
            )
            return
        worker.destination = destination
        self.last_clip = None
        self.state.set("●  녹화 준비 중")
        self.notice.set(str(destination))
        self.update_controls()

    def stop(self):
        if self.waiting_for_camera:
            self.waiting_for_camera = False
            self.state.set("●  대기")
            self.notice.set("미리보기와 MP4에 같은 상하 반전을 적용합니다.")
        if self.worker is not None and not self.worker.stop.is_set():
            self.worker.request_stop()
            self.state.set("●  저장 중" if self.worker.destination else "●  미리보기 종료 중")
            self.update_controls()

    def import_clip(self):
        if self.last_clip and self.app.input_idle:
            self.app.import_video(self.last_clip, recorded_flip_vertical=True)

    def poll(self):
        if self.waiting_for_camera and self.app.session is None:
            self.waiting_for_camera = False
            self.preview()
        worker = self.worker
        if worker is None:
            return
        if not worker.thread.is_alive():
            self.worker = self.displayed = self.photo = None
            self.view.configure(image="", text="카메라 미리보기")
            if worker.saved:
                self.last_clip = worker.saved
            self.state.set("●  저장 완료" if worker.saved else "●  대기")
            self.notice.set(str(worker.saved) if worker.saved else "미리보기가 종료됐습니다.")
            self.app.update_input_controls()
            alternate_name = worker.saved and worker.saved != worker.destination
            if worker.error or alternate_name:
                from tkinter import messagebox

                if worker.error:
                    self.state.set("●  일부 저장" if worker.saved else "●  촬영 오류")
                message = (
                    worker.error
                    or "선택한 이름을 쓸 수 없어 같은 폴더에 다른 이름으로 저장했습니다."
                )
                if worker.saved:
                    message += f"\n저장한 영상 · {worker.saved}"
                self.notice.set(message)
                messagebox.showwarning("동영상 촬영", message, parent=self.app.root)
            return
        now = time.monotonic()
        frame = worker.frame
        if (
            worker.stop.is_set()
            or frame is None
            or frame is self.displayed
            or now < self.next_preview
        ):
            return
        import cv2
        from PIL import Image, ImageTk

        h, w = frame.shape[:2]
        if self.displayed is None:
            self.state.set("●  미리보기")
            self.notice.set(f"실제 입력 {w}×{h} · MP4 {worker.fps}fps · 녹화 시작을 누르세요.")
            self.update_controls()
        self.displayed, self.next_preview = frame, now + 1 / 24
        scale = min(self.view.winfo_width() / w, self.view.winfo_height() / h, 1)
        image = cv2.resize(frame, (max(1, int(w * scale)), max(1, int(h * scale))))
        self.photo = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB)))
        self.view.configure(image=self.photo)
        if worker.started_at is not None:
            elapsed = int(now - worker.started_at)
            label = f"●  녹화 중  {elapsed // 60:02d}:{elapsed % 60:02d}"
            if self.state.get() != label:
                self.state.set(label)
