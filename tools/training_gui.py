"""GUI for reviewed manual labels, using the existing local retraining tools."""

import argparse
import codecs
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import yaml

from tools.label_editor import LabelDocument
from tools.retrain import read_json
from tools.training_data import SPLITS, check_dataset

ROOT = Path(__file__).resolve().parents[1]
STAGES = {"prepare": "데이터 준비", "approve": "데이터 검수", "train": "모델 학습"}


def label_source(folder, split, capture_id):
    if split not in SPLITS or not capture_id.strip():
        raise ValueError("분할과 촬영 그룹 이름을 지정하세요.")
    document = LabelDocument(folder)
    if document.annotation["status"] != "reviewed":
        raise ValueError("라벨 편집기에서 모든 이미지를 확인하고 검수 완료 저장을 누르세요.")
    source = json.loads(document.source_bytes)
    entry = {
        "split": split,
        "capture_id": capture_id.strip(),
        "source_type": "video" if source["source_type"] == "video_frames" else "photos",
        "samples": str(document.samples_path),
        "annotations": str(document.path),
    }
    if entry["source_type"] == "photos":
        record = document.root / "measurement.json"
        if not record.is_file():
            raise ValueError("카메라 수집 폴더의 measurement.json이 필요합니다.")
        entry["record"] = str(record)
    return entry


def reviewed_plan(sources):
    if {s["split"] for s in sources} != set(SPLITS):
        raise ValueError("독립적인 train / val / test 촬영 폴더를 모두 추가하세요.")
    seen, groups = set(), {}
    for source in sources:
        path = source["samples"]
        group, split = source["capture_id"], source["split"]
        if path in seen or group in groups and groups[group] != split:
            raise ValueError("중복 폴더 또는 여러 분할에 배정한 같은 촬영 그룹이 있습니다.")
        seen.add(path)
        groups[group] = split
    return dict(
        schema_version=1,
        status="reviewed",
        split_rule="capture_group_before_case_selection",
        sources=sources,
    )


def approve_dataset(folder):
    folder = Path(folder).resolve()
    manifest = folder / "manifest.json"
    if manifest.is_symlink():
        raise ValueError("manifest.json의 심볼릭 링크는 지원하지 않습니다.")
    before = manifest.read_bytes()
    spec, summary = check_dataset(folder / "dataset.yaml", manifest, require_ready=False)
    if Path(spec["path"]) != folder:
        raise ValueError("데이터 경로가 준비한 폴더 밖으로 변경됐습니다.")
    value = json.loads(before)
    value["status"] = "ready"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=folder, prefix=".manifest-", delete=False) as f:
            temporary = Path(f.name)
            f.write(json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        if manifest.is_symlink() or manifest.read_bytes() != before:
            raise ValueError("검사 중 manifest가 변경됐습니다. 다시 검수하세요.")
        os.replace(temporary, manifest)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return summary


def training_command(dataset, model, device, epochs, imgsz, batch, output):
    model = Path(model).expanduser().resolve()
    if not model.is_file() or model.suffix != ".pt":
        raise ValueError("신뢰하는 로컬 .pt 가중치를 선택하세요.")
    try:
        epochs, imgsz, batch = int(epochs), int(imgsz), int(batch)
    except ValueError as exc:
        raise ValueError("학습 횟수·이미지 크기·배치는 정수여야 합니다.") from exc
    if epochs < 1 or batch < 1 or imgsz < 32 or imgsz % 32:
        raise ValueError("횟수·배치는 양수, 이미지 크기는 32의 양의 배수여야 합니다.")
    if device not in ("cuda", "mps", "cpu", "auto"):
        raise ValueError("CUDA / MPS / CPU / auto 중에서 선택하세요.")
    return [
        sys.executable,
        "-u",
        "-m",
        "tools.train",
        "--data",
        str(dataset / "dataset.yaml"),
        "--manifest",
        str(dataset / "manifest.json"),
        "--model",
        str(model),
        "--device",
        device,
        "--epochs",
        str(epochs),
        "--imgsz",
        str(imgsz),
        "--batch",
        str(batch),
        "--output",
        str(output),
    ]


def open_directory(path):
    if sys.platform == "win32":
        os.startfile(path)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


class TrainingWindow:
    def __init__(self, parent, can_run=lambda: True):
        import tkinter as tk
        from tkinter import ttk

        self.can_run = can_run
        self.sources, self.editable = [], []
        self.workspace = self.dataset = self.result = None
        self.process = self.log_reader = self.log_writer = None
        self.approved = self.cancelled = self.closing = False
        self.sequence, self.stage = 0, None
        self.window = tk.Toplevel(parent)
        self.window.title("수동 라벨로 모델 학습")
        self.window.geometry("1160x820")
        self.window.minsize(1000, 740)
        self.window.configure(background="#f7f9fc")
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=1)
        self.folder = tk.StringVar()
        self.split = tk.StringVar(value="train")
        self.capture = tk.StringVar()
        self.parent_folder = tk.StringVar()
        self.version = tk.StringVar(value="manual-label-v1")
        self.groups_reviewed = tk.BooleanVar()
        self.data_reviewed = tk.BooleanVar()
        self.model = tk.StringVar()
        self.device = tk.StringVar(value="mps" if sys.platform == "darwin" else "cuda")
        self.epochs, self.imgsz, self.batch = (tk.StringVar(value=v) for v in ("100", "640", "4"))
        self.status = tk.StringVar(value="검수 완료한 라벨 폴더와 독립 촬영 그룹을 추가하세요.")
        self.data_info = tk.StringVar(value="데이터 준비 → 복사본 검수 → 모델 학습 순서입니다.")
        tk.Label(
            self.window,
            text="수동 라벨로 모델 학습",
            background="#182c43",
            foreground="white",
            font=("Arial", 21, "bold"),
            anchor="w",
            padx=22,
            pady=18,
        ).grid(row=0, sticky="ew")
        host = ttk.Frame(self.window)
        host.grid(row=1, sticky="nsew", padx=18, pady=14)
        host.columnconfigure(0, weight=1)
        host.rowconfigure(0, weight=1)
        self.view = tk.Canvas(host, background="#f7f9fc", highlightthickness=0)
        self.view.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        scroll = ttk.Scrollbar(host, orient="vertical", command=self.view.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.view.configure(yscrollcommand=scroll.set)
        body = ttk.Frame(self.view)
        item = self.view.create_window(0, 0, window=body, anchor="nw")
        body.bind(
            "<Configure>", lambda event: self.view.configure(scrollregion=self.view.bbox("all"))
        )
        self.view.bind(
            "<Configure>", lambda event: self.view.itemconfigure(item, width=event.width)
        )
        for col in (0, 1):
            body.columnconfigure(col, weight=1, uniform="cards")
        data = ttk.Frame(body, style="Card.TFrame", padding=16)
        data.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        train = ttk.Frame(body, style="Card.TFrame", padding=16)
        train.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        data.columnconfigure(0, weight=1)
        train.columnconfigure(0, weight=1)
        ttk.Label(data, text="1. 라벨과 촬영 그룹", style="Section.TLabel").grid(sticky="w")
        self.path_field(data, 1, "라벨 폴더", self.folder, self.choose_source)
        fields = ttk.Frame(data, style="Card.TFrame")
        fields.grid(row=2, sticky="ew", pady=8)
        fields.columnconfigure(1, weight=1)
        ttk.Label(fields, text="분할").grid(row=0, column=0, sticky="w")
        self.control(
            ttk.Combobox(fields, textvariable=self.split, values=SPLITS, width=9, state="readonly"),
            "readonly",
        ).grid(row=1, column=0, padx=(0, 8))
        ttk.Label(fields, text="촬영 그룹 이름").grid(row=0, column=1, sticky="w")
        self.control(ttk.Entry(fields, textvariable=self.capture, width=18)).grid(
            row=1, column=1, sticky="ew"
        )
        self.control(ttk.Button(data, text="목록에 추가", command=self.add_source)).grid(
            row=3, sticky="ew"
        )
        self.tree = ttk.Treeview(
            data, columns=("split", "group", "folder"), show="headings", height=5
        )
        self.tree.grid(row=4, sticky="ew", pady=8)
        for name, title, width in (
            ("split", "분할", 55),
            ("group", "촬영 그룹", 105),
            ("folder", "폴더", 160),
        ):
            self.tree.heading(name, text=title)
            self.tree.column(name, width=width, minwidth=40)
        self.control(ttk.Button(data, text="선택 항목 삭제", command=self.remove_source)).grid(
            row=5, sticky="ew"
        )
        self.control(
            ttk.Checkbutton(
                data,
                text="같은 촬영은 같은 그룹·분할로 묶었고\ntrain/val/test의 독립성을 확인했습니다.",
                variable=self.groups_reviewed,
                command=self.review_change,
            )
        ).grid(row=6, sticky="w", pady=12)
        ttk.Label(data, text="2. 데이터 준비와 검수", style="Section.TLabel").grid(
            row=7, sticky="w"
        )
        self.path_field(
            data, 8, "새 작업을 저장할 상위 폴더", self.parent_folder, self.choose_parent
        )
        ttk.Label(data, text="데이터 버전 이름").grid(row=9, sticky="w", pady=(8, 0))
        self.control(ttk.Entry(data, textvariable=self.version)).grid(row=10, sticky="ew")
        self.prepare_button = ttk.Button(data, text="새 데이터 준비", command=self.prepare)
        self.prepare_button.grid(row=11, sticky="ew", pady=8)
        ttk.Label(data, textvariable=self.data_info, wraplength=370, style="Muted.TLabel").grid(
            row=12, sticky="w"
        )
        self.open_data_button = ttk.Button(
            data, text="준비한 데이터 폴더 열기", command=lambda: self.open_folder(self.dataset)
        )
        self.open_data_button.grid(row=13, sticky="ew", pady=8)
        self.review_check = ttk.Checkbutton(
            data,
            text="복사된 이미지·YOLO 라벨·촬영 그룹\n분할을 직접 확인했습니다.",
            variable=self.data_reviewed,
            command=self.review_change,
        )
        self.review_check.grid(row=14, sticky="w")
        self.approve_button = ttk.Button(data, text="데이터 검수 완료", command=self.approve)
        self.approve_button.grid(row=15, sticky="ew", pady=8)
        ttk.Label(train, text="3. 기존 가중치에서 추가 학습", style="Section.TLabel").grid(
            sticky="w"
        )
        self.path_field(train, 1, "신뢰하는 로컬 .pt 가중치", self.model, self.choose_model)
        for row, (label, variable) in enumerate(
            (
                ("장치", self.device),
                ("학습 횟수 (epochs)", self.epochs),
                ("이미지 크기 (imgsz)", self.imgsz),
                ("배치 (batch)", self.batch),
            ),
            2,
        ):
            ttk.Label(train, text=label).grid(row=row * 2, sticky="w", pady=(12, 2))
            widget = (
                ttk.Combobox(
                    train,
                    textvariable=variable,
                    values=("cuda", "mps", "cpu", "auto"),
                    state="readonly",
                )
                if variable is self.device
                else ttk.Entry(train, textvariable=variable)
            )
            self.control(widget, "readonly" if variable is self.device else "normal").grid(
                row=row * 2 + 1, sticky="ew"
            )
        self.start_button = ttk.Button(
            train, text="▶  학습 시작", style="Primary.TButton", command=self.start_training
        )
        self.start_button.grid(row=12, sticky="ew", pady=(20, 8))
        self.stop_button = ttk.Button(train, text="작업 중지", command=self.stop)
        self.stop_button.grid(row=13, sticky="ew", pady=4)
        self.open_result_button = ttk.Button(
            train, text="학습 결과 폴더 열기", command=lambda: self.open_folder(self.result)
        )
        self.open_result_button.grid(row=14, sticky="ew", pady=4)
        ttk.Label(
            train,
            text="지정 GPU를 사용할 수 없으면 오류로 종료합니다.\n"
            "auto만 CUDA → MPS → CPU 순으로 선택합니다.\n\n"
            "원본·이전 모델·기존 결과를 보존합니다.\n"
            "test는 학습에 넘기지 않습니다.\n"
            "완료 모델은 결과 폴더의 weights/best.pt이며\n추적 화면에서 직접 선택해 사용하세요.",
            wraplength=370,
            style="Muted.TLabel",
        ).grid(row=15, sticky="nw", pady=18)
        log_host = ttk.Frame(self.window)
        log_host.grid(row=2, sticky="ew", padx=18)
        log_host.columnconfigure(0, weight=1)
        self.log = tk.Text(
            log_host,
            height=8,
            wrap="word",
            background="#101d30",
            foreground="#d6e3f6",
            state="disabled",
        )
        self.log.grid(row=0, column=0, sticky="ew")
        ttk.Scrollbar(log_host, orient="vertical", command=self.log.yview).grid(
            row=0, column=1, sticky="ns"
        )
        self.log.configure(yscrollcommand=log_host.winfo_children()[-1].set)
        footer = ttk.Label(
            self.window,
            textvariable=self.status,
            style="Muted.TLabel",
            background="#f7f9fc",
            wraplength=1060,
        )
        footer.grid(row=3, sticky="ew", padx=18, pady=12)
        pending = [body, self.view]
        while pending:
            widget = pending.pop()
            if widget is not self.tree:
                for event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                    widget.bind(event, self.scroll)
            pending.extend(widget.winfo_children())
        self.window.protocol("WM_DELETE_WINDOW", self.request_close)
        self.refresh()
        self.poll_id = self.window.after(120, self.poll)

    def control(self, widget, state="normal"):
        self.editable.append((widget, state))
        return widget

    def path_field(self, parent, row, label, variable, command):
        from tkinter import ttk

        field = ttk.Frame(parent, style="Card.TFrame")
        field.grid(row=row, sticky="ew", pady=(10, 0))
        field.columnconfigure(0, weight=1)
        ttk.Label(field, text=label).grid(row=0, column=0, columnspan=2, sticky="w")
        self.control(ttk.Entry(field, textvariable=variable, width=20)).grid(
            row=1, column=0, sticky="ew"
        )
        self.control(ttk.Button(field, text="선택", command=command)).grid(
            row=1, column=1, padx=(8, 0)
        )

    def scroll(self, event):
        self.view.yview_scroll(-1 if event.num == 4 or event.delta > 0 else 1, "units")
        return "break"

    def error(self, exc):
        from tkinter import messagebox

        messagebox.showerror("학습 작업을 확인하세요", str(exc), parent=self.window)

    def choose_source(self):
        from tkinter import filedialog

        path = filedialog.askdirectory(parent=self.window, title="검수 완료한 수집/추출 폴더")
        if path:
            self.folder.set(path)

    def choose_parent(self):
        from tkinter import filedialog

        path = filedialog.askdirectory(parent=self.window, title="새 작업을 저장할 상위 폴더")
        if path:
            self.parent_folder.set(path)

    def choose_model(self):
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            parent=self.window,
            title="신뢰하는 로컬 .pt 모델",
            filetypes=[("PyTorch weights", "*.pt")],
        )
        if path:
            self.model.set(path)

    def invalidate(self):
        self.workspace = self.dataset = None
        self.approved = False
        self.groups_reviewed.set(False)
        self.data_reviewed.set(False)
        self.data_info.set("목록을 변경했습니다. 분할 검수 후 새 데이터로 준비하세요.")
        self.refresh()

    def add_source(self):
        if self.process is not None:
            return
        try:
            if not self.folder.get().strip():
                raise ValueError("라벨 폴더를 선택하세요.")
            entry = label_source(self.folder.get(), self.split.get(), self.capture.get())
            if any(s["samples"] == entry["samples"] for s in self.sources):
                raise ValueError("이미 추가한 폴더입니다.")
        except (OSError, ValueError) as exc:
            self.error(exc)
            return
        self.sources.append(entry)
        self.tree.insert(
            "",
            "end",
            values=(entry["split"], entry["capture_id"], str(Path(entry["samples"]).parent)),
        )
        self.capture.set("")
        self.invalidate()

    def remove_source(self):
        if self.process is not None:
            return
        selected = set(self.tree.selection())
        self.sources = [
            s
            for item, s in zip(self.tree.get_children(), self.sources, strict=True)
            if item not in selected
        ]
        for item in selected:
            self.tree.delete(item)
        self.invalidate()

    def prepare(self):
        if self.process is not None:
            return
        try:
            if not self.groups_reviewed.get():
                raise ValueError("촬영 그룹과 train/val/test 독립성을 직접 확인하고 체크하세요.")
            plan = reviewed_plan(self.sources)
            parent = Path(self.parent_folder.get()).expanduser().resolve()
            if (
                not self.parent_folder.get().strip()
                or not parent.is_dir()
                or not self.version.get().strip()
            ):
                raise ValueError("기존 상위 폴더와 데이터 버전 이름을 지정하세요.")
            self.workspace = parent / f"retrain-{uuid4().hex[:12]}"
            self.workspace.mkdir(exist_ok=False)
            plan_path = self.workspace / "plan.json"
            plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
            self.dataset, self.approved = None, False
            self.data_reviewed.set(False)
            self.data_info.set(
                str(self.workspace / "dataset") + "\n준비 완료 후 복사본을 검수하세요."
            )
            self.start_job(
                "prepare",
                [
                    sys.executable,
                    "-u",
                    "-m",
                    "tools.retrain",
                    "prepare",
                    "--plan",
                    str(plan_path),
                    "--output",
                    str(self.workspace / "dataset"),
                    "--version",
                    self.version.get().strip(),
                ],
            )
        except (OSError, ValueError) as exc:
            self.error(exc)

    def approve(self):
        if self.process is not None or self.dataset is None:
            return
        if not self.data_reviewed.get():
            self.error("준비한 데이터 폴더의 이미지·라벨·분할을 직접 확인하고 체크하세요.")
            return
        self.approved = False
        self.start_job(
            "approve",
            [sys.executable, "-u", "-m", "tools.training_gui", "--approve", str(self.dataset)],
        )

    def review_change(self):
        if not self.groups_reviewed.get() or not self.data_reviewed.get():
            self.approved = False
        self.refresh()

    def start_training(self):
        if self.process is not None or self.dataset is None or not self.approved:
            return
        try:
            output = self.workspace / f"train-{uuid4().hex[:12]}"
            command = training_command(
                self.dataset,
                self.model.get(),
                self.device.get(),
                self.epochs.get(),
                self.imgsz.get(),
                self.batch.get(),
                output,
            )
            self.pending_output = output
            self.start_job("train", command)
        except (OSError, ValueError) as exc:
            self.error(exc)

    def start_job(self, stage, command):
        if self.process is not None:
            return
        if not self.can_run():
            self.error("추적·동영상 가져오기를 정지한 뒤 학습 작업을 실행하세요.")
            self.refresh()
            return
        self.sequence += 1
        log_path = self.workspace / f"{self.sequence:03d}-{stage}.log"
        self.record_path = log_path.with_suffix(".json")
        self.job_record = dict(stage=stage, status="running", command=command, log=str(log_path))
        self.stage, self.cancelled = stage, False
        self.log_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            self.record_path.write_text(
                json.dumps(self.job_record, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self.log_writer = log_path.open("xb")
            self.log_reader = log_path.open("rb")
            env = {
                **os.environ,
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
                "PYTHONUNBUFFERED": "1",
            }
            self.process = subprocess.Popen(
                command,
                cwd=ROOT,
                stdout=self.log_writer,
                stderr=subprocess.STDOUT,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except OSError as exc:
            self.finish(-1, str(exc))
            return
        self.status.set(f"{STAGES[stage]} 실행 중 · 전체 로그: {log_path}")
        self.refresh()

    def append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text.replace("\r", "\n"))
        self.log.delete("1.0", "end-50000c")
        self.log.see("end")
        self.log.configure(state="disabled")

    def poll(self):
        if self.process is not None:
            self.append_log(self.log_decoder.decode(self.log_reader.read(65536)))
            code = self.process.poll()
            if code is None and self.cancelled and time.monotonic() - self.cancel_time > 3:
                try:
                    self.process.kill()
                except OSError as exc:
                    self.append_log(str(exc))
            if code is not None:
                position = self.log_reader.tell()
                end = self.log_reader.seek(0, os.SEEK_END)
                start = max(position, end - 65536)
                self.log_reader.seek(start)
                data = self.log_reader.read(65536)
                if start > position:
                    self.log_decoder.reset()
                    # A bounded tail may begin inside a UTF-8 character.
                    while data and 0x80 <= data[0] < 0xC0:
                        data = data[1:]
                    self.append_log("\n[중간 출력은 전체 로그 파일에서 확인하세요.]\n")
                self.append_log(self.log_decoder.decode(data, final=True))
                self.finish(code)
        if self.closing and self.process is None:
            self.window.destroy()
            return
        self.poll_id = self.window.after(120, self.poll)

    def finish(self, code, error=None):
        for stream in (self.log_reader, self.log_writer):
            if stream is not None:
                stream.close()
        self.log_reader = self.log_writer = self.process = None
        status = "cancelled" if self.cancelled else "failed" if code else "completed"
        try:
            if status == "completed":
                if self.stage == "prepare":
                    self.dataset = self.workspace / "dataset"
                    if read_json(self.dataset / "manifest.json")["status"] != "needs_review":
                        raise ValueError("준비 결과의 검수 상태를 확인하세요.")
                    self.data_info.set(
                        str(self.dataset) + "\n복사된 이미지·라벨·촬영 분할을 확인하세요."
                    )
                elif self.stage == "approve":
                    if read_json(self.dataset / "manifest.json")["status"] != "ready":
                        raise ValueError("데이터 검수 완료 기록이 없습니다.")
                    self.approved = True
                    self.data_info.set(str(self.dataset) + "\n검수 완료 · 학습 가능")
                else:
                    record = read_json(self.pending_output / "training_record.json")
                    if (
                        record["status"] != "completed"
                        or not (self.pending_output / "weights/best.pt").is_file()
                    ):
                        raise ValueError("완료 기록과 weights/best.pt를 확인할 수 없습니다.")
                    self.result = self.pending_output
        except (OSError, ValueError, KeyError) as exc:
            status, error = "failed", str(exc)
            self.approved = False
        self.job_record.update(status=status, exit_code=code, error=error)
        try:
            self.record_path.write_text(
                json.dumps(self.job_record, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            status, error = "failed", str(exc)
        if self.stage == "prepare" and status != "completed":
            self.dataset = None
            self.data_info.set("준비 미완료 · 부분 결과: " + str(self.workspace))
        outcome = {"completed": "완료", "cancelled": "중지", "failed": "실패"}[status]
        self.status.set(f"{STAGES[self.stage]} {outcome} · 결과와 로그: {self.workspace}")
        if error:
            self.append_log("\n" + error + "\n")
        self.refresh()

    def stop(self):
        if self.process is None or self.cancelled:
            return
        self.cancelled, self.cancel_time = True, time.monotonic()
        try:
            self.process.terminate()
        except OSError as exc:
            self.append_log(str(exc))
        self.status.set("작업 중지 중 · 부분 결과를 보존합니다.")
        self.refresh()

    def refresh(self):
        busy = self.process is not None
        for widget, state in self.editable:
            widget.configure(state="disabled" if busy else state)
        for widget, enabled in (
            (self.prepare_button, bool(self.sources) and not busy),
            (self.approve_button, self.dataset is not None and not busy),
            (self.review_check, self.dataset is not None and not busy),
            (self.start_button, self.approved and not busy),
            (self.stop_button, busy and not self.cancelled),
            (self.open_data_button, self.dataset is not None),
            (self.open_result_button, self.result is not None),
        ):
            widget.configure(state="normal" if enabled else "disabled")

    def open_folder(self, path):
        if path is not None:
            try:
                open_directory(path)
            except OSError as exc:
                self.error(exc)

    def request_close(self):
        from tkinter import messagebox

        if self.process is not None:
            if not self.closing and messagebox.askyesno(
                "작업 실행 중",
                "작업을 중지하고 창을 닫을까요? 부분 결과는 보존합니다.",
                parent=self.window,
            ):
                self.closing = True
                self.stop()
            return False
        self.window.after_cancel(self.poll_id)
        self.window.destroy()
        return True


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="GUI에서 확인한 데이터의 무결성 검사와 검수 상태 저장"
    )
    parser.add_argument("--approve", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(approve_dataset(args.approve), ensure_ascii=False, indent=2))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"데이터 검수 실패: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
