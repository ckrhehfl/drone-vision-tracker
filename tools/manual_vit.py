"""Run the pinned OpenCV Zoo ViT model on MPS; preprocessing follows OpenCV 5.0."""

import math
import warnings
from functools import lru_cache


@lru_cache(maxsize=1)
def _mps_model(model_path):
    import torch

    try:
        from onnx2torch import convert
    except ImportError as exc:
        raise ValueError(
            "수동 MPS 패키지가 없습니다. requirements-macos.lock.txt로 설치하세요."
        ) from exc
    # Repeated tracing retains MPS allocations; reuse only the stateless pinned network.
    with torch.inference_mode(), warnings.catch_warnings():
        warnings.simplefilter("ignore", torch.jit.TracerWarning)
        warnings.filterwarnings("ignore", message="Using a non-tuple sequence")
        model = convert(model_path).eval()
        samples = (torch.zeros(1, 3, 128, 128), torch.zeros(1, 3, 256, 256))
        # Fixed shapes fold shape/slice metadata without GPU synchronizations.
        model = torch.jit.freeze(torch.jit.trace(model, samples).eval().to("mps"))
        gpu_samples = tuple(sample.to("mps") for sample in samples)
        # Later JIT optimization must finish before the first live observation.
        for _ in range(3):
            model(*gpu_samples)
        torch.mps.synchronize()
    return model


class MPSVitTracker:
    def __init__(self, model_path, threshold):
        import torch

        if not torch.backends.mps.is_available():
            raise ValueError("MPS를 사용할 수 없습니다. 장치를 cpu로 선택해 다시 시작하세요.")
        self.threshold, self.score, self.rect = threshold, 0.0, None
        self.model = _mps_model(str(model_path))
        self.mean = torch.tensor((0.485, 0.456, 0.406), device="mps").view(1, 3, 1, 1)
        # OpenCV computes 1/Scalar as conjugate/norm, not channel-wise division.
        norm = 0.229**2 + 0.224**2 + 0.225**2
        self.scale = torch.tensor((0.229 / norm, -0.224 / norm, -0.225 / norm), device="mps").view(
            1, 3, 1, 1
        )
        hann = [0.5 * (1 - math.cos(2 * math.pi * (i + 1) / 17)) for i in range(16)]
        self.window = [y * x for y in hann for x in hann]

    def _crop(self, frame, factor, size):
        import cv2
        import numpy as np
        import torch

        x, y, width, height = self.rect
        side = math.ceil(math.sqrt(width * height) * factor)
        x0, y0 = x + math.trunc((width - side) / 2), y + math.trunc((height - side) / 2)
        crop = np.zeros((side, side, 3), dtype=np.uint8)
        h, w = frame.shape[:2]
        left, top, right, bottom = (
            max(0, x0),
            max(0, y0),
            min(w, x0 + side),
            min(h, y0 + side),
        )
        if right > left and bottom > top:
            crop[top - y0 : bottom - y0, left - x0 : right - x0] = frame[top:bottom, left:right]
        resized = cv2.resize(crop, (size, size))
        # OpenCV's pinned tracker normalizes BGR without swapping channels.
        tensor = torch.from_numpy(resized.transpose(2, 0, 1).copy()).to("mps", dtype=torch.float32)
        return (tensor.unsqueeze(0) / 255 - self.mean) * self.scale, side, (x0, y0)

    def init(self, frame, box):
        self.rect = tuple(int(v) for v in box)
        self.template = self._crop(frame, 2, 128)[0]
        self.score = 0.0

    def _decode(self, values, side, origin):
        if len(values) != 1280 or not all(math.isfinite(v) for v in values):
            self.score = 0.0
            return False, self.rect
        scores = [v * window for v, window in zip(values[:256], self.window, strict=True)]
        index = max(range(256), key=scores.__getitem__)
        self.score = scores[index]
        if not self.threshold <= self.score <= 1:
            return False, self.rect
        y, x = divmod(index, 16)
        cx, cy = (x + values[768 + index]) / 16, (y + values[1024 + index]) / 16
        width, height = values[256 + index], values[512 + index]
        box = (
            math.floor((cx - width / 2) * side + origin[0]),
            math.floor((cy - height / 2) * side + origin[1]),
            math.floor(width * side),
            math.floor(height * side),
        )
        if box[2] < 8 or box[3] < 8:
            return False, self.rect
        self.rect = box
        return True, box

    def update(self, frame):
        import torch

        with torch.inference_mode():
            search, side, origin = self._crop(frame, 4, 256)
            outputs = self.model(self.template, search)
            if tuple(tuple(v.shape) for v in outputs) != (
                (1, 1, 16, 16),
                (1, 2, 16, 16),
                (1, 2, 16, 16),
            ):
                raise ValueError("수동 ViT 모델 출력 크기가 다릅니다.")
            values = torch.cat([v.reshape(-1) for v in outputs]).cpu().tolist()
        return self._decode(values, side, origin)

    def getTrackingScore(self):
        return self.score
