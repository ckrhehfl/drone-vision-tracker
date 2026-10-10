"""Build reviewed learning data from measured camera stills and compare local YOLO weights."""

import argparse
import json
import math
import shutil
from pathlib import Path

import yaml

from tools.camera_gui import overlap
from tools.prepare_dataset import write_metadata
from tools.tracking_metrics import validate_box, validate_record
from tools.train import prepare_runtime, select_device
from tools.training_data import SPLITS, check_dataset, clear_label_caches, is_sha256, sha256
from tools.validate_config import strict_json


def read_json(path):
    return strict_json(Path(path).read_text(encoding="utf-8"))


def local_input(parent, name):
    if not isinstance(name, str) or not name.strip():
        raise ValueError("로컬 record/samples/annotations 파일 경로가 필요합니다.")
    return (parent / name).resolve()


def load_source(entry, parent):
    if not isinstance(entry, dict) or entry.get("split") not in SPLITS:
        raise ValueError("각 촬영 그룹에 train/val/test 중 하나를 지정하세요.")
    if not isinstance(entry.get("capture_id"), str) or not entry["capture_id"].strip():
        raise ValueError("독립성을 검수한 촬영 그룹 capture_id가 필요합니다.")
    paths = {
        key: local_input(parent, entry.get(key)) for key in ("record", "samples", "annotations")
    }
    provenance = {key + "_sha256": sha256(path) for key, path in paths.items()}
    record, collection, annotations = (read_json(paths[key]) for key in paths)
    rows = validate_record(record)
    for value in (collection, annotations):
        if (
            not isinstance(value, dict)
            or type(value.get("schema_version")) is not int
            or value["schema_version"] != 1
            or value.get("session_id") != record["session_id"]
        ):
            raise ValueError("측정·수집·라벨 파일의 버전/session_id가 일치해야 합니다.")
    if collection.get("source_type") != "camera_stills" or collection.get("status") != "completed":
        raise ValueError(
            "정상 종료된 카메라 원본 사진 수집만 지원합니다. "
            "영상에서 추출한 자료는 기존 video manifest를 사용하세요."
        )
    if annotations.get("status") != "reviewed" or annotations.get("target_class") != "drone":
        raise ValueError(
            "드론 정답을 사람이 검수한 annotations"
            "(status=reviewed, target_class=drone)가 필요합니다."
        )
    samples, labels = collection.get("samples"), annotations.get("frames")
    if not isinstance(samples, list) or not samples or not isinstance(labels, list):
        raise ValueError("저장된 원본 사진과 라벨 목록이 필요합니다.")
    by_id = {}
    for label in labels:
        if (
            not isinstance(label, dict)
            or type(label.get("frame_id")) is not int
            or label["frame_id"] in by_id
            or "box" not in label
        ):
            raise ValueError("라벨 frame_id가 잘못됐거나 중복됐습니다.")
        by_id[label["frame_id"]] = label["box"]
    prepared, seen = [], set()
    root = paths["samples"].parent
    for sample in samples:
        if (
            not isinstance(sample, dict)
            or type(sample.get("frame_id")) is not int
            or not 1 <= sample["frame_id"] <= len(rows)
            or sample["frame_id"] in seen
        ):
            raise ValueError("수집 frame_id가 기록에 없거나 중복됐습니다.")
        frame_id = sample["frame_id"]
        seen.add(frame_id)
        row = rows[frame_id - 1]
        if (
            type(sample.get("width")) is not int
            or type(sample.get("height")) is not int
            or type(sample.get("timestamp_s")) not in (int, float)
            or any(sample.get(key) != row[key] for key in ("width", "height", "timestamp_s"))
        ):
            raise ValueError("수집 사진의 원본 크기·시각이 측정 기록과 다릅니다.")
        relative = sample.get("image_path")
        if relative != f"frames/frame-{frame_id:06d}.png":
            raise ValueError("수집 사진 경로는 frames/frame-NNNNNN.png 형식이어야 합니다.")
        image = (root / relative).resolve()
        if (
            not image.is_relative_to(root / "frames")
            or not image.is_file()
            or not is_sha256(sample.get("sha256"))
            or sha256(image) != sample["sha256"]
        ):
            raise ValueError("원본 사진이 없거나 경로·해시가 다릅니다.")
        try:
            from PIL import Image

            with Image.open(image) as actual:
                if actual.size != (row["width"], row["height"]):
                    raise ValueError("원본 사진의 실제 크기가 기록과 다릅니다.")
                actual.verify()
        except ImportError as exc:
            raise ValueError("기존 macOS/학습 환경의 Pillow가 필요합니다.") from exc
        if frame_id not in by_id:
            raise ValueError(
                "저장된 모든 사진을 라벨링해야 합니다. 미라벨링을 배경으로 취급하지 않습니다."
            )
        target = validate_box(by_id[frame_id], row["width"], row["height"])
        case = None
        if entry["split"] == "train":
            prediction = row["box"]
            case = (
                "unprocessed"
                if not row["processed"]
                else "background_false_box"
                if target is None and prediction is not None
                else "background_correct"
                if target is None
                else "missed"
                if prediction is None
                else "mislocalized"
                if overlap(target, prediction) < 0.5
                else "matched"
            )
        prepared.append({"image": image, "sample": sample, "target": target, "case": case})
    if seen != set(by_id):
        raise ValueError("라벨 frame_id는 저장된 사진 목록과 정확히 일치해야 합니다.")
    if provenance != {key + "_sha256": sha256(path) for key, path in paths.items()}:
        raise ValueError("준비 중 측정·수집·라벨 파일이 변경됐습니다. 다시 검수하세요.")
    return record["session_id"], prepared, provenance


def prepare(plan_path, output, version):
    plan_path, output = Path(plan_path).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("출력 폴더가 이미 있습니다. 기존 자료를 보존하고 새 버전을 사용하세요.")
    plan_digest = sha256(plan_path)
    plan = read_json(plan_path)
    if (
        not isinstance(plan, dict)
        or plan.get("schema_version") != 1
        or type(plan.get("schema_version")) is not int
        or plan.get("status") != "reviewed"
        or plan.get("split_rule") != "capture_group_before_case_selection"
        or not isinstance(version, str)
        or not version.strip()
    ):
        raise ValueError(
            "촬영 그룹 분할을 검수한 계획(status=reviewed, "
            "split_rule=capture_group_before_case_selection)과 버전이 필요합니다."
        )
    sources = plan.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("학습 데이터 계획에 sources가 필요합니다.")
    groups, sessions_seen, loaded = {}, set(), []
    for entry in sources:
        session_id, photos, provenance = load_source(entry, plan_path.parent)
        split, capture_id = entry["split"], entry["capture_id"]
        if session_id in sessions_seen or capture_id in groups and groups[capture_id] != split:
            raise ValueError(
                "같은 기록을 재사용하거나 같은 촬영 그룹을 여러 split으로 나눌 수 없습니다."
            )
        sessions_seen.add(session_id)
        groups[capture_id] = split
        loaded.append((entry, session_id, photos, provenance))
    if set(groups.values()) != set(SPLITS):
        raise ValueError("독립 train/val/test 촬영 그룹을 모두 준비하세요.")
    output.mkdir(parents=True, exist_ok=False)
    sessions, cases, origins = [], [], []
    for number, (entry, session_id, photos, provenance) in enumerate(loaded, 1):
        split, group = entry["split"], f"source-{number:03d}"
        images = []
        for photo in photos:
            sample, target = photo["sample"], photo["target"]
            name = f"{sample['frame_id']:06d}.png"
            destination = output / "images" / split / group / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(photo["image"], destination)
            if sha256(destination) != sample["sha256"]:
                raise ValueError(
                    "준비 중 원본 사진이 변경됐습니다. 출력 자료를 검수하고 새 버전을 사용하세요."
                )
            label = output / "labels" / split / group / Path(name).with_suffix(".txt")
            label.parent.mkdir(parents=True, exist_ok=True)
            text = ""
            if target is not None:
                x1, y1, x2, y2 = target
                width, height = sample["width"], sample["height"]
                values = (
                    (x1 + x2) / (2 * width),
                    (y1 + y2) / (2 * height),
                    (x2 - x1) / width,
                    (y2 - y1) / height,
                )
                text = "0 " + " ".join(f"{v:.12f}" for v in values) + "\n"
            label.write_text(text, encoding="utf-8")
            images.append(
                {
                    "path": destination.relative_to(output).as_posix(),
                    "sha256": sample["sha256"],
                    "source_path": f"{group}/{sample['image_path']}",
                    "capture_elapsed_s": sample["timestamp_s"],
                }
            )
            if split == "train":
                cases.append(
                    {
                        "path": images[-1]["path"],
                        "measurement_session_id": session_id,
                        "frame_id": sample["frame_id"],
                        "case": photo["case"],
                    }
                )
        sessions.append(
            {
                "session_id": group,
                "source_type": "photos",
                "capture_id": entry["capture_id"],
                "split": split,
                "images": images,
            }
        )
        origins.append(
            {
                "measurement_session_id": session_id,
                "split": split,
                "capture_id": entry["capture_id"],
                **provenance,
            }
        )
    (output / "training_cases.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if sha256(plan_path) != plan_digest:
        raise ValueError("준비 중 촬영 분할 계획이 변경됐습니다. 새 버전으로 다시 준비하세요.")
    return write_metadata(
        output,
        version,
        sessions,
        retrain_plan_sha256=plan_digest,
        learning_sources=origins,
        split_rule=plan["split_rule"],
    )


def compare(args):
    spec, dataset = check_dataset(args.data, args.manifest)

    def check_validation(validator):
        loaded = validator.dataloader.dataset
        actual = {"images": len(loaded), "objects": sum(len(v["cls"]) for v in loaded.labels)}
        if actual != dataset["splits"]["val"]:
            raise ValueError("실제 validation 이미지·라벨 수가 검사한 데이터와 다릅니다.")

    output = args.output.resolve()
    if output.exists():
        raise ValueError("비교 출력 폴더가 이미 있습니다. 새 경로를 사용하세요.")
    weights = {"baseline": args.baseline, "candidate": args.candidate}
    if any(not path.is_file() or path.suffix != ".pt" for path in weights.values()):
        raise ValueError("신뢰하는 로컬 baseline/candidate .pt 파일이 필요합니다.")
    if args.imgsz < 32 or args.imgsz % 32 or args.batch < 1:
        raise ValueError("imgsz는 32의 양의 배수, batch는 양수여야 합니다.")
    torch, YOLO = prepare_runtime()
    device = select_device(args.device, torch)
    validator = None
    if device == "mps":
        from tools.mps_training import MPSDetectionValidator

        validator = MPSDetectionValidator
    output.mkdir(parents=True, exist_ok=False)
    data = output / "validation-data.yaml"
    data.write_text(yaml.safe_dump(spec), encoding="utf-8")
    report = {
        "status": "running",
        "test_evaluated": False,
        "dataset": dataset,
        "device": device,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "models": {},
    }
    try:
        for role, path in weights.items():
            digest = sha256(path)
            model = YOLO(str(path.resolve()), task="detect")
            if model.task != "detect" or model.names != {0: "drone"}:
                raise ValueError("비교 모델은 단일 클래스 0: drone 검출 모델이어야 합니다.")
            model.add_callback("on_val_start", check_validation)
            clear_label_caches(spec)
            result = model.val(
                validator=validator,
                data=str(data),
                split="val",
                device=device,
                imgsz=args.imgsz,
                batch=args.batch,
                workers=0,
                plots=False,
                save=False,
                save_json=False,
                project=str(output),
                name=role,
                exist_ok=False,
            )
            if int(sum(result.nt_per_class)) != dataset["splits"]["val"]["objects"]:
                raise ValueError("실제 validation 라벨 수가 검사한 데이터와 다릅니다.")
            scores = {
                "precision": float(result.box.mp),
                "recall": float(result.box.mr),
                "map50": float(result.box.map50),
                "map50_95": float(result.box.map),
            }
            if not all(math.isfinite(v) and 0 <= v <= 1 for v in scores.values()):
                raise ValueError("validation 지표가 유효하지 않습니다.")
            if sha256(path) != digest:
                raise ValueError("비교 중 가중치가 변경됐습니다. 실행을 다시 준비하세요.")
            report["models"][role] = {"sha256": digest, "metrics": scores}
        if check_dataset(args.data, args.manifest)[1] != dataset:
            raise ValueError("비교 중 데이터가 변경됐습니다. 동일한 검증 자료로 다시 실행하세요.")
        report["delta"] = {
            key: report["models"]["candidate"]["metrics"][key]
            - report["models"]["baseline"]["metrics"][key]
            for key in scores
        }
        report["status"] = "completed"
    except BaseException:
        report["status"] = "failed"
        raise
    finally:
        (output / "comparison.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
        )
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("prepare")
    build.add_argument("--plan", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--version", required=True)
    validation = commands.add_parser("compare")
    for name in ("data", "manifest", "baseline", "candidate", "output"):
        validation.add_argument(f"--{name}", type=Path, required=True)
    validation.add_argument("--device", choices=("auto", "cuda", "mps", "cpu"), default="mps")
    validation.add_argument("--imgsz", type=int, default=640)
    validation.add_argument("--batch", type=int, default=4)
    args = parser.parse_args(argv)
    try:
        result = (
            prepare(args.plan, args.output, args.version)
            if args.command == "prepare"
            else compare(args)
        )
    except (OSError, ValueError, RuntimeError, ImportError, yaml.YAMLError) as exc:
        print(f"재학습 파이프라인 실패: {exc}")
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
