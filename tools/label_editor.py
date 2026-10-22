"""Manual single-drone annotations for existing camera/video frame collections."""

import copy
import hashlib
import io
import json
import math
import os
import sys
import tempfile
from pathlib import Path

from tools.tracking_metrics import validate_box
from tools.training_data import is_sha256
from tools.validate_config import strict_json

UNLABELED = "needs_label"


class LabelDocument:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.samples_path = self.root / "samples.json"
        self.path = self.root / "annotations.json"
        if self.samples_path.is_symlink() or self.path.is_symlink():
            raise ValueError("수집·라벨 JSON의 심볼릭 링크는 지원하지 않습니다.")
        self.source_bytes = self.samples_path.read_bytes()
        source = strict_json(self.source_bytes.decode("utf-8"))
        if (
            not isinstance(source, dict)
            or type(source.get("schema_version")) is not int
            or source["schema_version"] != 1
            or not isinstance(source.get("session_id"), str)
            or not source["session_id"].strip()
            or source.get("status") != "completed"
            or source.get("source_type") not in ("camera_stills", "video_frames")
            or source.get("error") is not None
            or not isinstance(source.get("samples"), list)
            or not source["samples"]
        ):
            raise ValueError("정상 완료된 수집·영상 추출 폴더의 samples.json이 필요합니다.")
        self.samples = source["samples"]
        seen = set()
        for sample in self.samples:
            if (
                not isinstance(sample, dict)
                or type(sample.get("frame_id")) is not int
                or sample["frame_id"] <= 0
                or sample["frame_id"] in seen
                or any(
                    type(sample.get(key)) is not int or sample[key] <= 0
                    for key in ("width", "height")
                )
                or type(sample.get("timestamp_s")) not in (int, float)
                or not math.isfinite(sample["timestamp_s"])
                or sample["timestamp_s"] < 0
                or not is_sha256(sample.get("sha256"))
                or sample.get("image_path") != f"frames/frame-{sample['frame_id']:06d}.png"
            ):
                raise ValueError("수집 프레임 ID·크기·시각·해시·경로를 확인하세요.")
            seen.add(sample["frame_id"])
        self.saved_bytes = self.path.read_bytes() if self.path.exists() else None
        self.annotation = (
            strict_json(self.saved_bytes.decode("utf-8"))
            if self.saved_bytes is not None
            else {
                "schema_version": 1,
                "session_id": source["session_id"],
                "status": "needs_review",
                "target_class": "drone",
                "frames": [{"frame_id": s["frame_id"], "box": UNLABELED} for s in self.samples],
            }
        )
        a = self.annotation
        if (
            not isinstance(a, dict)
            or type(a.get("schema_version")) is not int
            or a["schema_version"] != 1
            or a.get("session_id") != source["session_id"]
            or a.get("target_class") != "drone"
            or a.get("status") not in ("needs_review", "reviewed")
            or not isinstance(a.get("frames"), list)
        ):
            raise ValueError("기존 annotations.json의 버전·세션·드론 클래스·상태를 확인하세요.")
        by_id = {}
        for row in a["frames"]:
            if (
                not isinstance(row, dict)
                or type(row.get("frame_id")) is not int
                or row["frame_id"] in by_id
                or "box" not in row
            ):
                raise ValueError("기존 라벨의 프레임 ID가 잘못됐거나 중복됐습니다.")
            by_id[row["frame_id"]] = row
        if set(by_id) != seen:
            raise ValueError("라벨과 수집 이미지의 프레임 목록이 정확히 일치해야 합니다.")
        self.rows = [by_id[s["frame_id"]] for s in self.samples]
        for sample, row in zip(self.samples, self.rows, strict=True):
            if row["box"] != UNLABELED:
                validate_box(row["box"], sample["width"], sample["height"])
        if a["status"] == "reviewed" and self.remaining:
            raise ValueError("검수 완료 파일에 미라벨 이미지가 있습니다.")
        self.dirty = False
        self.history = []

    @property
    def remaining(self):
        return sum(row["box"] == UNLABELED for row in self.rows)

    def image_bytes(self, index):
        if self.samples_path.read_bytes() != self.source_bytes:
            raise ValueError("수집 기록이 변경됐습니다. 폴더를 다시 여세요.")
        sample = self.samples[index]
        image = (self.root / sample["image_path"]).resolve()
        if not image.is_relative_to(self.root / "frames"):
            raise ValueError("이미지 경로가 수집 폴더 밖을 가리킵니다.")
        data = image.read_bytes()
        if hashlib.sha256(data).hexdigest() != sample["sha256"]:
            raise ValueError("원본 이미지 해시가 다릅니다. 원본을 복구한 뒤 다시 여세요.")
        return data

    def set_box(self, index, box):
        sample = self.samples[index]
        if box != UNLABELED:
            validate_box(box, sample["width"], sample["height"])
        if self.rows[index]["box"] == box:
            return
        self.history.append((index, copy.deepcopy(self.rows[index]["box"])))
        self.history = self.history[-50:]
        self.rows[index]["box"] = copy.deepcopy(box)
        self.annotation["status"] = "needs_review"
        self.dirty = True

    def undo(self):
        if not self.history:
            return None
        index, box = self.history.pop()
        self.rows[index]["box"] = box
        self.annotation["status"] = "needs_review"
        self.dirty = True
        return index

    def check_unchanged(self):
        if self.samples_path.read_bytes() != self.source_bytes or self.path.is_symlink():
            raise ValueError("수집 기록 또는 라벨 경로가 변경됐습니다. 다시 열어 확인하세요.")
        current = self.path.read_bytes() if self.path.exists() else None
        if current != self.saved_bytes:
            raise ValueError("다른 프로그램이 라벨 파일을 변경했습니다. 덮어쓰지 않습니다.")

    def save(self, reviewed=False):
        self.check_unchanged()
        if reviewed and self.remaining:
            raise ValueError(f"미라벨 이미지 {self.remaining}장이 남아 있습니다.")
        if reviewed:
            from PIL import Image

            for index, sample in enumerate(self.samples):
                with Image.open(io.BytesIO(self.image_bytes(index))) as image:
                    if image.size != (sample["width"], sample["height"]):
                        raise ValueError("이미지 실제 크기가 수집 기록과 다릅니다.")
                    image.verify()
        payload = copy.deepcopy(self.annotation)
        payload["status"] = "reviewed" if reviewed else "needs_review"
        data = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.root, prefix=".annotations-", delete=False
            ) as f:
                temporary = Path(f.name)
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            self.check_unchanged()
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self.saved_bytes = data
        self.annotation["status"] = payload["status"]
        self.dirty = False


class LabelEditor:
    def __init__(self, parent):
        import tkinter as tk
        from tkinter import ttk

        self.window = tk.Toplevel(parent)
        self.window.title("수동 라벨 편집기 · 드론 비전 추적")
        self.window.geometry("1200x820")
        self.window.minsize(1000, 740)
        self.window.configure(background="#f7f9fc")
        self.document, self.image, self.photo = None, None, None
        self.index, self.scale, self.offset = 0, 1.0, (0, 0)
        self.gesture = None
        self.folder = tk.StringVar(value="수집·영상 추출 폴더를 선택하세요")
        self.progress = tk.StringVar(value="이미지 없음")
        self.label_state = tk.StringVar(value="미라벨")
        self.coordinates = tk.StringVar(value="원본 좌표: —")
        self.position = tk.StringVar(value="1")
        self.status = tk.StringVar(value="드론 박스 1개 또는 드론 없음(배경)을 직접 지정하세요.")
        header = tk.Frame(self.window, background="#182c43", padx=22, pady=18)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        tk.Label(
            header,
            text="수동 라벨 편집기",
            background="#182c43",
            foreground="white",
            font=("Arial", 22, "bold"),
        ).pack(side="left")
        tk.Label(
            header,
            text="drone · 이미지당 박스 1개",
            background="#182c43",
            foreground="#c0d0e4",
            font=("Arial", 12),
        ).pack(side="right")
        controls_host = ttk.Frame(self.window)
        controls_host.grid(row=1, column=0, sticky="ns", padx=(18, 12), pady=18)
        self.controls_view = tk.Canvas(
            controls_host, width=302, highlightthickness=0, background="white"
        )
        self.controls_view.pack(side="left", fill="both", expand=True, padx=(0, 8))
        scrollbar = ttk.Scrollbar(controls_host, command=self.controls_view.yview)
        scrollbar.pack(side="right", fill="y")
        self.controls_view.configure(yscrollcommand=scrollbar.set)
        controls = ttk.Frame(self.controls_view, style="Card.TFrame", padding=16)
        controls_window = self.controls_view.create_window(0, 0, window=controls, anchor="nw")
        controls.bind(
            "<Configure>",
            lambda event: self.controls_view.configure(
                width=controls.winfo_reqwidth(), scrollregion=self.controls_view.bbox("all")
            ),
        )
        self.controls_view.bind(
            "<Configure>",
            lambda event: self.controls_view.itemconfigure(controls_window, width=event.width),
        )
        controls.columnconfigure(0, weight=1)
        ttk.Button(controls, text="수집/추출 폴더 열기", command=self.open_folder).grid(
            row=0, sticky="ew"
        )
        ttk.Label(controls, textvariable=self.folder, wraplength=270, style="Muted.TLabel").grid(
            row=1, sticky="w", pady=(10, 16)
        )
        ttk.Label(controls, textvariable=self.progress, style="Section.TLabel").grid(
            row=2, sticky="w"
        )
        navigation = ttk.Frame(controls, style="Card.TFrame")
        navigation.grid(row=3, sticky="ew", pady=10)
        for column, (text, step) in enumerate((("‹ 이전", -1), ("다음 ›", 1))):
            navigation.columnconfigure(column, weight=1)
            ttk.Button(navigation, text=text, command=lambda step=step: self.navigate(step)).grid(
                row=0, column=column, sticky="ew", padx=2
            )
        jump = ttk.Frame(controls, style="Card.TFrame")
        jump.grid(row=4, sticky="ew")
        ttk.Entry(jump, textvariable=self.position, width=7).pack(side="left")
        ttk.Button(jump, text="이동", command=self.jump).pack(side="left", padx=6)
        ttk.Button(jump, text="미라벨 찾기", command=self.next_missing).pack(side="left")
        ttk.Label(controls, textvariable=self.label_state, style="Section.TLabel").grid(
            row=5, sticky="w", pady=(18, 8)
        )
        ttk.Label(
            controls, textvariable=self.coordinates, style="Muted.TLabel", wraplength=270
        ).grid(row=6, sticky="w")
        for row, (text, command) in enumerate(
            (
                ("드론 없음 (배경)", lambda: self.set_box(None)),
                ("라벨 지우기 (미라벨)", lambda: self.set_box(UNLABELED)),
                ("실행 취소", self.undo),
            ),
            7,
        ):
            ttk.Button(controls, text=text, command=command).grid(row=row, sticky="ew", pady=(8, 0))
        ttk.Separator(controls).grid(row=10, sticky="ew", pady=16)
        ttk.Button(controls, text="임시 저장", command=self.save).grid(row=11, sticky="ew")
        self.review_button = ttk.Button(
            controls,
            text="검수 완료 저장",
            style="Primary.TButton",
            command=lambda: self.save(reviewed=True),
            state="disabled",
        )
        self.review_button.grid(row=12, sticky="ew", pady=8)
        ttk.Label(
            controls,
            text="빈 영역 드래그: 새 박스\n박스 안 드래그: 이동\n모서리 드래그: 크기 변경\n"
            "← / →: 이미지 이동 · Ctrl/⌘+S: 임시 저장",
            style="Muted.TLabel",
            wraplength=270,
        ).grid(row=13, sticky="w", pady=(8, 0))
        self.canvas = tk.Canvas(self.window, background="#101d30", highlightthickness=0)
        self.canvas.grid(row=1, column=1, sticky="nsew", padx=(0, 18), pady=18)
        self.canvas.bind("<Configure>", self.render)
        self.canvas.bind("<ButtonPress-1>", self.begin)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.end)
        self.window.columnconfigure(1, weight=1)
        self.window.rowconfigure(1, weight=1)
        footer = ttk.Label(
            self.window,
            textvariable=self.status,
            background="#f7f9fc",
            style="Muted.TLabel",
            wraplength=1100,
        )
        footer.grid(row=2, column=0, columnspan=2, sticky="ew", padx=18, pady=(0, 14))
        self.window.bind(
            "<Configure>",
            lambda event: footer.configure(wraplength=max(200, event.width - 36))
            if event.widget is self.window
            else None,
        )
        pending = [controls_host]
        while pending:
            widget = pending.pop()
            for event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                widget.bind(event, self.scroll_controls)
            pending.extend(widget.winfo_children())
        self.window.bind("<Left>", lambda event: self.shortcut(event, lambda: self.navigate(-1)))
        self.window.bind("<Right>", lambda event: self.shortcut(event, lambda: self.navigate(1)))
        modifier = "Command" if sys.platform == "darwin" else "Control"
        self.window.bind(f"<{modifier}-s>", lambda event: self.save())
        self.window.bind(f"<{modifier}-z>", lambda event: self.shortcut(event, self.undo))
        self.window.bind("<Escape>", self.cancel_gesture)
        self.window.protocol("WM_DELETE_WINDOW", self.request_close)

    def scroll_controls(self, event):
        self.controls_view.yview_scroll(-1 if event.num == 4 or event.delta > 0 else 1, "units")
        return "break"

    def shortcut(self, event, action):
        if event.widget.winfo_class() not in ("Entry", "TEntry", "TSpinbox"):
            action()
            return "break"
        return None

    def open_folder(self):
        from tkinter import filedialog, messagebox

        folder = filedialog.askdirectory(
            parent=self.window, title="samples.json이 있는 수집/추출 폴더"
        )
        if not folder or not self.confirm_changes():
            return
        try:
            document = LabelDocument(folder)
            image = self.read_image(document, 0)
        except (OSError, ValueError) as exc:
            messagebox.showerror("라벨 폴더 열기 실패", str(exc), parent=self.window)
            return
        self.document, self.image, self.index = document, image, 0
        self.gesture = None
        self.folder.set(str(document.root))
        self.status.set("폴더 열기 완료 · 모든 이미지의 드론 또는 배경을 직접 확인하세요.")
        self.refresh()

    def read_image(self, document, index):
        from PIL import Image

        sample = document.samples[index]
        try:
            with Image.open(io.BytesIO(document.image_bytes(index))) as image:
                if image.size != (sample["width"], sample["height"]):
                    raise ValueError("이미지 실제 크기가 수집 기록과 다릅니다.")
                return image.convert("RGB")
        except Image.DecompressionBombError as exc:
            raise ValueError("안전하게 열 수 없는 이미지 크기입니다.") from exc

    def select(self, index):
        from tkinter import messagebox

        if self.document is None or not 0 <= index < len(self.document.samples):
            return
        try:
            image = self.read_image(self.document, index)
        except (OSError, ValueError) as exc:
            messagebox.showerror("이미지 열기 실패", str(exc), parent=self.window)
            return
        self.index, self.image, self.gesture = index, image, None
        self.refresh()

    def navigate(self, step):
        self.select(self.index + step)

    def jump(self):
        from tkinter import messagebox

        try:
            index = int(self.position.get()) - 1
            if self.document is None or not 0 <= index < len(self.document.samples):
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "이미지 번호", "목록 안의 이미지 번호를 입력하세요.", parent=self.window
            )
            return
        self.select(index)

    def next_missing(self):
        if self.document is None:
            return
        for step in range(1, len(self.document.rows) + 1):
            index = (self.index + step) % len(self.document.rows)
            if self.document.rows[index]["box"] == UNLABELED:
                self.select(index)
                return
        self.status.set("모든 이미지가 라벨링되었습니다. 검수 완료 저장으로 확정하세요.")

    def set_box(self, box):
        if self.document is None:
            return
        self.gesture = None
        self.document.set_box(self.index, box)
        self.refresh()

    def undo(self):
        if self.document is not None:
            index = self.document.undo()
            if index is not None:
                self.select(index)

    def refresh(self):
        if self.document is None:
            return
        d = self.document
        self.position.set(str(self.index + 1))
        self.progress.set(f"{self.index + 1} / {len(d.rows)}장 · 미라벨 {d.remaining}장")
        box = d.rows[self.index]["box"]
        self.label_state.set(
            "미라벨" if box == UNLABELED else "드론 없음 (배경)" if box is None else "drone"
        )
        self.coordinates.set(
            "원본 좌표: " + (", ".join(f"{v:g}" for v in box) if isinstance(box, list) else "—")
        )
        self.review_button.configure(state="normal" if d.remaining == 0 else "disabled")
        self.window.title(f"{'* ' if d.dirty else ''}수동 라벨 편집기 · {d.root.name}")
        self.render()

    def render(self, _event=None):
        from PIL import Image, ImageTk

        self.canvas.delete("all")
        if self.image is None:
            self.canvas.create_text(
                self.canvas.winfo_width() / 2,
                self.canvas.winfo_height() / 2,
                text="수집/추출 폴더를 열어 라벨링을 시작하세요",
                fill="#c0d0e4",
                font=("Arial", 16),
                width=max(200, self.canvas.winfo_width() - 40),
            )
            return
        width, height = self.image.size
        self.scale = min(
            max(1, self.canvas.winfo_width()) / width, max(1, self.canvas.winfo_height()) / height
        )
        size = (max(1, round(width * self.scale)), max(1, round(height * self.scale)))
        self.offset = (
            (self.canvas.winfo_width() - size[0]) / 2,
            (self.canvas.winfo_height() - size[1]) / 2,
        )
        self.photo = ImageTk.PhotoImage(self.image.resize(size, Image.Resampling.LANCZOS))
        self.canvas.create_image(*self.offset, anchor="nw", image=self.photo)
        self.draw_box()

    def draw_box(self, box=None):
        self.canvas.delete("box")
        if self.document is None:
            return
        if box is None:
            box = self.document.rows[self.index]["box"]
        if not isinstance(box, list):
            return
        x, y = self.offset
        x1, y1, x2, y2 = (box[i] * self.scale + (x if i % 2 == 0 else y) for i in range(4))
        self.canvas.create_rectangle(x1, y1, x2, y2, outline="#46df98", width=2, tags="box")
        self.canvas.create_text(
            x1 + 4,
            max(y + 2, y1 - 20),
            text="drone",
            anchor="nw",
            fill="#46df98",
            font=("Arial", 12, "bold"),
            tags="box",
        )
        for px, py in ((x1, y1), (x2, y1), (x1, y2), (x2, y2)):
            self.canvas.create_rectangle(
                px - 4, py - 4, px + 4, py + 4, fill="#46df98", outline="#101d30", tags="box"
            )

    def point(self, event, clamp=False):
        x, y = ((event.x - self.offset[0]) / self.scale, (event.y - self.offset[1]) / self.scale)
        width, height = self.image.size
        if not clamp and not (0 <= x <= width and 0 <= y <= height):
            return None
        return max(0, min(width, x)), max(0, min(height, y))

    def begin(self, event):
        if self.image is None:
            return
        point = self.point(event)
        if point is None:
            return
        box = self.document.rows[self.index]["box"]
        mode = "draw"
        corner = None
        if isinstance(box, list):
            for a, b in ((0, 1), (2, 1), (0, 3), (2, 3)):
                if max(abs(point[0] - box[a]), abs(point[1] - box[b])) * self.scale <= 8:
                    mode, corner = "resize", (a, b)
                    break
            if mode == "draw" and box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]:
                mode = "move"
        self.gesture = dict(
            start=point, original=copy.deepcopy(box), mode=mode, corner=corner, box=None
        )
        self.canvas.focus_set()

    def drag(self, event):
        if self.gesture is None:
            return
        x, y = self.point(event, clamp=True)
        g = self.gesture
        if g["mode"] == "draw":
            sx, sy = g["start"]
            box = [min(sx, x), min(sy, y), max(sx, x), max(sy, y)]
        elif g["mode"] == "resize":
            box = g["original"].copy()
            a, b = g["corner"]
            box[a], box[b] = x, y
            box = [
                min(box[0], box[2]),
                min(box[1], box[3]),
                max(box[0], box[2]),
                max(box[1], box[3]),
            ]
        else:
            before = g["original"]
            width, height = self.image.size
            dx = max(-before[0], min(width - before[2], x - g["start"][0]))
            dy = max(-before[1], min(height - before[3], y - g["start"][1]))
            box = [before[0] + dx, before[1] + dy, before[2] + dx, before[3] + dy]
        g["box"] = box
        self.draw_box(box)

    def end(self, event):
        if self.gesture is None:
            return
        self.drag(event)
        box = self.gesture["box"]
        moved = self.point(event, clamp=True) != self.gesture["start"]
        self.gesture = None
        if moved and box[2] - box[0] >= 1 and box[3] - box[1] >= 1:
            self.set_box(box)
        else:
            self.draw_box()

    def cancel_gesture(self, _event=None):
        self.gesture = None
        self.draw_box()

    def save(self, reviewed=False):
        from tkinter import messagebox

        from PIL import Image

        if self.document is None:
            return False
        self.cancel_gesture()
        self.status.set("원본 확인 및 저장 중…" if reviewed else "임시 저장 중…")
        self.window.update_idletasks()
        try:
            self.document.save(reviewed=reviewed)
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            self.status.set("저장 실패 · 편집 내용은 메모리에 유지됩니다.")
            messagebox.showerror("라벨 저장 실패", str(exc), parent=self.window)
            return False
        self.status.set(f"{'검수 완료' if reviewed else '임시'} 저장 · {self.document.path}")
        self.refresh()
        return True

    def confirm_changes(self):
        from tkinter import messagebox

        if self.document is None or not self.document.dirty:
            return True
        choice = messagebox.askyesnocancel(
            "저장하지 않은 라벨",
            "변경한 라벨을 임시 저장할까요?\n아니오: 저장하지 않고 계속 · 취소: 편집 유지",
            parent=self.window,
        )
        return self.save() if choice is True else choice is False

    def request_close(self):
        if not self.confirm_changes():
            return False
        self.window.destroy()
        return True
