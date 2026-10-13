"""Local camera tracking; saves stills only on opt-in and never opens Serial."""

import hashlib
import json
import math
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from tools.frame_collection import FrameCollector
from tools.tracking_metrics import (
    SessionMetrics,
    evaluate,
    evaluation_text,
    performance_text,
)

MAX_AGE_S = 0.5  # Preview cutoff, not the project's 100 ms control performance target.
MANUAL_MODEL = (
    Path(__file__).resolve().parents[1]
    / "models/local/vittrack/object_tracking_vittrack_2023sep.onnx"
)
MANUAL_MODEL_SHA256 = "2990f0b7cd44d92afa48cd97db6de7be113fc1d9594fddb74e2725c10478e91d"


def list_cameras():
    """Read device metadata without creating a capture session or requesting access."""
    if sys.platform != "darwin":
        return [
            {"id": str(i), "name": f"{i}번", "index": i, "builtin": False, "kind": "카메라"}
            for i in range(10)
        ]
    # Match the pinned OpenCV AVFoundation backend's video/muxed uniqueID ordering.
    script = """
ObjC.import('Foundation');
ObjC.import('AVFoundation');
var capture = $.NSClassFromString('AVCaptureDevice');
var devices = capture.devicesWithMediaType('vide')
    .arrayByAddingObjectsFromArray(capture.devicesWithMediaType('muxx'));
devices = devices.sortedArrayUsingDescriptors([
    $.NSSortDescriptor.sortDescriptorWithKeyAscending('uniqueID', true)
]);
var output = [];
for (var i = 0; i < devices.count; i++) {
    var device = devices.objectAtIndex(i);
    output.push({id: ObjC.unwrap(device.uniqueID), name: ObjC.unwrap(device.localizedName),
        transport: Number(device.transportType)});
}
JSON.stringify(output);
"""
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-l", "JavaScript", "-e", script],
            capture_output=True,
            text=True,
            check=True,
            timeout=3,
        )
        devices = json.loads(result.stdout)
        if not isinstance(devices, list) or any(
            not isinstance(d, dict)
            or any(not isinstance(d.get(key), str) or not d[key] for key in ("id", "name"))
            or type(d.get("transport")) is not int
            for d in devices
        ):
            raise ValueError("invalid camera metadata")
        for index, device in enumerate(devices):
            device["index"] = index
            device["builtin"] = device["transport"] == 0x626C746E  # 'bltn'
            device["kind"] = (
                "Mac 내장 카메라"
                if device["builtin"]
                else "USB 카메라"
                if device["transport"] == 0x75736220
                else "외장 카메라"
            )
        return devices
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise RuntimeError(
            "카메라 목록을 읽지 못했습니다. 목록 새로고침을 다시 눌러주세요."
        ) from exc


def put_latest(channel, item):
    """Single producer, single consumer: discard queued work before publishing."""
    discarded = False
    try:
        channel.get_nowait()
        discarded = True
    except queue.Empty:
        pass
    channel.put_nowait(item)
    return discarded


def selection_box(start, end, width, height):
    x1, x2 = sorted(max(0, min(width, int(x))) for x in (start[0], end[0]))
    y1, y2 = sorted(max(0, min(height, int(y))) for y in (start[1], end[1]))
    return (x1, y1, x2, y2) if x2 - x1 >= 12 and y2 - y1 >= 12 else None


def overlap(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1])
    )
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0.0


def choose_target(detections, previous):
    """Keep overlapping detections; never jump to another visible drone on loss."""
    candidates = [d for d in detections if previous is None or overlap(d[:4], previous) >= 0.1]
    return max(candidates, key=lambda d: d[4], default=None)


def template_sizes(template_shape, frame_shape):
    """Try the original size first, then 10% scale steps between 0.1x and 2x."""
    height, width = template_shape[:2]
    frame_height, frame_width = frame_shape[:2]
    sizes = dict.fromkeys(
        (round(width * scale), round(height * scale))
        for scale in (1.0, 0.1, *(1.1**step for step in range(-24, 8)), 2.0)
    )
    return [(w, h) for w, h in sizes if 8 <= w <= frame_width and 8 <= h <= frame_height]


def match_template(frame, template, threshold, *, fast=False, unique=False):
    import cv2

    best = None

    def evaluate(width, height):
        nonlocal best
        if not (8 <= width <= frame.shape[1] and 8 <= height <= frame.shape[0]):
            return
        sample = template
        if (height, width) != template.shape[:2]:
            interpolation = cv2.INTER_AREA if width < template.shape[1] else cv2.INTER_LINEAR
            sample = cv2.resize(template, (width, height), interpolation=interpolation)
        if sample.std() < 5:
            return
        scores = cv2.matchTemplate(frame, sample, cv2.TM_CCOEFF_NORMED)
        _, score, _, (x, y) = cv2.minMaxLoc(scores)
        if unique and math.isfinite(score) and score >= threshold:
            # Nearby peaks describe the same box; separated peers make recovery ambiguous.
            scores[
                max(0, y - height // 2) : y + height // 2 + 1,
                max(0, x - width // 2) : x + width // 2 + 1,
            ] = -1
            _, runner_up, _, _ = cv2.minMaxLoc(scores)
            if not math.isfinite(runner_up) or score - runner_up < 0.08:
                return
        if math.isfinite(score) and (best is None or score > best[4]):
            best = (x, y, x + width, y + height, score)

    # ponytail: scale search cannot recover missing detail or rotation; use trained YOLO.
    sizes = [template.shape[:2][::-1]] if fast else template_sizes(template.shape, frame.shape)
    for width, height in sizes:
        evaluate(width, height)
        if (height, width) == template.shape[:2] and best and best[4] >= max(0.9, threshold):
            return best
    if best is not None and not fast:
        width = best[2] - best[0]
        # Refine between coarse scales, where a small size error can spoil the match.
        radius = max(1, round(width * 0.05))
        for refined_width in range(width - radius, width + radius + 1):
            scale = refined_width / template.shape[1]
            if 0.1 <= scale <= 2.0:
                evaluate(refined_width, round(template.shape[0] * scale))
    return best if best is not None and best[4] >= threshold else None


def color_signature(image):
    import cv2

    h, w = image.shape[:2]
    inner = image[h // 5 : h - h // 5, w // 5 : w - w // 5]
    hsv = cv2.cvtColor(inner, cv2.COLOR_BGR2HSV)
    histogram = cv2.calcHist([hsv], [0, 1, 2], None, [12, 4, 4], [0, 180, 0, 256, 0, 256])
    return cv2.normalize(histogram, histogram, norm_type=cv2.NORM_L1)


def verify_appearance(frame, detection, signature):
    if detection is None:
        return None
    import cv2

    x1, y1, x2, y2 = (round(v) for v in detection[:4])
    current = color_signature(frame[y1:y2, x1:x2])
    # ponytail: color prevents drift, not identity; similar objects need a trained detector.
    distance = cv2.compareHist(
        signature.reshape(-1), current.reshape(-1), cv2.HISTCMP_BHATTACHARYYA
    )
    return detection if math.isfinite(distance) and 0 <= distance <= 0.45 else None


def color_mask(frame, signature):
    import cv2

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    # A 3D NumPy histogram can be read as a multichannel Mat; project 1D/2D marginals.
    hs = cv2.calcBackProject([hsv], [0, 1], signature.sum(axis=2), [0, 180, 0, 256], 255)
    value = cv2.calcBackProject([hsv], [2], signature.sum(axis=(0, 1)), [0, 256], 255)
    return cv2.bitwise_and(
        cv2.threshold(hs, 1, 255, cv2.THRESH_BINARY)[1],
        cv2.threshold(value, 1, 255, cv2.THRESH_BINARY)[1],
    )


def match_features(frame, template, threshold, mask):
    """Recover rotated/scaled appearances only with distributed geometric agreement."""
    import cv2

    if min(template.shape[:2]) < 32:
        return None
    orb = cv2.ORB_create(nfeatures=1600, edgeThreshold=15, fastThreshold=12)
    source, descriptors = orb.detectAndCompute(template, None)
    if descriptors is None or len(source) < 8:
        return None
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    targets, target_descriptors = orb.detectAndCompute(gray, mask)
    if target_descriptors is None or len(targets) < 8:
        return None
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(descriptors, target_descriptors, k=2)
    matches = [
        pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < 0.7 * pair[1].distance
    ]
    if len(matches) < 8 or len({m.trainIdx for m in matches}) < 8:
        return None
    points = cv2.KeyPoint_convert([source[m.queryIdx] for m in matches])
    moved = cv2.KeyPoint_convert([targets[m.trainIdx] for m in matches])
    matrix, inliers = cv2.estimateAffine2D(
        points, moved, method=cv2.RANSAC, ransacReprojThreshold=3.0
    )
    if matrix is None or inliers is None:
        return None
    accepted = [p for p, ok in zip(points.tolist(), inliers.ravel().tolist(), strict=True) if ok]
    score = len(accepted) / len(matches)
    if len(accepted) < 8 or score < max(0.8, threshold):
        return None
    height, width = template.shape[:2]
    if any(
        max(p[axis] for p in accepted) - min(p[axis] for p in accepted) < span * 0.3
        for axis, span in enumerate((width, height))
    ):
        return None
    (a, b, tx), (c, d, ty) = matrix.tolist()
    if not all(math.isfinite(v) for v in (a, b, c, d, tx, ty)):
        return None
    sx, sy = math.hypot(a, c), math.hypot(b, d)
    if not (0.1 <= sx <= 2 and 0.1 <= sy <= 2 and a * d - b * c > 0):
        return None
    if abs(a * b + c * d) > 0.4 * sx * sy:
        return None
    corners = [
        (a * x + b * y + tx, c * x + d * y + ty)
        for x, y in ((0, 0), (width, 0), (0, height), (width, height))
    ]
    h, w = frame.shape[:2]
    box = (
        max(0, min(p[0] for p in corners)),
        max(0, min(p[1] for p in corners)),
        min(w, max(p[0] for p in corners)),
        min(h, max(p[1] for p in corners)),
        score,
    )
    return box if box[2] - box[0] >= 8 and box[3] - box[1] >= 8 else None


def target_crop(box, shape):
    """A native-pixel crop with room for motion, clipped at camera edges."""
    height, width = shape[:2]
    crop_width = min(width, max(640, math.ceil((box[2] - box[0]) * 3)))
    crop_height = min(height, max(640, math.ceil((box[3] - box[1]) * 3)))
    x = max(0, min(width - crop_width, round((box[0] + box[2] - crop_width) / 2)))
    y = max(0, min(height - crop_height, round((box[1] + box[3] - crop_height) / 2)))
    return x, y, x + crop_width, y + crop_height


def track_motion(before, frame, box, threshold):
    """Measure translation and independent size changes from foreground feature motion."""
    import cv2

    x, y, right, bottom = target_crop(box, frame.shape)
    old = cv2.cvtColor(before[y:bottom, x:right], cv2.COLOR_BGR2GRAY)
    new = cv2.cvtColor(frame[y:bottom, x:right], cv2.COLOR_BGR2GRAY)
    width, height = box[2] - box[0], box[3] - box[1]
    # Avoid corners on the selection border, which often belong to the background.
    left, top = round(box[0] - x + width * 0.1), round(box[1] - y + height * 0.1)
    end_x, end_y = round(box[2] - x - width * 0.1), round(box[3] - y - height * 0.1)
    points = cv2.goodFeaturesToTrack(
        old[top:end_y, left:end_x], maxCorners=80, qualityLevel=0.01, minDistance=3
    )
    if points is None or len(points) < 6:
        return None
    points += (left, top)
    options = {"winSize": (21, 21), "maxLevel": 3}
    moved, forward, _ = cv2.calcOpticalFlowPyrLK(old, new, points, None, **options)
    if moved is None or forward is None:
        return None
    returned, backward, _ = cv2.calcOpticalFlowPyrLK(new, old, moved, None, **options)
    if returned is None or backward is None:
        return None
    valid = [
        index
        for index, (original, back, ok, reverse_ok) in enumerate(
            zip(
                points.reshape(-1, 2).tolist(),
                returned.reshape(-1, 2).tolist(),
                forward.ravel().tolist(),
                backward.ravel().tolist(),
                strict=True,
            )
        )
        if ok and reverse_ok and math.dist(original, back) <= 1.5
    ]
    if len(valid) < 6:
        return None
    # ponytail: one affine motion fits the patch; severe articulation needs a detector.
    matrix, inliers = cv2.estimateAffine2D(
        points[valid], moved[valid], method=cv2.RANSAC, ransacReprojThreshold=2.0
    )
    if matrix is None or inliers is None:
        return None
    accepted = [
        point
        for point, ok in zip(
            points[valid].reshape(-1, 2).tolist(), inliers.ravel().tolist(), strict=True
        )
        if ok
    ]
    score = len(accepted) / len(points)
    if len(accepted) < 6 or score < threshold:
        return None
    if any(
        max(p[axis] for p in accepted) - min(p[axis] for p in accepted) < span * 0.15
        for axis, span in enumerate((width, height))
    ):
        return None
    (a, b, tx), (c, d, ty) = matrix.tolist()
    if not all(math.isfinite(value) for value in (a, b, c, d, tx, ty)):
        return None
    sx, sy = math.hypot(a, c), math.hypot(b, d)
    if not (0.75 <= sx <= 1.33 and 0.75 <= sy <= 1.33 and a * d - b * c > 0):
        return None
    if abs(a * b + c * d) > 0.4 * sx * sy:
        return None
    cx, cy = (box[0] + box[2]) / 2 - x, (box[1] + box[3]) / 2 - y
    cx, cy = a * cx + b * cy + tx + x, c * cx + d * cy + ty + y
    height_limit, width_limit = frame.shape[:2]
    result = (
        max(0, cx - width * sx / 2),
        max(0, cy - height * sy / 2),
        min(width_limit, cx + width * sx / 2),
        min(height_limit, cy + height * sy / 2),
        score,
    )
    return result if result[2] - result[0] >= 8 and result[3] - result[1] >= 8 else None


def create_manual_tracker(threshold, device="cpu"):
    """Use the pinned local model, with no network access or dependency changes."""
    if device not in ("cpu", "mps"):
        raise ValueError("수동 추적 장치는 cpu 또는 mps를 선택하세요.")
    if not MANUAL_MODEL.is_file():
        return None
    if hashlib.sha256(MANUAL_MODEL.read_bytes()).hexdigest() != MANUAL_MODEL_SHA256:
        raise ValueError("수동 ViT 모델의 해시가 다릅니다. models/vittrack.md를 확인하세요.")
    if device == "mps":
        from tools.manual_vit import MPSVitTracker

        return MPSVitTracker(MANUAL_MODEL, threshold)
    import cv2

    params = cv2.TrackerVit_Params()
    params.net = str(MANUAL_MODEL)
    params.tracking_score_threshold = threshold
    return cv2.TrackerVit_create(params)


def tracker_detection(tracker, frame, threshold):
    ok, (x, y, width, height) = tracker.update(frame)
    score = float(tracker.getTrackingScore())
    if not ok or not all(math.isfinite(v) for v in (x, y, width, height, score)):
        return None
    if not threshold <= score <= 1 or width <= 0 or height <= 0:
        return None
    h, w = frame.shape[:2]
    box = (max(0, x), max(0, y), min(w, x + width), min(h, y + height), score)
    return box if box[2] - box[0] >= 8 and box[3] - box[1] >= 8 else None


def search_tiles(shape, side=640):
    """Overlapping native-resolution tiles; scan one per fresh frame on loss."""
    height, width = shape[:2]
    tile_width, tile_height = min(side, width), min(side, height)
    step = max(1, side * 3 // 4)
    xs = sorted(set(range(0, width - tile_width + 1, step)) | {width - tile_width})
    ys = sorted(set(range(0, height - tile_height + 1, step)) | {height - tile_height})
    return [(x, y, x + tile_width, y + tile_height) for y in ys for x in xs]


def restore_detection(detection, region, processed_shape):
    """Map crop/resized-image boxes back to the original camera pixels."""
    if detection is None:
        return None
    x, y, right, bottom = region
    sx = (right - x) / processed_shape[1]
    sy = (bottom - y) / processed_shape[0]
    x1, y1, x2, y2, score = detection
    return x + x1 * sx, y + y1 * sy, x + x2 * sx, y + y2 * sy, score


def match_region(frame, template, threshold, region, *, fast=False, unique=False):
    import cv2

    x1, y1, x2, y2 = region
    gray = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    sample = template
    if not fast and max(gray.shape) > 960:
        # Only broad searches are reduced; small-target crops keep every source pixel.
        scale = 960 / max(gray.shape)
        size = (round(gray.shape[1] * scale), round(gray.shape[0] * scale))
        sample_size = (
            max(1, round(template.shape[1] * size[0] / gray.shape[1])),
            max(1, round(template.shape[0] * size[1] / gray.shape[0])),
        )
        gray = cv2.resize(gray, size, interpolation=cv2.INTER_AREA)
        sample = cv2.resize(template, sample_size, interpolation=cv2.INTER_AREA)
    options = {}
    if fast:
        options["fast"] = True
    if unique:
        options["unique"] = True
    detection = match_template(gray, sample, threshold, **options)
    return restore_detection(detection, region, gray.shape)


def observation_for_preview(result, captured, shape, now):
    """Overlay only a recent observation from this frame size, never a future frame."""
    if result is None:
        return None
    observed, observed_shape = result[:2]
    if observed > captured or observed_shape != shape or not 0 <= now - observed <= MAX_AGE_S:
        return None
    return result


class CameraSession:
    def __init__(self, camera, model_path, threshold, device, resolution=(1920, 1080)):
        self.camera, self.model_path = camera, model_path
        self.threshold, self.device = threshold, device
        self.resolution = resolution
        self.metrics = SessionMetrics(
            {
                "device": device,
                "mode": "YOLO" if model_path else "manual",
                "threshold": threshold,
                "requested_resolution": list(resolution),
            }
        )
        self.manual_engine = "대기"
        self.collector = None
        self.collection_lock = threading.Lock()
        self.stop = threading.Event()
        self.frames = queue.Queue(maxsize=1)
        self.previews = queue.Queue(maxsize=1)
        self.results = queue.Queue(maxsize=1)
        self.selections = queue.Queue(maxsize=1)
        self.errors = queue.Queue()
        self.threads = [
            threading.Thread(target=self.capture, daemon=True),
            threading.Thread(target=self.process, daemon=True),
        ]
        for thread in self.threads:
            thread.start()

    def capture(self):
        import cv2

        capture = None
        try:
            backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
            camera = self.camera
            if isinstance(camera, dict):
                camera = next(
                    (d["index"] for d in list_cameras() if d["id"] == self.camera["id"]), None
                )
                if camera is None:
                    raise RuntimeError(
                        "선택한 카메라가 연결되어 있지 않습니다. 목록을 새로고침하세요."
                    )
            capture = cv2.VideoCapture(camera, backend)
            if not capture.isOpened():
                raise RuntimeError(
                    "선택한 카메라를 열 수 없습니다. 연결과 macOS 카메라 권한을 확인하세요."
                )
            if isinstance(self.camera, dict):
                devices = list_cameras()
                if camera >= len(devices) or devices[camera]["id"] != self.camera["id"]:
                    raise RuntimeError(
                        "카메라 목록이 바뀌었습니다. 목록 새로고침 후 다시 시작하세요."
                    )
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.resolution[0])
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.resolution[1])
            while not self.stop.is_set():
                ok, frame = capture.read()
                if not ok:
                    raise RuntimeError(
                        "카메라 영상이 끊겼습니다. 연결 확인 후 목록 새로고침으로 다시 시작하세요."
                    )
                item = (time.monotonic(), frame)
                frame_id = self.metrics.capture(item[0], frame.shape)
                collector = self.collector
                if collector is not None and frame_id is not None:
                    collector.submit(frame_id, item[0], frame)
                self.metrics.discard("preview_replaced", int(put_latest(self.previews, item)))
                self.metrics.discard("tracking_replaced", int(put_latest(self.frames, item)))
        except Exception as exc:
            self.metrics.fail(exc)
            self.errors.put(str(exc))
            self.stop.set()
        finally:
            if capture is not None:
                capture.release()
            with self.collection_lock:
                if self.collector is not None:
                    self.collector.close()

    def start_collection(self, parent):
        with self.collection_lock:
            if self.stop.is_set() or self.collector is not None:
                raise ValueError("실행 중인 새 세션에서 한 번만 수집을 시작할 수 있습니다.")
            self.collector = FrameCollector(
                Path(parent) / f"learning-{self.metrics.session_id}", self.metrics
            )

    def process(self):
        try:
            model, drone_classes = None, []
            if self.model_path:
                from ultralytics import YOLO

                model = YOLO(self.model_path)
                if model.task != "detect":
                    raise ValueError("드론 객체 검출용 모델을 선택하세요.")
                drone_classes = [
                    key for key, name in model.names.items() if name.lower() == "drone"
                ]
                if not drone_classes:
                    raise ValueError(
                        "모델에 drone 클래스가 없습니다. 드론 학습 best.pt를 선택하세요."
                    )
            template, recent_template, previous, geometry = None, None, None, None
            self.manual_engine = f"ViT {self.device.upper()} 준비 중" if model is None else "대기"
            tracker = create_manual_tracker(self.threshold, self.device) if model is None else None
            tracker_ready = False
            self.manual_engine = (
                f"특징점 CPU + ViT {self.device.upper()}"
                if tracker is not None
                else "특징점 CPU (ViT 모델 없음)"
            )
            motion_frame, motion_time = None, 0.0
            signature, pending = None, None
            scan_index = 0
            last_seen = time.monotonic()
            while not self.stop.is_set():
                try:
                    captured, frame = self.frames.get(timeout=0.1)
                except queue.Empty:
                    continue
                started = time.monotonic()
                if started - captured > MAX_AGE_S:
                    self.metrics.discard("stale_input")
                    continue
                shape = frame.shape[:2]
                if geometry != shape:
                    template, previous, geometry, scan_index = None, None, shape, 0
                    recent_template = None
                    signature, pending = None, None
                    motion_frame = None
                    tracker_ready = False
                try:
                    selection = self.selections.get_nowait()
                    template, previous, scan_index = None, None, 0
                    recent_template = None
                    signature, pending = None, None
                    motion_frame = None
                    tracker_ready = False
                    if selection is not None and selection[2].shape[:2] == shape:
                        template, previous, motion_frame, motion_time = selection
                        recent_template = template
                        last_seen = started
                        x1, y1, x2, y2 = previous
                        signature = color_signature(motion_frame[y1:y2, x1:x2])
                        if tracker is not None:
                            tracker.init(motion_frame, (x1, y1, x2 - x1, y2 - y1))
                            tracker_ready = True
                except queue.Empty:
                    pass
                if started - last_seen > 2.0:
                    previous = None
                    motion_frame = None
                    tracker_ready = False
                whole = (0, 0, shape[1], shape[0])
                region = whole
                search_template = template
                recent = previous is not None and started - last_seen <= 1.0
                small = (
                    previous is not None
                    and max(
                        (previous[2] - previous[0]) / shape[1],
                        (previous[3] - previous[1]) / shape[0],
                    )
                    <= 0.08
                )
                if recent and (model is None or small):
                    region = target_crop(previous, shape)
                elif not recent and (model is not None or template is not None):
                    tiles = search_tiles(shape)
                    # One broad search per sweep also covers targets larger than a tile.
                    slot = scan_index % (len(tiles) + 1)
                    region = whole if slot == 0 else tiles[slot - 1]
                    if model is None and (scan_index // (len(tiles) + 1)) % 2 == 0:
                        search_template = recent_template
                    scan_index += 1
                detection = None
                candidate = None
                method = "YOLO" if model else "템플릿"
                if model is None:
                    if template is not None:
                        missing = motion_frame is None
                        confirmed = False
                        if pending is not None and not 0 < captured - pending[0] <= MAX_AGE_S:
                            pending = None
                        if missing and pending is not None:
                            detection = verify_appearance(
                                frame,
                                track_motion(
                                    pending[1], frame, pending[2][:4], max(0.8, self.threshold)
                                ),
                                signature,
                            )
                            if detection is not None:
                                confirmed = True
                                region = target_crop(pending[2][:4], shape)
                            else:
                                import cv2

                                x1, y1, x2, y2 = (round(v) for v in pending[2][:4])
                                sample = cv2.cvtColor(pending[1][y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
                                for pending_region in (target_crop(pending[2][:4], shape), whole):
                                    detection = verify_appearance(
                                        frame,
                                        match_region(
                                            frame,
                                            sample,
                                            max(0.8, self.threshold),
                                            pending_region,
                                            fast=True,
                                            unique=True,
                                        ),
                                        signature,
                                    )
                                    if detection is not None:
                                        confirmed, region = True, pending_region
                                        break
                        if (
                            detection is None
                            and motion_frame is not None
                            and previous is not None
                            and 0 <= captured - motion_time <= 1.0
                        ):
                            detection = verify_appearance(
                                frame,
                                track_motion(motion_frame, frame, previous, self.threshold),
                                signature,
                            )
                            if detection is not None:
                                method = "특징점"
                        if detection is None and tracker_ready and not missing:
                            detection = verify_appearance(
                                frame, tracker_detection(tracker, frame, self.threshold), signature
                            )
                            if detection is not None:
                                method, region = "ViT", whole
                        mask = color_mask(frame, signature) if detection is None else None
                        searchable = mask is not None and mask.any()
                        if detection is None and searchable:
                            fast_region = whole if missing else region
                            detection = verify_appearance(
                                frame,
                                match_region(
                                    frame,
                                    recent_template,
                                    max(0.8, self.threshold)
                                    if fast_region == whole
                                    else self.threshold,
                                    fast_region,
                                    fast=True,
                                    unique=fast_region == whole,
                                ),
                                signature,
                            )
                            if detection is None and fast_region != whole:
                                detection = verify_appearance(
                                    frame,
                                    match_region(
                                        frame,
                                        recent_template,
                                        max(0.8, self.threshold),
                                        whole,
                                        fast=True,
                                        unique=True,
                                    ),
                                    signature,
                                )
                                if detection is not None:
                                    fast_region = whole
                            if detection is not None:
                                method, region = "재획득" if missing else "빠른 재검색", fast_region
                        if (
                            detection is None
                            and searchable
                            and missing
                            and recent_template is not template
                        ):
                            detection = verify_appearance(
                                frame,
                                match_region(
                                    frame,
                                    template,
                                    max(0.8, self.threshold),
                                    whole,
                                    fast=True,
                                    unique=True,
                                ),
                                signature,
                            )
                            if detection is not None:
                                method, region = "재획득", whole
                        if detection is None and searchable:
                            references = (
                                (template,)
                                if recent_template is template
                                else (template, recent_template)
                            )
                            # Original geometry avoids box inflation after rotated crops.
                            for reference in references:
                                detection = verify_appearance(
                                    frame,
                                    match_features(frame, reference, self.threshold, mask),
                                    signature,
                                )
                                if detection is not None:
                                    method, region = "특징점 재검색", whole
                                    break
                        if detection is None and searchable:
                            detection = verify_appearance(
                                frame,
                                match_region(
                                    frame,
                                    search_template,
                                    max(0.8, self.threshold) if missing else self.threshold,
                                    region,
                                    unique=missing or region == whole,
                                ),
                                signature,
                            )
                        if detection is not None and missing:
                            if (
                                pending is not None
                                and overlap(pending[2][:4], detection[:4]) >= 0.3
                            ):
                                confirmed = True
                            if confirmed:
                                method = "재획득"
                            else:
                                candidate, detection, method = detection, None, "재획득 확인 중"
                else:
                    x1, y1, x2, y2 = region
                    crop = frame[y1:y2, x1:x2]
                    result = model.predict(
                        crop,
                        conf=self.threshold,
                        classes=drone_classes,
                        device=self.device,
                        imgsz=640,
                        verbose=False,
                    )[0]
                    boxes = result.boxes
                    detections = [
                        restore_detection((*coords, float(confidence)), region, crop.shape[:2])
                        for coords, confidence in zip(
                            boxes.xyxy.cpu().tolist(), boxes.conf.cpu().tolist(), strict=True
                        )
                    ]
                    # ponytail: IoU needs overlapping boxes; add an ID tracker for fast motion.
                    detection = choose_target(detections, previous)
                appearance = None
                if model is None and detection is not None:
                    x1, y1, x2, y2 = (round(v) for v in detection[:4])
                    # Keep the last complete appearance, not a clipped exit or blank background.
                    if 0 < x1 < x2 < shape[1] and 0 < y1 < y2 < shape[0]:
                        import cv2

                        patch = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
                        if patch.std() >= 5:
                            appearance = patch
                if tracker is not None and detection is not None and method != "ViT":
                    x1, y1, x2, y2 = (round(v) for v in detection[:4])
                    tracker.init(frame, (x1, y1, x2 - x1, y2 - y1))
                    tracker_ready = True
                finished = time.monotonic()
                if finished - captured > MAX_AGE_S:
                    self.metrics.discard("stale_result")
                    detection = None
                    candidate, pending = None, None
                    tracker_ready = False
                elif candidate is not None:
                    pending = captured, frame, candidate
                elif detection is not None:
                    pending = None
                    previous, last_seen = detection[:4], finished
                    if appearance is not None:
                        recent_template = appearance
                if model is None:
                    motion_frame = frame if detection is not None else None
                    motion_time = captured
                    if detection is None:
                        tracker_ready = False
                        if candidate is None:
                            pending = None
                            if template is not None:
                                method = "재검색"
                self.metrics.processed(captured, started, finished, detection, method)
                self.metrics.discard(
                    "result_replaced",
                    int(
                        put_latest(
                            self.results,
                            (captured, shape, detection, finished - started, region, method),
                        )
                    ),
                )
        except Exception as exc:
            self.metrics.fail(exc)
            self.errors.put(f"추적 오류: {exc}")
            self.stop.set()

    def alive(self):
        return any(thread.is_alive() for thread in self.threads)

    def finish(self):
        if self.metrics.ended is not None:
            return
        self.metrics.finish()
        if self.collector is not None:
            self.metrics.settings.update(
                collection_path=str(self.collector.root), collection_error=self.collector.error
            )
            try:
                with (self.collector.root / "measurement.json").open("x", encoding="utf-8") as file:
                    json.dump(
                        self.metrics.report(), file, ensure_ascii=False, indent=2, allow_nan=False
                    )
            except OSError as exc:
                self.collector.error = str(exc)
                self.metrics.settings["collection_error"] = str(exc)


class CameraGUI:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk

        self.root, self.session, self.frame = root, None, None
        self.closing = False
        self.drag_start, self.selection, self.photo = None, None, None
        self.result, self.selection_time = None, 0.0
        self.metrics = None
        self.display_scale = 1.0
        root.title("드론 카메라 추적 · 로컬 시험")
        root.geometry("1040x780")
        root.minsize(800, 650)
        controls = ttk.Frame(root, padding=12)
        controls.pack(fill="x")
        self.camera = tk.StringVar()
        self.cameras = {}
        self.mode = tk.StringVar(value="수동 지정")
        self.model = tk.StringVar()
        self.threshold = tk.StringVar(value="0.65")
        self.device = tk.StringVar(value="mps")
        self.resolution = tk.StringVar(value="1920×1080")
        self.capture_info = tk.StringVar(value="실제 입력: 대기 · 작은 대상 원본 크롭")
        ttk.Label(controls, text="카메라").grid(row=0, column=0)
        self.camera_select = ttk.Combobox(
            controls,
            state="readonly",
            textvariable=self.camera,
            width=30,
            postcommand=self.refresh_cameras,
        )
        self.camera_select.grid(row=0, column=1, columnspan=4, sticky="ew", padx=8)
        self.refresh_button = ttk.Button(
            controls, text="목록 새로고침", command=self.refresh_cameras
        )
        self.refresh_button.grid(row=0, column=5, padx=4)
        ttk.Label(controls, text="추적 방식").grid(row=1, column=0)
        ttk.Combobox(
            controls,
            values=("수동 지정", "YOLO 자동 검출"),
            state="readonly",
            textvariable=self.mode,
            width=16,
        ).grid(row=1, column=1, padx=8)
        ttk.Label(controls, text="품질 임계값\n수동 0.65 / 자동 0.25").grid(
            row=1, column=2, columnspan=2
        )
        ttk.Entry(controls, textvariable=self.threshold, width=5).grid(row=1, column=4)
        ttk.Combobox(
            controls, values=("mps", "cpu"), state="readonly", textvariable=self.device, width=5
        ).grid(row=1, column=5, padx=8)
        self.start_button = ttk.Button(controls, text="카메라 시작", command=self.start)
        self.start_button.grid(row=0, column=6, padx=4)
        ttk.Button(controls, text="정지", command=self.stop).grid(row=0, column=7)
        ttk.Entry(controls, textvariable=self.model, width=50).grid(
            row=2, column=0, columnspan=6, sticky="ew", pady=10
        )
        ttk.Button(controls, text="best.pt 선택", command=self.browse).grid(row=2, column=6)
        ttk.Button(controls, text="대상 해제", command=self.clear).grid(row=2, column=7)
        ttk.Label(controls, text="입력 해상도").grid(row=3, column=0)
        ttk.Combobox(
            controls,
            values=("1920×1080", "1280×720", "640×480"),
            state="readonly",
            textvariable=self.resolution,
            width=16,
        ).grid(row=3, column=1, columnspan=2, sticky="w", pady=4)
        ttk.Label(controls, textvariable=self.capture_info, wraplength=380, justify="left").grid(
            row=3, column=3, columnspan=5, sticky="w"
        )
        ttk.Label(
            root,
            text="수동: 영상에서 드론을 드래그하세요. 자동: 로컬 드론 모델을 선택하세요. "
            "학습 수집을 선택하면 원본 사진을 로컬 저장합니다.",
            padding=8,
        ).pack()
        measurements = ttk.Frame(root, padding=(12, 4))
        measurements.pack(fill="x")
        self.collect_button = ttk.Button(
            measurements, text="학습 프레임 수집", command=self.collect_frames, state="disabled"
        )
        self.collect_button.grid(row=0, column=0, sticky="w")
        self.save_metrics_button = ttk.Button(
            measurements, text="측정 기록 저장", command=self.save_metrics, state="disabled"
        )
        self.save_metrics_button.grid(row=0, column=1, padx=8)
        self.evaluate_button = ttk.Button(
            measurements, text="정답 파일로 평가", command=self.evaluate_metrics, state="disabled"
        )
        self.evaluate_button.grid(row=0, column=2)
        self.performance = tk.StringVar(
            value="성능: 대기 · 속도와 품질 점수는 인식 정확도가 아닙니다."
        )
        ttk.Label(measurements, textvariable=self.performance, wraplength=750, justify="left").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=4
        )
        self.canvas = tk.Canvas(root, background="#111827", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=12)
        self.canvas.bind("<ButtonPress-1>", self.begin_selection)
        self.canvas.bind("<B1-Motion>", self.move_selection)
        self.canvas.bind("<ButtonRelease-1>", self.end_selection)
        self.status = tk.StringVar(value="대기 · 시작 버튼을 누르면 카메라가 켜집니다.")
        footer = ttk.Frame(root)
        footer.pack(fill="x")
        ttk.Button(footer, text="종료", command=self.close).pack(side="right", padx=12, pady=8)
        ttk.Label(footer, textvariable=self.status, padding=12).pack(
            side="left", fill="x", expand=True
        )
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh_cameras()
        root.after(30, self.poll)

    def refresh_cameras(self):
        if self.session is not None:
            return
        selected = self.cameras.get(self.camera.get(), {}).get("id")
        empty_label = "연결된 카메라 없음"
        try:
            devices = list_cameras()
        except RuntimeError as exc:
            devices = []
            empty_label = "카메라 목록 조회 실패"
            self.status.set(str(exc))
        else:
            self.status.set(
                "카메라를 선택하고 시작하세요. USB 연결 후 목록 새로고침을 누르세요."
                if devices
                else "연결된 카메라가 없습니다. 연결 후 목록 새로고침을 누르세요."
            )
        self.cameras = {}
        for device in devices:
            label = f"{device['kind']} · {device['name']}"
            base, number = label, 2
            while label in self.cameras:
                label, number = f"{base} ({number})", number + 1
            self.cameras[label] = device
        self.camera_select.configure(values=tuple(self.cameras))
        choice = next((d for d in devices if d["id"] == selected), None)
        if choice is None:
            choice = next((d for d in devices if d["builtin"]), devices[0] if devices else None)
        self.camera.set(
            next(label for label, d in self.cameras.items() if d is choice)
            if choice is not None
            else empty_label
        )
        self.start_button.configure(state="normal" if devices else "disabled")

    def browse(self):
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title="신뢰하는 로컬 드론 학습 모델 선택", filetypes=[("PyTorch weights", "*.pt")]
        )
        if path:
            self.model.set(path)

    def start(self):
        from tkinter import messagebox

        if self.session is not None or self.closing:
            return
        try:
            camera = self.cameras.get(self.camera.get())
            if camera is None:
                raise ValueError(
                    "연결된 카메라를 선택하세요. 목록 새로고침으로 확인할 수 있습니다."
                )
            threshold = float(self.threshold.get())
            if not math.isfinite(threshold) or not 0 < threshold <= 1:
                raise ValueError("임계값은 0 초과 1 이하여야 합니다.")
            if self.resolution.get() not in ("1920×1080", "1280×720", "640×480"):
                raise ValueError("지원하는 입력 해상도를 선택하세요.")
            resolution = tuple(int(value) for value in self.resolution.get().split("×"))
            model = None
            if self.mode.get() == "YOLO 자동 검출":
                path = Path(self.model.get()).expanduser()
                if path.suffix.lower() != ".pt" or not path.is_file():
                    raise ValueError("기존 로컬 드론 학습 .pt 파일을 선택하세요.")
                model = str(path.resolve())
            self.frame = None
            self.result = None
            self.selection_time = time.monotonic()
            self.canvas.delete("all")
            self.session = CameraSession(camera, model, threshold, self.device.get(), resolution)
            self.metrics = self.session.metrics
            self.save_metrics_button.configure(state="disabled")
            self.evaluate_button.configure(state="disabled")
            self.collect_button.configure(state="normal")
            self.start_button.configure(state="disabled")
            self.camera_select.configure(state="disabled")
            self.refresh_button.configure(state="disabled")
            self.status.set(f"{self.camera.get()} 연결 중…")
        except ValueError as exc:
            messagebox.showerror("설정 확인", str(exc))

    def stop(self):
        if self.session is not None:
            self.session.stop.set()
            self.status.set("정지 중 · 카메라 해제 대기…")
        self.frame = None
        self.canvas.delete("all")
        self.drag_start = None
        self.result = None

    def collect_frames(self):
        from tkinter import filedialog, messagebox

        session = self.session
        if session is None or session.stop.is_set():
            return
        parent = filedialog.askdirectory(title="원본 학습 사진을 저장할 로컬 폴더 선택")
        if parent:
            try:
                session.start_collection(parent)
                self.collect_button.configure(state="disabled")
                self.status.set(f"학습 사진 수집 · {session.collector.root}")
            except (OSError, ValueError) as exc:
                messagebox.showerror("학습 수집 실패", str(exc))

    def save_metrics(self):
        from tkinter import filedialog, messagebox

        if self.session is not None or self.metrics is None:
            return
        path = filedialog.asksaveasfilename(
            title="로컬 측정 기록 저장",
            defaultextension=".json",
            initialfile=f"tracking-{self.metrics.session_id[:8]}.json",
            initialdir=self.metrics.settings.get("collection_path", ""),
            filetypes=[("JSON", "*.json")],
        )
        if path:
            try:
                Path(path).write_text(
                    json.dumps(
                        self.metrics.report(), ensure_ascii=False, indent=2, allow_nan=False
                    ),
                    encoding="utf-8",
                )
                self.status.set("측정 JSON 저장 완료")
            except (OSError, ValueError) as exc:
                messagebox.showerror("측정 저장 실패", str(exc))

    def evaluate_metrics(self):
        from tkinter import filedialog, messagebox

        from tools.validate_config import strict_json

        if self.session is not None or self.metrics is None:
            return
        path = filedialog.askopenfilename(
            title="같은 측정 세션의 정답 파일 선택", filetypes=[("JSON", "*.json")]
        )
        if path:
            try:
                result = evaluate(
                    self.metrics.report(), strict_json(Path(path).read_text(encoding="utf-8"))
                )
                messagebox.showinfo("추적 박스 평가", evaluation_text(result))
            except (OSError, ValueError) as exc:
                messagebox.showerror("평가 실패", str(exc))

    def clear(self):
        if self.session and not self.session.model_path:
            self.selection_time = time.monotonic()
            put_latest(self.session.selections, None)
            self.result = None
            self.status.set("대상 해제 · 드론을 다시 드래그하세요.")

    def begin_selection(self, event):
        if self.frame is not None and self.session and not self.session.model_path:
            self.drag_start = (event.x, event.y)
            self.canvas.delete("selection")
            self.selection = self.canvas.create_rectangle(
                event.x, event.y, event.x, event.y, outline="#fbbf24", width=2, tags="selection"
            )

    def move_selection(self, event):
        if self.drag_start:
            self.canvas.coords(self.selection, *self.drag_start, event.x, event.y)

    def end_selection(self, event):
        import cv2

        if self.drag_start is None or self.frame is None or self.session is None:
            return
        start = tuple(value / self.display_scale for value in self.drag_start)
        end = (event.x / self.display_scale, event.y / self.display_scale)
        height, width = self.frame.shape[:2]
        box = selection_box(start, end, width, height)
        if box:
            x1, y1, x2, y2 = box
            template = cv2.cvtColor(self.frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
            if template.std() < 5:
                self.status.set("무늬가 너무 적습니다. 드론 외형이 들어오도록 다시 지정하세요.")
            else:
                self.selection_time = time.monotonic()
                put_latest(self.session.selections, (template, box, self.frame, self.last_display))
                self.result = None
        self.drag_start = None
        self.canvas.delete("selection")

    def poll(self):
        import cv2
        from PIL import Image, ImageTk

        session = self.session
        if session is not None:
            try:
                error = session.errors.get_nowait()
                self.stop()
                self.status.set(error)
            except queue.Empty:
                pass
            if session.stop.is_set():
                if not session.alive():
                    session.finish()
                    self.metrics = session.metrics
                    self.session = None
                    self.save_metrics_button.configure(state="normal")
                    self.evaluate_button.configure(state="normal")
                    self.collect_button.configure(state="disabled")
                    self.start_button.configure(state="normal")
                    self.camera_select.configure(state="readonly")
                    self.refresh_button.configure(state="normal")
                    if self.status.get().startswith("정지 중"):
                        self.status.set("정지 · 카메라 해제 완료")
            elif self.drag_start is None:
                try:
                    self.result = session.results.get_nowait()
                    if self.result[0] < self.selection_time:
                        self.result = None
                except queue.Empty:
                    pass
                try:
                    self.last_display, self.frame = session.previews.get_nowait()
                except queue.Empty:
                    pass
                now = time.monotonic()
                captured = getattr(self, "last_display", 0)
                age = now - captured
                if self.frame is not None and age <= MAX_AGE_S:
                    frame = self.frame
                    rendered = frame.copy()
                    h, w = frame.shape[:2]
                    self.capture_info.set(
                        f"실제 {w}×{h} / 요청 {session.resolution[0]}×{session.resolution[1]}"
                        + (f" · 수동 {session.manual_engine}" if not session.model_path else "")
                    )
                    observation = observation_for_preview(self.result, captured, (h, w), now)
                    detection = observation[2] if observation else None
                    cv2.drawMarker(
                        rendered, (w // 2, h // 2), (255, 255, 255), cv2.MARKER_CROSS, 20, 1
                    )
                    info = (
                        "대상 미관측"
                        if session.model_path
                        else "드론을 드래그해 지정하세요 / 대상 소실"
                    )
                    if observation and observation[5] == "재검색":
                        info = "지정 대상 재검색 중 · 재진입 대기"
                    elif observation and observation[5] == "재획득 확인 중":
                        info = "재진입 후보 확인 중 · 다음 관측 대기"
                    if observation:
                        region = observation[4]
                        if region != (0, 0, w, h):
                            cv2.rectangle(rendered, region[:2], region[2:], (255, 180, 0), 1)
                    if detection:
                        x1, y1, x2, y2 = (int(v) for v in detection[:4])
                        center = ((x1 + x2) // 2, (y1 + y2) // 2)
                        cv2.rectangle(rendered, (x1, y1), (x2, y2), (80, 220, 80), 2)
                        cv2.line(rendered, (w // 2, h // 2), center, (80, 220, 80), 1)
                        info = (
                            f"최근 관측 · {observation[5]} 품질 {detection[4]:.2f} · "
                            f"중심 오차 x={center[0] - w // 2:+d}, y={center[1] - h // 2:+d}px · "
                            f"{(now - observation[0]) * 1000:.0f}ms 전"
                        )
                    self.display_scale = min(
                        self.canvas.winfo_width() / w, self.canvas.winfo_height() / h
                    )
                    size = (
                        max(1, int(w * self.display_scale)),
                        max(1, int(h * self.display_scale)),
                    )
                    preview = cv2.resize(rendered, size, interpolation=cv2.INTER_AREA)
                    rgb = cv2.cvtColor(preview, cv2.COLOR_BGR2RGB)
                    self.photo = ImageTk.PhotoImage(Image.fromarray(rgb))
                    self.canvas.delete("all")
                    self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
                    session.metrics.displayed(captured, time.monotonic())
                    processing = (
                        f" · 추적 처리 {self.result[3] * 1000:.0f}ms" if self.result else ""
                    )
                    if observation and observation[4] != (0, 0, w, h):
                        x1, y1, x2, y2 = observation[4]
                        processing += f" · 크롭 {x2 - x1}×{y2 - y1}"
                    self.status.set(f"{info}{processing} · 영상 {age * 1000:.0f}ms")
                elif age > MAX_AGE_S:
                    self.frame = None
                    self.canvas.delete("all")
                    self.status.set("새 영상 대기 · 대상 관측 무효")
            self.performance.set(performance_text(session.metrics.snapshot()))
            if session.collector is not None:
                collector = session.collector
                self.performance.set(
                    self.performance.get()
                    + f" · 학습 PNG {len(collector.samples)}장"
                    + f" / 저장 큐 누락 {collector.dropped}"
                    + (f" · 저장 오류: {collector.error}" if collector.error else "")
                )
        self.root.after(30, self.poll)

    def close(self):
        self.closing = True
        self.stop()
        if self.session and self.session.alive():
            self.root.after(50, self.close)
        else:
            if self.session is not None:
                self.session.finish()
            self.root.destroy()


def main():
    import tkinter as tk

    root = tk.Tk()
    CameraGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
