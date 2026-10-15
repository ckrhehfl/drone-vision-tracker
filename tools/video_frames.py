"""Extract local video frames for human-reviewed retraining; never start training."""

import argparse
import json
import math
import uuid
from pathlib import Path

from tools.frame_collection import SAMPLE_INTERVAL_S
from tools.training_data import sha256

MAX_SAMPLES = 6000


def extract_video(video, output, interval=SAMPLE_INTERVAL_S, cancel=None):
    video, output = Path(video).expanduser().resolve(), Path(output).expanduser().resolve()
    if not video.is_file():
        raise ValueError("기존 로컬 동영상 파일을 선택하세요.")
    if type(interval) not in (int, float) or not math.isfinite(interval) or interval <= 0:
        raise ValueError("추출 간격은 유한한 양수(초)여야 합니다.")
    if output.exists():
        raise ValueError("출력 폴더가 이미 있습니다. 새 폴더를 사용하세요.")
    import cv2

    digest = sha256(video)
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": 1,
        "session_id": str(uuid.uuid4()),
        "source_type": "video_frames",
        "status": "failed",
        "original_video": str(video),
        "original_video_sha256": digest,
        "timestamp_source": "opencv_pos_msec",
        "sample_interval_s": interval,
        "frames_decoded": 0,
        "samples": [],
        "error": None,
    }
    capture = None
    try:
        (output / "frames").mkdir()
        capture = cv2.VideoCapture(str(video))
        if not capture.isOpened():
            raise ValueError("동영상을 열 수 없습니다. 파일·코덱을 확인하세요.")
        expected = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        if not math.isfinite(expected) or expected < 1 or expected != int(expected):
            raise ValueError("전체 프레임 수를 확인할 수 없어 영상의 완전성을 검증하지 못했습니다.")
        previous, next_sample = None, 0.0
        while True:
            if cancel is not None and cancel.is_set():
                raise ValueError(
                    "동영상 가져오기가 취소됐습니다. 부분 결과는 학습에 쓰지 않습니다."
                )
            ok, frame = capture.read()
            if not ok:
                break
            timestamp = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000
            if (
                not math.isfinite(timestamp)
                or timestamp < 0
                or previous is not None
                and timestamp <= previous
            ):
                raise ValueError("영상 시각을 확인할 수 없습니다. FPS로 시각을 만들지 않습니다.")
            previous = timestamp
            report["frames_decoded"] += 1
            if timestamp + 1e-9 < next_sample:
                continue
            if len(report["samples"]) >= MAX_SAMPLES:
                raise ValueError(
                    "추출 한도 6000장을 초과했습니다. 간격을 늘려 새 폴더에 추출하세요."
                )
            name = f"frames/frame-{report['frames_decoded']:06d}.png"
            ok, encoded = cv2.imencode(".png", frame)
            if not ok:
                raise OSError("동영상 프레임 PNG 인코딩 실패")
            path = output / name
            path.write_bytes(encoded.tobytes())
            report["samples"].append(
                {
                    "frame_id": report["frames_decoded"],
                    "timestamp_s": timestamp,
                    "width": int(frame.shape[1]),
                    "height": int(frame.shape[0]),
                    "image_path": name,
                    "sha256": sha256(path),
                }
            )
            next_sample = timestamp + interval
        if not report["samples"]:
            raise ValueError("디코딩한 프레임이 없습니다.")
        if report["frames_decoded"] != int(expected):
            raise ValueError("디코딩한 프레임 수가 영상 정보와 다릅니다. 부분 결과는 거부합니다.")
        if sha256(video) != digest:
            raise ValueError("추출 중 원본 동영상이 변경됐습니다.")
        report["status"] = "completed"
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        if capture is not None:
            capture.release()
        (output / "samples.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
        )
        (output / "annotations.example.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "session_id": report["session_id"],
                    "status": "needs_review",
                    "target_class": "drone",
                    "frames": [
                        {"frame_id": row["frame_id"], "box": "needs_label"}
                        for row in report["samples"]
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=SAMPLE_INTERVAL_S)
    args = parser.parse_args(argv)
    try:
        report = extract_video(args.video, args.output, args.interval)
    except Exception as exc:
        print(f"동영상 가져오기 실패: {exc}")
        return 1
    print(f"{len(report['samples'])}장 추출 완료: {args.output.resolve()}")
    print("annotations.example.json을 복사해 전체 라벨과 촬영 분할을 검수한 뒤 추가 학습하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
