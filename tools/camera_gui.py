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
from tools.pretrained_drone import installed_model
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
// Keep descriptors in a native NSArray; JXA converts a JS array into dictionaries.
devices = devices.sortedArrayUsingDescriptors($.NSArray.arrayWithObject(
    $.NSSortDescriptor.sortDescriptorWithKeyAscending('uniqueID', true)
));
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
    candidates = [d for d in detections if previous is None or overlap(d[:4], previous) >= 0.3]
    return max(
        candidates,
        key=lambda d: (overlap(d[:4], previous) if previous is not None else 0, d[4]),
        default=None,
    )


class YoloReacquisition:
    """Confirm a lost target on three consistent fresh frames before publishing it."""

    def __init__(self):
        self.required = False
        self.candidate = None
        self.confirmed_box = None
        self.first_seen, self.last_seen, self.count = 0.0, 0.0, 0

    def lose(self):
        self.required = True
        self.candidate = None
        self.count = 0

    def candidate_for(self, observed):
        if self.candidate is not None and not (
            0 < observed - self.last_seen <= MAX_AGE_S
            and 0 < observed - self.first_seen <= MAX_AGE_S
        ):
            self.lose()
        return self.candidate

    def observe(self, detection, observed):
        if detection is None:
            self.lose()
            return None, "재검색"
        if not self.required:
            return detection, "YOLO"
        if self.confirmed_box is not None:
            x1, y1, x2, y2 = self.confirmed_box
            area = (detection[2] - detection[0]) * (detection[3] - detection[1])
            # shortcut: large scale jumps need a new session or better identity evidence.
            if area > 4 * (x2 - x1) * (y2 - y1):
                self.lose()
                return None, "재검색"
        previous = self.candidate_for(observed)
        if previous is not None and overlap(previous, detection[:4]) >= 0.3:
            self.count += 1
        else:
            self.first_seen, self.count = observed, 1
        self.candidate, self.last_seen = detection[:4], observed
        if self.count < 3:
            return None, f"재획득 확인 중 ({self.count}/3)"
        self.required, self.candidate = False, None
        return detection, "재획득"


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
    if device not in ("cpu", "cuda", "mps"):
        raise ValueError("수동 추적 장치는 cpu, cuda 또는 mps를 선택하세요.")
    if not MANUAL_MODEL.is_file():
        return None
    if hashlib.sha256(MANUAL_MODEL.read_bytes()).hexdigest() != MANUAL_MODEL_SHA256:
        raise ValueError("수동 ViT 모델의 해시가 다릅니다. models/vittrack.md를 확인하세요.")
    if device in ("cuda", "mps"):
        from tools.manual_vit import TorchVitTracker

        return TorchVitTracker(MANUAL_MODEL, threshold, device)
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
    def __init__(
        self, camera, model_path, threshold, device, resolution=(1920, 1080), *, video_path=None
    ):
        if video_path is not None and not model_path:
            raise ValueError("영상 테스트에는 drone 클래스가 있는 로컬 YOLO 모델이 필요합니다.")
        self.camera, self.model_path = camera, model_path
        self.video_path = Path(video_path) if video_path is not None else None
        self.paused = threading.Event()
        self.video_displayed = 0.0
        self.display_ack = threading.Event()
        self.video_timestamp = 0.0
        self.video_progress = (0.0, 0)
        self.threshold, self.device = threshold, device
        self.resolution = resolution
        self.metrics = SessionMetrics(
            {
                "device": device,
                "mode": "YOLO" if model_path else "manual",
                "threshold": threshold,
                "requested_resolution": list(resolution),
                "source_type": "video_test" if video_path is not None else "camera",
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
        if self.metrics.settings.get("source_type") == "video_test":
            self.capture_video()
            return
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
            self.metrics.settings["requested_fps"] = 60
            capture.set(cv2.CAP_PROP_FPS, 60)
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

    def capture_video(self):
        import cv2

        from tools.training_data import sha256

        capture = None
        settings = self.metrics.settings
        settings["video_complete"] = False
        try:
            if not self.video_path.is_file():
                raise ValueError("기존 로컬 동영상 파일을 선택하세요.")
            digest = sha256(self.video_path)
            settings.update(
                video_path=str(self.video_path),
                video_sha256=digest,
                model_sha256=sha256(Path(self.model_path)),
                timestamp_source="opencv_pos_msec",
            )
            capture = cv2.VideoCapture(str(self.video_path))
            if not capture.isOpened():
                raise ValueError("동영상을 열 수 없습니다. 파일·코덱을 확인하세요.")
            expected = capture.get(cv2.CAP_PROP_FRAME_COUNT)
            if not math.isfinite(expected) or expected < 1 or expected != int(expected):
                raise ValueError("전체 프레임 수를 확인할 수 없어 영상 테스트를 중단합니다.")
            settings["video_frame_count"] = int(expected)
            previous, dispatched, decoded = None, time.monotonic(), 0
            while not self.stop.is_set():
                while self.paused.is_set() and not self.stop.wait(0.03):
                    pass
                if self.stop.is_set():
                    break
                ok, frame = capture.read()
                if not ok:
                    if decoded != expected:
                        raise ValueError(
                            "영상 디코딩이 중간에 끊겼습니다. 전체 영상 평가는 불가합니다."
                        )
                    if sha256(self.video_path) != digest:
                        raise ValueError("테스트 중 원본 동영상이 변경됐습니다.")
                    settings["video_complete"] = True
                    self.stop.set()
                    break
                timestamp = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000
                if (
                    not math.isfinite(timestamp)
                    or timestamp < 0
                    or previous is not None
                    and timestamp <= previous
                ):
                    raise ValueError("원본 영상 시각이 유효하지 않아 테스트를 중단합니다.")
                # Pace by media time, slowing playback instead of skipping inference frames.
                if previous is not None and self.stop.wait(
                    max(0, timestamp - previous - (time.monotonic() - dispatched))
                ):
                    break
                while self.paused.is_set() and not self.stop.wait(0.03):
                    pass
                if self.stop.is_set():
                    break
                dispatched = time.monotonic()
                self.video_timestamp, previous = timestamp, timestamp
                decoded += 1
                self.video_progress = (timestamp, decoded)
                self.metrics.capture(dispatched, frame.shape, video_time_s=timestamp)
                self.frames.put_nowait((dispatched, frame))
                # GUI acknowledges this exact frame only after its result has been displayed.
                while self.video_displayed < dispatched and not self.stop.is_set():
                    self.display_ack.wait(0.02)
                    self.display_ack.clear()
        except Exception as exc:
            self.metrics.fail(exc)
            self.errors.put(f"영상 테스트 오류: {exc}")
            self.stop.set()
        finally:
            if capture is not None:
                capture.release()

    def start_collection(self, parent):
        with self.collection_lock:
            if self.metrics.settings.get("source_type") == "video_test":
                raise ValueError(
                    "영상 테스트는 학습 수집과 별개입니다. 학습용 영상 가져오기를 사용하세요."
                )
            if self.stop.is_set() or self.collector is not None:
                raise ValueError("실행 중인 새 세션에서 한 번만 수집을 시작할 수 있습니다.")
            self.collector = FrameCollector(
                Path(parent) / f"learning-{self.metrics.session_id}", self.metrics
            )

    def process(self):
        try:
            video_input = self.metrics.settings.get("source_type") == "video_test"
            model, drone_classes = None, []
            if self.model_path:
                import torch
                from ultralytics import YOLO

                from tools.train import select_device

                inference_device = select_device(self.device, torch)
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
            yolo = YoloReacquisition()
            scan_index = 0
            last_seen = 0.0 if video_input else time.monotonic()
            while not self.stop.is_set():
                try:
                    captured, frame = self.frames.get(timeout=0.1)
                except queue.Empty:
                    continue
                started = time.monotonic()
                tracking_time = (
                    self.video_timestamp
                    if video_input
                    else captured
                    if model is not None
                    else started
                )
                if not video_input and started - captured > MAX_AGE_S:
                    yolo.lose()
                    self.metrics.discard("stale_input")
                    continue
                shape = frame.shape[:2]
                if geometry != shape:
                    if geometry is not None:
                        yolo.lose()
                    yolo.confirmed_box = None
                    template, previous, geometry, scan_index = None, None, shape, 0
                    recent_template = None
                    signature, pending = None, None
                    motion_frame = None
                    tracker_ready = False
                try:
                    selection = self.selections.get_nowait()
                    yolo.lose()
                    yolo.confirmed_box = None
                    template, previous, scan_index = None, None, 0
                    recent_template = None
                    signature, pending = None, None
                    motion_frame = None
                    tracker_ready = False
                    if selection is not None and selection[2].shape[:2] == shape:
                        template, previous, motion_frame, motion_time = selection
                        recent_template = template
                        last_seen = tracking_time
                        x1, y1, x2, y2 = previous
                        signature = color_signature(motion_frame[y1:y2, x1:x2])
                        if tracker is not None:
                            tracker.init(motion_frame, (x1, y1, x2 - x1, y2 - y1))
                            tracker_ready = True
                except queue.Empty:
                    pass
                if previous is not None and tracking_time - last_seen > MAX_AGE_S:
                    if not yolo.required:
                        yolo.lose()
                if tracking_time - last_seen > 2.0:
                    previous = None
                    motion_frame = None
                    tracker_ready = False
                whole = (0, 0, shape[1], shape[0])
                region = whole
                search_template = template
                recent = previous is not None and tracking_time - last_seen <= 1.0
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
                yolo_candidate = yolo.candidate_for(tracking_time)
                if model is not None and yolo_candidate is not None:
                    region = target_crop(yolo_candidate, shape)
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
                        device=inference_device,
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
                    detection, method = yolo.observe(
                        choose_target(detections, yolo_candidate or previous), tracking_time
                    )
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
                if not video_input and finished - captured > MAX_AGE_S:
                    self.metrics.discard("stale_result")
                    detection = None
                    candidate, pending = None, None
                    tracker_ready = False
                    if model is not None:
                        yolo.lose()
                        method = "재검색"
                elif candidate is not None:
                    pending = captured, frame, candidate
                elif detection is not None:
                    pending = None
                    previous = detection[:4]
                    last_seen = tracking_time if video_input or model is not None else finished
                    if model is not None:
                        yolo.confirmed_box = detection[:4]
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
                result_item = (captured, shape, detection, finished - started, region, method)
                self.metrics.discard(
                    "result_replaced",
                    int(
                        put_latest(
                            self.results,
                            (*result_item, frame, self.video_progress)
                            if video_input
                            else result_item,
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
        self.video_thread = None
        self.video_cancel = threading.Event()
        self.video_results = queue.Queue(maxsize=1)
        self.drag_start, self.selection, self.photo = None, None, None
        self.render_key = None
        self.result, self.selection_time = None, 0.0
        self.video_progress = (0.0, 0)
        self.metrics = None
        self.label_editor = None
        self.training_window = None
        self.display_scale = 1.0
        self.display_offset = (0, 0)
        self.video_path = None
        self.cameras = {}
        self.settings_widgets = []
        self.mode_buttons = []
        default_model = installed_model()
        self.camera = tk.StringVar()
        self.source = tk.StringVar(value="camera")
        self.page = tk.StringVar(value="tracking")
        self.mode = tk.StringVar(value="YOLO 자동 검출" if default_model else "수동 지정")
        self.model = tk.StringVar(value=str(default_model) if default_model else "")
        self.threshold = tk.StringVar(value="0.25" if default_model else "0.65")
        self.device = tk.StringVar(value="mps" if sys.platform == "darwin" else "cpu")
        self.resolution = tk.StringVar(value="1920×1080")
        self.preview_fps = tk.StringVar(value="60")
        self.video_name = tk.StringVar(value="테스트할 동영상을 선택하세요")
        self.capture_info = tk.StringVar(value="입력을 시작하면 실제 해상도를 확인할 수 있습니다.")
        self.performance = tk.StringVar(value="수신 · 화면 · 추적 FPS와 지연은 시작 후 표시됩니다.")
        self.status = tk.StringVar(value="입력을 선택하고 추적을 시작하세요.")
        self.heading = tk.StringVar(value="드론 추적")
        self.run_state = tk.StringVar(value="●  대기")
        self.observation_state = tk.StringVar(value="●  입력 대기")
        self.actual_resolution = tk.StringVar(value="—")
        self.processing_time = tk.StringVar(value="—")
        self.observed = tk.StringVar(value="—  미관측")
        self.advanced_summary = tk.StringVar()
        for variable in (self.threshold, self.device, self.resolution, self.preview_fps):
            variable.trace_add("write", self.update_advanced_summary)
        self.update_advanced_summary()

        root.title("드론 비전 추적")
        root.geometry(
            f"{min(1440, root.winfo_screenwidth() - 60)}x{min(900, root.winfo_screenheight() - 80)}"
        )
        root.minsize(1080, 760)
        root.configure(background="#f7f9fc")
        root.option_add("*Font", ("Arial", 12))
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#f7f9fc")
        style.configure("Card.TFrame", background="white")
        style.configure("TLabel", background="white", foreground="#101c30")
        style.configure("Muted.TLabel", foreground="#63728a", font=("Arial", 11))
        style.configure("Section.TLabel", font=("Arial", 13, "bold"))
        style.configure(
            "TButton",
            padding=(12, 10),
            background="#f5f7fb",
            foreground="#24344d",
            bordercolor="#d8e0eb",
            focusthickness=2,
        )
        style.map(
            "TButton",
            background=[("active", "#e7eef9"), ("disabled", "#f1f4f8")],
            foreground=[("disabled", "#a0aaba")],
        )
        style.configure(
            "Primary.TButton",
            background="#216bea",
            foreground="white",
            font=("Arial", 14, "bold"),
            padding=(12, 14),
            bordercolor="#216bea",
        )
        style.map(
            "Primary.TButton",
            background=[("active", "#1857c4"), ("disabled", "#cfdbed")],
            foreground=[("disabled", "#72839c")],
        )
        style.configure("TEntry", padding=9, fieldbackground="white", bordercolor="#d8e0eb")
        style.configure(
            "TCombobox",
            padding=8,
            fieldbackground="white",
            background="#f5f7fb",
            bordercolor="#d8e0eb",
            arrowsize=14,
        )
        style.map("TCombobox", fieldbackground=[("readonly", "white")])

        sidebar = tk.Frame(root, background="#182c43", width=190)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        tk.Label(
            sidebar,
            text="드론 비전 추적",
            background="#182c43",
            foreground="white",
            font=("Arial", 20, "bold"),
            anchor="w",
        ).pack(fill="x", padx=20, pady=(35, 30))
        for text, value in (("⊕   추적", "tracking"), ("▥   학습 · 평가", "learning")):
            tk.Radiobutton(
                sidebar,
                text=text,
                variable=self.page,
                value=value,
                command=self.show_page,
                indicatoron=False,
                anchor="w",
                background="#182c43",
                foreground="#d8e4f4",
                selectcolor="#2266c9",
                activebackground="#254567",
                activeforeground="white",
                borderwidth=0,
                font=("Arial", 15, "bold"),
                padx=18,
                pady=17,
                cursor="hand2",
            ).pack(fill="x", padx=10, pady=5)
        tk.Button(
            sidebar,
            text="종료",
            command=self.close,
            background="#182c43",
            foreground="#dc2626",
            activebackground="#254567",
            activeforeground="#dc2626",
            borderwidth=0,
            pady=12,
        ).pack(side="bottom", fill="x", padx=16, pady=16)
        tk.Label(
            sidebar,
            text="로컬 영상 추적\n카메라 · 동영상",
            background="#182c43",
            foreground="#99adc7",
            justify="left",
            anchor="w",
            font=("Arial", 11),
        ).pack(side="bottom", fill="x", padx=22, pady=12)

        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)
        content = tk.Frame(root, background="#f7f9fc")
        content.grid(row=0, column=1, sticky="nsew", padx=22, pady=20)
        content.columnconfigure(1, weight=1)
        content.rowconfigure(1, weight=1)
        header = tk.Frame(content, background="#f7f9fc")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(4, 22))
        tk.Label(
            header,
            textvariable=self.heading,
            background="#f7f9fc",
            foreground="#101c30",
            font=("Arial", 28, "bold"),
        ).pack(side="left")
        self.state_badge = tk.Label(
            header,
            textvariable=self.run_state,
            background="#e1f5ed",
            foreground="#128257",
            padx=15,
            pady=8,
            font=("Arial", 12, "bold"),
        )
        self.state_badge.pack(side="left", padx=18)

        panel_container = ttk.Frame(content)
        panel_container.grid(row=1, column=0, sticky="nsew", padx=(0, 16))
        self.panel_view = tk.Canvas(
            panel_container, width=318, background="#f7f9fc", highlightthickness=0
        )
        self.panel_view.pack(side="left", fill="both", expand=True, padx=(0, 8))
        scrollbar = ttk.Scrollbar(panel_container, command=self.panel_view.yview)
        scrollbar.pack(side="right", fill="y")
        self.panel_view.configure(yscrollcommand=scrollbar.set)
        panel_stack = ttk.Frame(self.panel_view)
        panel_window = self.panel_view.create_window(0, 0, window=panel_stack, anchor="nw")
        panel_stack.bind(
            "<Configure>",
            lambda event: self.panel_view.configure(
                width=panel_stack.winfo_reqwidth(), scrollregion=self.panel_view.bbox("all")
            ),
        )
        self.panel_view.bind(
            "<Configure>",
            lambda event: self.panel_view.itemconfigure(panel_window, width=event.width),
        )
        self.tracking_panel = ttk.Frame(panel_stack)
        self.tracking_panel.grid(row=0, column=0, sticky="new")
        self.tracking_panel.columnconfigure(0, weight=1, minsize=318)
        controls = tk.Frame(
            self.tracking_panel,
            background="white",
            padx=16,
            pady=16,
            highlightbackground="#dfe6ef",
            highlightthickness=1,
        )
        controls.grid(row=0, column=0, sticky="new")
        controls.columnconfigure(0, weight=1)
        ttk.Label(controls, text="입력", style="Section.TLabel").grid(
            row=0, sticky="w", pady=(0, 10)
        )
        self.source_buttons = []
        sources = tk.Frame(controls, background="#e4eaf2")
        sources.grid(row=1, sticky="ew", pady=(0, 12))
        for column, (text, value) in enumerate((("카메라", "camera"), ("동영상", "video"))):
            button = tk.Radiobutton(
                sources,
                text=text,
                variable=self.source,
                value=value,
                command=self.change_source,
                indicatoron=False,
                background="#e4eaf2",
                foreground="#263851",
                selectcolor="#216bea",
                activebackground="#d6e3f9",
                activeforeground="#101c30",
                borderwidth=0,
                pady=11,
                font=("Arial", 12, "bold"),
            )
            button.grid(row=0, column=column, sticky="ew", padx=2, pady=2)
            sources.columnconfigure(column, weight=1)
            self.source_buttons.append(button)
        self.camera_row = ttk.Frame(controls, style="Card.TFrame")
        self.camera_row.grid(row=2, sticky="ew")
        self.camera_row.columnconfigure(0, weight=1)
        self.camera_select = ttk.Combobox(
            self.camera_row,
            state="readonly",
            textvariable=self.camera,
            width=18,
            postcommand=self.refresh_cameras,
        )
        self.camera_select.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.refresh_button = ttk.Button(
            self.camera_row, text="↻ 새로고침", command=self.refresh_cameras
        )
        self.refresh_button.grid(row=0, column=1)
        self.video_row = ttk.Frame(controls, style="Card.TFrame")
        self.video_row.columnconfigure(0, weight=1)
        ttk.Label(
            self.video_row, textvariable=self.video_name, wraplength=270, style="Muted.TLabel"
        ).grid(row=0, sticky="w", pady=(0, 8))
        self.test_video_button = ttk.Button(
            self.video_row, text="동영상 선택", command=self.choose_video
        )
        self.test_video_button.grid(row=1, sticky="ew")
        ttk.Label(controls, text="추적 방식", style="Section.TLabel").grid(
            row=3, sticky="w", pady=(22, 10)
        )
        modes = tk.Frame(controls, background="#e4eaf2")
        modes.grid(row=4, sticky="ew")
        for column, (text, value) in enumerate(
            (("자동 검출", "YOLO 자동 검출"), ("수동 지정", "수동 지정"))
        ):
            button = tk.Radiobutton(
                modes,
                text=text,
                variable=self.mode,
                value=value,
                command=self.change_mode,
                indicatoron=False,
                background="#e4eaf2",
                foreground="#263851",
                selectcolor="#216bea",
                activebackground="#d6e3f9",
                activeforeground="#101c30",
                borderwidth=0,
                pady=11,
                font=("Arial", 12, "bold"),
            )
            button.grid(row=0, column=column, sticky="ew", padx=2, pady=2)
            modes.columnconfigure(column, weight=1)
            self.settings_widgets.append(button)
            self.mode_buttons.append(button)
        ttk.Label(controls, text="드론 모델", style="Section.TLabel").grid(
            row=5, sticky="w", pady=(22, 10)
        )
        model_row = ttk.Frame(controls, style="Card.TFrame")
        model_row.grid(row=6, sticky="ew")
        model_row.columnconfigure(0, weight=1)
        model_entry = ttk.Entry(model_row, textvariable=self.model, width=18)
        model_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        browse_button = ttk.Button(model_row, text="선택", command=self.browse)
        browse_button.grid(row=0, column=1)
        self.settings_widgets.extend((model_entry, browse_button))
        self.start_button = ttk.Button(
            controls, text="▶  추적 시작", style="Primary.TButton", command=self.start_selected
        )
        self.start_button.grid(row=7, sticky="ew", pady=(22, 10))
        actions = ttk.Frame(controls, style="Card.TFrame")
        actions.grid(row=8, sticky="ew")
        for column in range(2):
            actions.columnconfigure(column, weight=1)
        self.stop_button = ttk.Button(actions, text="■ 정지", command=self.stop, state="disabled")
        self.stop_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.clear_button = ttk.Button(
            actions, text="대상 해제", command=self.clear, state="disabled"
        )
        self.clear_button.grid(row=0, column=1, sticky="ew")
        self.pause_button = ttk.Button(
            controls, text="일시정지", command=self.pause_video, state="disabled"
        )
        self.pause_button.grid(row=9, sticky="ew", pady=(8, 0))

        advanced = tk.Frame(
            self.tracking_panel,
            background="white",
            padx=16,
            pady=12,
            highlightbackground="#dfe6ef",
            highlightthickness=1,
        )
        advanced.grid(row=1, column=0, sticky="new", pady=(12, 0))
        advanced.columnconfigure(0, weight=1)
        self.advanced_button = ttk.Button(
            advanced, text="›  고급 설정", command=self.toggle_advanced
        )
        self.advanced_button.grid(row=0, sticky="ew")
        ttk.Label(
            advanced, textvariable=self.advanced_summary, style="Muted.TLabel", wraplength=280
        ).grid(row=1, sticky="w", pady=(8, 0))
        self.advanced_fields = ttk.Frame(advanced, style="Card.TFrame")
        self.advanced_fields.columnconfigure(1, weight=1)
        for row, (label, variable, values) in enumerate(
            (
                ("품질 임계값", self.threshold, None),
                ("처리 장치", self.device, ("cuda", "mps", "cpu")),
                ("요청 해상도", self.resolution, ("1920×1080", "1280×720", "640×480")),
                ("화면 목표 FPS", self.preview_fps, ("30", "60")),
            )
        ):
            ttk.Label(self.advanced_fields, text=label).grid(
                row=row, column=0, sticky="w", padx=(0, 10), pady=4
            )
            widget = (
                ttk.Combobox(
                    self.advanced_fields,
                    textvariable=variable,
                    values=values,
                    state="readonly",
                    width=14,
                )
                if values
                else ttk.Entry(self.advanced_fields, textvariable=variable, width=14)
            )
            widget.grid(row=row, column=1, sticky="ew", pady=4)
            self.settings_widgets.append(widget)
        ttk.Label(
            self.tracking_panel,
            text="수동 지정은 영상에서 대상을 드래그하세요.\n"
            "동영상 테스트는 자동 검출을 사용합니다.",
            style="Muted.TLabel",
            background="#f7f9fc",
            wraplength=310,
        ).grid(row=2, sticky="nw", pady=12)

        self.learning_panel = tk.Frame(
            panel_stack,
            background="white",
            padx=16,
            pady=16,
            highlightbackground="#dfe6ef",
            highlightthickness=1,
        )
        self.learning_panel.columnconfigure(0, weight=1, minsize=284)
        ttk.Label(self.learning_panel, text="학습 데이터", style="Section.TLabel").grid(
            row=0, sticky="w"
        )
        ttk.Label(
            self.learning_panel,
            text="카메라 원본 사진을 수집하거나\n로컬 영상에서 학습용 프레임을 추출하세요.",
            style="Muted.TLabel",
            wraplength=284,
        ).grid(row=1, sticky="w", pady=(10, 16))
        self.collect_button = ttk.Button(
            self.learning_panel,
            text="학습 프레임 수집",
            command=self.collect_frames,
            state="disabled",
        )
        self.collect_button.grid(row=2, sticky="ew", pady=4)
        self.video_button = ttk.Button(
            self.learning_panel, text="학습용 영상 가져오기", command=self.import_video
        )
        self.video_button.grid(row=3, sticky="ew", pady=4)
        ttk.Button(self.learning_panel, text="수동 라벨 편집기", command=self.edit_labels).grid(
            row=4, sticky="ew", pady=4
        )
        ttk.Button(
            self.learning_panel, text="수동 라벨로 모델 학습", command=self.train_labels
        ).grid(row=5, sticky="ew", pady=4)
        ttk.Separator(self.learning_panel).grid(row=6, sticky="ew", pady=22)
        ttk.Label(self.learning_panel, text="측정 · 평가", style="Section.TLabel").grid(
            row=7, sticky="w"
        )
        ttk.Label(
            self.learning_panel,
            text="추적 정지 후 측정 기록을 저장하고\n같은 세션의 정답 파일로 평가하세요.",
            style="Muted.TLabel",
            wraplength=284,
        ).grid(row=8, sticky="w", pady=(10, 16))
        self.save_metrics_button = ttk.Button(
            self.learning_panel, text="측정 기록 저장", command=self.save_metrics, state="disabled"
        )
        self.save_metrics_button.grid(row=9, sticky="ew", pady=4)
        self.evaluate_button = ttk.Button(
            self.learning_panel,
            text="정답 파일로 평가",
            command=self.evaluate_metrics,
            state="disabled",
        )
        self.evaluate_button.grid(row=10, sticky="ew", pady=4)
        ttk.Label(
            self.learning_panel,
            text="수집·가져오기에서 선택한 폴더에만\n원본 사진을 저장합니다.\n"
            "수동 라벨로 모델 학습에서\n데이터 준비·검수·추가 학습을 진행하세요.",
            style="Muted.TLabel",
            wraplength=284,
        ).grid(row=11, sticky="w", pady=(20, 4))

        preview = tk.Frame(content, background="#f7f9fc")
        preview.grid(row=1, column=1, sticky="nsew")
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)
        viewport = tk.Frame(
            preview, background="#101d30", highlightbackground="#dfe6ef", highlightthickness=1
        )
        viewport.grid(row=0, column=0, sticky="nsew")
        self.canvas = tk.Canvas(viewport, background="#101d30", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self.draw_empty_preview)
        self.canvas.bind("<ButtonPress-1>", self.begin_selection)
        self.canvas.bind("<B1-Motion>", self.move_selection)
        self.canvas.bind("<ButtonRelease-1>", self.end_selection)
        self.preview_badge = tk.Label(
            viewport,
            textvariable=self.observation_state,
            background="#20354c",
            foreground="#c0d0e4",
            padx=12,
            pady=8,
            font=("Arial", 11, "bold"),
        )
        self.preview_badge.place(x=16, y=16)
        stats = tk.Frame(
            preview, background="white", highlightbackground="#dfe6ef", highlightthickness=1
        )
        stats.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        for column, (label, variable) in enumerate(
            (
                ("실제 해상도", self.actual_resolution),
                ("처리 시간", self.processing_time),
                ("대상 관측", self.observed),
            )
        ):
            stats.columnconfigure(column, weight=1, uniform="stat")
            cell = tk.Frame(stats, background="white", padx=18, pady=13)
            cell.grid(row=0, column=column, sticky="ew")
            ttk.Label(cell, text=label, style="Muted.TLabel").pack(anchor="w")
            ttk.Label(cell, textvariable=variable, font=("Arial", 18, "bold")).pack(
                anchor="w", pady=(6, 0)
            )
        ttk.Label(
            preview, textvariable=self.capture_info, style="Muted.TLabel", background="#f7f9fc"
        ).grid(row=2, sticky="w", pady=(10, 2))
        performance_label = ttk.Label(
            preview,
            textvariable=self.performance,
            style="Muted.TLabel",
            background="#f7f9fc",
            wraplength=600,
            justify="left",
        )
        performance_label.grid(row=3, sticky="ew")
        preview.bind(
            "<Configure>",
            lambda event: performance_label.configure(wraplength=max(200, event.width - 10)),
        )
        footer = ttk.Label(
            content,
            textvariable=self.status,
            style="Muted.TLabel",
            background="#f7f9fc",
            wraplength=1000,
            justify="left",
        )
        footer.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        content.bind("<Configure>", lambda event: footer.configure(wraplength=event.width))
        pending = [panel_container]
        while pending:
            widget = pending.pop()
            widget.bind("<MouseWheel>", self.scroll_controls)
            widget.bind("<Button-4>", self.scroll_controls)
            widget.bind("<Button-5>", self.scroll_controls)
            pending.extend(widget.winfo_children())
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.update_segments()
        self.refresh_cameras()
        self.next_poll = time.monotonic()
        root.after(0, self.poll)

    def show_page(self):
        learning = self.page.get() == "learning"
        self.heading.set("학습 · 평가" if learning else "드론 추적")
        self.tracking_panel.grid_remove()
        self.learning_panel.grid_remove()
        panel = self.learning_panel if learning else self.tracking_panel
        panel.grid(row=0, column=0, sticky="new")
        self.panel_view.yview_moveto(0)

    def scroll_controls(self, event):
        self.panel_view.yview_scroll(-1 if event.num == 4 or event.delta > 0 else 1, "units")
        return "break"

    def update_advanced_summary(self, *_args):
        self.advanced_summary.set(
            f"임계값 {self.threshold.get()} · 장치 {self.device.get().upper()}\n"
            f"요청 해상도 {self.resolution.get()} · 화면 {self.preview_fps.get()}fps"
        )

    def toggle_advanced(self):
        if self.advanced_fields.winfo_manager():
            self.advanced_fields.grid_remove()
            self.advanced_button.configure(text="›  고급 설정")
        else:
            self.advanced_fields.grid(row=2, sticky="ew", pady=(12, 0))
            self.advanced_button.configure(text="⌄  고급 설정")

    def change_source(self):
        if self.session is not None or self.video_thread is not None:
            return
        self.camera_row.grid_remove()
        self.video_row.grid_remove()
        (self.video_row if self.source.get() == "video" else self.camera_row).grid(
            row=2, sticky="ew"
        )
        self.update_segments()
        self.update_start_button()

    def update_segments(self):
        for variable, buttons in (
            (self.source, self.source_buttons),
            (self.mode, self.mode_buttons),
        ):
            for button in buttons:
                color = "white" if button.cget("value") == variable.get() else "#263851"
                button.configure(foreground=color, activeforeground=color)

    def update_start_button(self):
        ready = self.video_path is not None if self.source.get() == "video" else bool(self.cameras)
        self.start_button.configure(state="normal" if ready else "disabled")

    def lock_settings(self, locked):
        for widget in self.settings_widgets + self.source_buttons:
            widget.configure(
                state="disabled"
                if locked
                else "readonly"
                if widget.winfo_class() == "TCombobox"
                else "normal"
            )

    def choose_video(self):
        from tkinter import filedialog

        if self.session is not None or self.closing or self.video_thread is not None:
            return
        path = filedialog.askopenfilename(
            title="추적 테스트용 로컬 드론 영상 선택 (학습하지 않음)",
            filetypes=[("동영상", "*.mp4 *.mov *.avi *.mkv *.webm"), ("모든 파일", "*.*")],
        )
        if path:
            self.video_path = Path(path).expanduser().resolve()
            self.video_name.set(self.video_path.name)
            self.source.set("video")
            self.change_source()
            self.status.set("동영상 선택 완료 · 추적 시작을 누르세요.")

    def start_selected(self):
        if self.source.get() == "video":
            if self.video_path is None:
                return
            if self.mode.get() != "YOLO 자동 검출":
                self.mode.set("YOLO 자동 검출")
                self.change_mode()
            self.start(video_path=self.video_path)
        else:
            self.start()

    def draw_empty_preview(self, _event=None):
        if self.frame is not None:
            return
        self.render_key = None
        self.canvas.delete("empty")
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        self.canvas.create_text(
            width / 2, height / 2 - 30, text="⊕", fill="#59799c", font=("Arial", 46), tags="empty"
        )
        self.canvas.create_text(
            width / 2,
            height / 2 + 26,
            text="추적할 영상을 기다리고 있습니다",
            fill="#d9e5f4",
            font=("Arial", 16, "bold"),
            tags="empty",
        )
        self.canvas.create_text(
            width / 2,
            height / 2 + 58,
            text="입력을 선택한 뒤 추적 시작을 누르세요",
            fill="#819ab8",
            font=("Arial", 11),
            tags="empty",
        )

    def set_observation(self, text, color="#a9bdd6"):
        self.observation_state.set(f"●  {text}")
        self.preview_badge.configure(foreground=color)
        self.observed.set("●  관측 중" if text == "대상 추적 중" else "—  미관측")

    def refresh_cameras(self):
        if self.session is not None or self.video_thread is not None:
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
        self.update_start_button()

    def browse(self):
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title="신뢰하는 로컬 드론 학습 모델 선택", filetypes=[("PyTorch weights", "*.pt")]
        )
        if path:
            self.model.set(path)

    def change_mode(self, _event=None):
        self.threshold.set("0.25" if self.mode.get() == "YOLO 자동 검출" else "0.65")
        self.update_segments()

    def start(self, video_path=None):
        from tkinter import messagebox

        if self.session is not None or self.closing or self.video_thread is not None:
            return
        try:
            if self.training_window is not None and self.training_window.process is not None:
                raise ValueError("학습 창의 작업을 끝내거나 중지한 뒤 추적을 시작하세요.")
            camera = self.cameras.get(self.camera.get())
            if video_path is None and camera is None:
                raise ValueError(
                    "연결된 카메라를 선택하세요. 목록 새로고침으로 확인할 수 있습니다."
                )
            threshold = float(self.threshold.get())
            if not math.isfinite(threshold) or not 0 < threshold <= 1:
                raise ValueError("임계값은 0 초과 1 이하여야 합니다.")
            if self.resolution.get() not in ("1920×1080", "1280×720", "640×480"):
                raise ValueError("지원하는 입력 해상도를 선택하세요.")
            resolution = tuple(int(value) for value in self.resolution.get().split("×"))
            if self.preview_fps.get() not in ("30", "60"):
                raise ValueError("화면 목표 FPS는 30 또는 60을 선택하세요.")
            model = None
            if video_path is not None or self.mode.get() == "YOLO 자동 검출":
                path = Path(self.model.get()).expanduser()
                if path.suffix.lower() != ".pt" or not path.is_file():
                    raise ValueError("기존 로컬 드론 학습 .pt 파일을 선택하세요.")
                model = str(path.resolve())
            self.frame = None
            self.result = None
            self.selection_time = time.monotonic()
            self.canvas.delete("all")
            self.draw_empty_preview()
            if video_path is None:
                self.session = CameraSession(
                    camera, model, threshold, self.device.get(), resolution
                )
            else:
                if not video_path.is_file():
                    raise ValueError("기존 로컬 동영상 파일을 선택하세요.")
                self.session = CameraSession(
                    None, model, threshold, self.device.get(), resolution, video_path=video_path
                )
            self.metrics = self.session.metrics
            self.metrics.settings["preview_target_fps"] = int(self.preview_fps.get())
            self.save_metrics_button.configure(state="disabled")
            self.evaluate_button.configure(state="disabled")
            self.collect_button.configure(state="normal" if video_path is None else "disabled")
            self.video_button.configure(state="disabled")
            self.test_video_button.configure(state="disabled")
            self.pause_button.configure(state="normal" if video_path is not None else "disabled")
            self.start_button.configure(state="disabled")
            self.camera_select.configure(state="disabled")
            self.refresh_button.configure(state="disabled")
            self.lock_settings(True)
            self.stop_button.configure(state="normal")
            self.clear_button.configure(state="normal" if model is None else "disabled")
            self.run_state.set("●  준비 중")
            self.set_observation("입력 연결 중")
            self.actual_resolution.set("—")
            self.processing_time.set("—")
            self.status.set(
                f"영상 테스트 준비 중 · {video_path.name}"
                if video_path is not None
                else f"{self.camera.get()} 연결 중…"
            )
        except ValueError as exc:
            messagebox.showerror("설정 확인", str(exc))

    def stop(self):
        if self.session is not None:
            self.session.stop.set()
            self.status.set("정지 중 · 입력 해제 대기…")
            self.run_state.set("●  정지 중")
        self.stop_button.configure(state="disabled")
        self.clear_button.configure(state="disabled")
        self.frame = None
        self.canvas.delete("all")
        self.drag_start = None
        self.result = None
        self.set_observation("입력 대기")
        self.processing_time.set("—")
        self.draw_empty_preview()

    def collect_frames(self):
        from tkinter import filedialog, messagebox

        session = self.session
        if (
            session is None
            or session.stop.is_set()
            or session.metrics.settings.get("source_type") == "video_test"
        ):
            return
        parent = filedialog.askdirectory(title="원본 학습 사진을 저장할 로컬 폴더 선택")
        if parent:
            try:
                session.start_collection(parent)
                self.collect_button.configure(state="disabled")
                self.status.set(f"학습 사진 수집 · {session.collector.root}")
            except (OSError, ValueError) as exc:
                messagebox.showerror("학습 수집 실패", str(exc))

    def test_video(self):
        from tkinter import filedialog

        if self.session is not None or self.closing or self.video_thread is not None:
            return
        path = filedialog.askopenfilename(
            title="추적 테스트용 로컬 드론 영상 선택 (학습하지 않음)",
            filetypes=[("동영상", "*.mp4 *.mov *.avi *.mkv *.webm"), ("모든 파일", "*.*")],
        )
        if path:
            if self.mode.get() != "YOLO 자동 검출":
                self.mode.set("YOLO 자동 검출")
                self.change_mode()
            self.start(video_path=Path(path).expanduser().resolve())

    def pause_video(self):
        session = self.session
        if (
            session is None
            or session.stop.is_set()
            or session.metrics.settings.get("source_type") != "video_test"
        ):
            return
        if session.paused.is_set():
            session.paused.clear()
        else:
            session.paused.set()
        self.pause_button.configure(text="재생" if session.paused.is_set() else "일시정지")
        self.run_state.set("●  일시정지" if session.paused.is_set() else "●  실행 중")

    def import_video(self):
        from tkinter import filedialog

        if self.session is not None or self.closing or self.video_thread is not None:
            return
        if self.training_window is not None and self.training_window.process is not None:
            from tkinter import messagebox

            messagebox.showerror("학습 작업 실행 중", "학습 창의 작업을 끝내거나 중지하세요.")
            return
        video = filedialog.askopenfilename(
            title="추가 학습용 로컬 동영상 선택",
            filetypes=[("동영상", "*.mp4 *.mov *.avi *.mkv *.webm"), ("모든 파일", "*.*")],
        )
        if not video:
            return
        parent = filedialog.askdirectory(title="추출한 원본 프레임을 저장할 폴더 선택")
        if not parent:
            return
        from uuid import uuid4

        output = Path(parent) / f"video-{uuid4().hex[:12]}"
        self.video_cancel.clear()
        self.video_button.configure(state="disabled")
        self.test_video_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.lock_settings(True)
        self.run_state.set("●  프레임 추출 중")
        self.status.set("동영상 프레임 추출 중 · 카메라 없이 로컬 파일을 읽습니다.")
        self.video_thread = threading.Thread(
            target=self.extract_video, args=(video, output), daemon=True
        )
        self.video_thread.start()

    def extract_video(self, video, output):
        from tools.video_frames import extract_video

        try:
            report = extract_video(video, output, cancel=self.video_cancel)
            self.video_results.put((output, len(report["samples"]), None))
        except Exception as exc:
            self.video_results.put((output, 0, str(exc)))

    def finish_video_import(self):
        from tkinter import messagebox

        try:
            output, count, error = self.video_results.get_nowait()
        except queue.Empty:
            return
        self.video_thread = None
        if self.closing:
            return
        self.video_button.configure(state="normal")
        self.test_video_button.configure(state="normal")
        self.lock_settings(False)
        self.run_state.set("●  대기")
        self.refresh_cameras()
        if error:
            self.status.set(f"동영상 가져오기 실패 · {output}")
            messagebox.showerror("동영상 가져오기 실패", error)
        else:
            self.status.set(f"동영상 {count}장 추출 완료 · {output}")
            messagebox.showinfo(
                "라벨 검수 후 추가 학습",
                f"{count}장 저장: {output}\n\n"
                "수동 라벨 편집기에서 드론 박스와 배경을 검수 완료 저장(annotations.json)하세요.\n"
                "수동 라벨로 모델 학습에서 독립 촬영 그룹을 지정해 준비·학습합니다.\n"
                "자세한 순서: docs/retraining.md",
            )

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
            self.render_key = None
            self.set_observation("대상 지정 대기")
            self.status.set("대상 해제 · 드론을 다시 드래그하세요.")

    def begin_selection(self, event):
        if self.frame is not None and self.session and not self.session.model_path:
            x, y = self.display_offset
            h, w = self.frame.shape[:2]
            if not (
                x <= event.x < x + w * self.display_scale
                and y <= event.y < y + h * self.display_scale
            ):
                return
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
        x, y = self.display_offset
        start = (
            (self.drag_start[0] - x) / self.display_scale,
            (self.drag_start[1] - y) / self.display_scale,
        )
        end = ((event.x - x) / self.display_scale, (event.y - y) / self.display_scale)
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
                self.render_key = None
        self.drag_start = None
        self.canvas.delete("selection")

    def poll(self):
        import cv2
        from PIL import Image, ImageTk

        self.finish_video_import()
        session = self.session
        if session is not None:
            video_input = session.metrics.settings.get("source_type") == "video_test"
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
                    self.video_button.configure(state="normal")
                    self.test_video_button.configure(state="normal")
                    self.pause_button.configure(state="disabled", text="일시정지")
                    self.update_start_button()
                    self.camera_select.configure(state="readonly")
                    self.refresh_button.configure(state="normal")
                    self.lock_settings(False)
                    self.stop_button.configure(state="disabled")
                    self.clear_button.configure(state="disabled")
                    self.run_state.set("●  오류" if session.metrics.error else "●  정지")
                    self.set_observation("입력 종료")
                    self.frame = None
                    self.result = None
                    self.processing_time.set("—")
                    self.canvas.delete("all")
                    self.draw_empty_preview()
                    if video_input and session.metrics.settings.get("video_complete"):
                        self.status.set("영상 테스트 완료 · 측정 기록 저장 / 정답 파일로 평가 가능")
                    elif self.status.get().startswith("정지 중"):
                        self.status.set(
                            "영상 테스트 중단 · 전체 영상 평가는 완료 후 가능합니다."
                            if video_input
                            else "정지 · 카메라 해제 완료"
                        )
            elif self.drag_start is None:
                try:
                    self.result = session.results.get_nowait()
                    if video_input:
                        self.last_display, self.frame = self.result[0], self.result[6]
                        self.video_progress = self.result[7]
                        self.result = self.result[:6]
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
                if self.frame is not None and (video_input or age <= MAX_AGE_S):
                    frame = self.frame
                    h, w = frame.shape[:2]
                    self.actual_resolution.set(f"{w}×{h}")
                    paused = video_input and session.paused.is_set()
                    self.run_state.set("●  일시정지" if paused else "●  실행 중")
                    self.capture_info.set(
                        f"영상 원본 {w}×{h} · {self.video_progress[0]:.2f}초"
                        if video_input
                        else f"실제 {w}×{h} / 요청 {session.resolution[0]}×{session.resolution[1]}"
                        + (f" · 수동 {session.manual_engine}" if not session.model_path else "")
                    )
                    if video_input:
                        observation = (
                            self.result
                            if self.result is not None
                            and self.result[0] == captured
                            and self.result[1] == (h, w)
                            else None
                        )
                    else:
                        observation = observation_for_preview(self.result, captured, (h, w), now)
                    detection = observation[2] if observation else None
                    self.processing_time.set(
                        f"{observation[3] * 1000:.0f}ms" if observation else "—"
                    )
                    state = "대상 추적 중" if detection else "대상 미관측"
                    if observation and not detection:
                        if observation[5] == "재검색":
                            state = "대상 소실 · 재검색"
                        elif observation[5].startswith("재획득 확인 중"):
                            state = observation[5]
                    self.set_observation(state, "#46df98" if detection else "#a9bdd6")
                    info = (
                        "대상 미관측"
                        if session.model_path
                        else "드론을 드래그해 지정하세요 / 대상 소실"
                    )
                    if observation and observation[5] == "재검색":
                        info = "대상 소실 · 추적 갱신 중단 · 현재 영상에서 재검색"
                    elif observation and observation[5].startswith("재획득 확인 중"):
                        info = f"{observation[5]} · 추적 갱신 중단 · 다음 관측 대기"
                    region = observation[4] if observation else None
                    if region == (0, 0, w, h):
                        region = None
                    box = tuple(int(v) for v in detection[:4]) if detection else None
                    if detection:
                        x1, y1, x2, y2 = box
                        center = ((x1 + x2) // 2, (y1 + y2) // 2)
                        info = (
                            f"최근 관측 · {observation[5]} 품질 {detection[4]:.2f} · "
                            f"중심 오차 x={center[0] - w // 2:+d}, y={center[1] - h // 2:+d}px · "
                            f"{(now - observation[0]) * 1000:.0f}ms 전"
                        )
                    canvas_size = (self.canvas.winfo_width(), self.canvas.winfo_height())
                    # Published frames stay unchanged; only pixel inputs invalidate the image.
                    render_key = (captured, id(frame), box, region, canvas_size)
                    if render_key != self.render_key:
                        rendered = frame.copy()
                        cv2.drawMarker(
                            rendered, (w // 2, h // 2), (255, 255, 255), cv2.MARKER_CROSS, 20, 1
                        )
                        if region is not None:
                            cv2.rectangle(rendered, region[:2], region[2:], (255, 180, 0), 1)
                        if box is not None:
                            cv2.rectangle(rendered, box[:2], box[2:], (80, 220, 80), 2)
                            cv2.line(rendered, (w // 2, h // 2), center, (80, 220, 80), 1)
                        self.display_scale = min(canvas_size[0] / w, canvas_size[1] / h)
                        size = (
                            max(1, int(w * self.display_scale)),
                            max(1, int(h * self.display_scale)),
                        )
                        preview = cv2.resize(rendered, size, interpolation=cv2.INTER_AREA)
                        rgb = cv2.cvtColor(preview, cv2.COLOR_BGR2RGB)
                        self.photo = ImageTk.PhotoImage(Image.fromarray(rgb))
                        self.canvas.delete("all")
                        self.display_offset = (
                            (canvas_size[0] - size[0]) // 2,
                            (canvas_size[1] - size[1]) // 2,
                        )
                        self.canvas.create_image(
                            *self.display_offset, anchor="nw", image=self.photo
                        )
                        self.render_key = render_key
                        session.metrics.displayed(captured, time.monotonic())
                    processing = (
                        f" · 추적 처리 {observation[3] * 1000:.0f}ms" if observation else ""
                    )
                    if observation and observation[4] != (0, 0, w, h):
                        x1, y1, x2, y2 = observation[4]
                        processing += f" · 크롭 {x2 - x1}×{y2 - y1}"
                    if video_input:
                        state = "일시정지" if session.paused.is_set() else "재생"
                        count = self.video_progress[1]
                        self.status.set(
                            f"영상 테스트 {state} · {count}/"
                            f"{session.metrics.settings['video_frame_count']}프레임 · "
                            f"{info}{processing}"
                        )
                        if observation is not None:
                            session.video_displayed = captured
                            session.display_ack.set()
                    else:
                        self.status.set(f"{info}{processing} · 영상 {age * 1000:.0f}ms")
                elif age > MAX_AGE_S:
                    self.frame = None
                    self.canvas.delete("all")
                    self.set_observation("새 영상 대기")
                    self.run_state.set("●  입력 대기")
                    self.processing_time.set("—")
                    self.draw_empty_preview()
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
        now = time.monotonic()
        fps = 30 if self.preview_fps.get() == "30" else 60
        # Keep the cadence across render time; do not queue missed refreshes.
        self.next_poll = max(self.next_poll + 1 / fps, now)
        self.root.after(max(1, math.ceil((self.next_poll - now) * 1000)), self.poll)

    def edit_labels(self):
        from tools.label_editor import LabelEditor

        if self.closing:
            return
        if self.label_editor is not None and self.label_editor.window.winfo_exists():
            self.label_editor.window.lift()
        else:
            self.label_editor = LabelEditor(self.root)

    def train_labels(self):
        from tools.training_gui import TrainingWindow

        if self.closing:
            return
        if self.training_window is not None and self.training_window.window.winfo_exists():
            self.training_window.window.lift()
        else:
            self.training_window = TrainingWindow(
                self.root, can_run=lambda: self.session is None and self.video_thread is None
            )

    def close(self):
        if (
            self.label_editor is not None
            and self.label_editor.window.winfo_exists()
            and not self.label_editor.request_close()
        ):
            return
        if (
            self.training_window is not None
            and self.training_window.window.winfo_exists()
            and not self.training_window.request_close()
        ):
            if self.training_window.closing:
                self.root.after(50, self.close)
            return
        self.closing = True
        self.video_cancel.set()
        self.stop()
        if (
            self.session
            and self.session.alive()
            or self.video_thread
            and self.video_thread.is_alive()
        ):
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
