"""Validate the local single-class dataset before loading any AI library."""

import hashlib
import json
import math
import re
from glob import has_magic
from pathlib import Path

import yaml

SPLITS = ("train", "val", "test")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def is_sha256(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_labels(path: Path) -> int:
    """An empty label file explicitly marks a reviewed background image."""
    rows = path.read_text(encoding="utf-8").splitlines()
    for row in rows:
        fields = row.split()
        if len(fields) != 5 or fields[0] != "0":
            raise ValueError(f"Expected class 0 and four YOLO coordinates: {path}")
        x, y, width, height = map(float, fields[1:])
        if not (
            all(math.isfinite(v) for v in (x, y, width, height))
            and 0 < width <= 1
            and 0 < height <= 1
            and width / 2 <= x <= 1 - width / 2
            and height / 2 <= y <= 1 - height / 2
        ):
            raise ValueError(f"Invalid normalized bounding box: {path}")
    return len(rows)


def check_dataset(data_path: Path, manifest_path: Path) -> tuple[dict, dict]:
    data_path = data_path.resolve()
    data = yaml.safe_load(data_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) - {"path", "train", "val", "test", "names", "nc"}:
        raise ValueError("Dataset YAML accepts only path/train/val/test/names/nc; no downloads.")
    if data.get("names") not in (["drone"], {0: "drone"}, {"0": "drone"}):
        raise ValueError("Dataset must have exactly one class: 0: drone.")
    if data.get("nc", 1) != 1 or not isinstance(data.get("path", "."), str):
        raise ValueError("Expected nc=1 and a local dataset path.")
    root = (data_path.parent / data.get("path", ".")).resolve()
    if has_magic(str(root)):
        raise ValueError("Dataset root cannot contain glob characters: *, ? or brackets.")
    for split in SPLITS:
        if data.get(split) != f"images/{split}":
            raise ValueError(f"Use the fixed local layout: {split}: images/{split}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or (
        manifest.get("schema_version") != 1
        or manifest.get("status") != "ready"
        or not isinstance(manifest.get("dataset_version"), str)
        or not manifest["dataset_version"].strip()
        or not isinstance(manifest.get("sessions"), list)
    ):
        raise ValueError(
            "Manifest needs schema_version=1, status=ready, dataset_version and sessions."
        )
    declared = {}
    session_ids = set()
    capture_splits = {}
    capture_names = {}
    for session in manifest["sessions"]:
        if not isinstance(session, dict):
            raise ValueError("Each session must be an object.")
        session_id, split = session.get("session_id"), session.get("split")
        images = session.get("images")
        if (
            not isinstance(session_id, str)
            or not session_id.strip()
            or session_id in session_ids
            or split not in SPLITS
            or not isinstance(images, list)
            or not images
        ):
            raise ValueError("Sessions need a unique ID, one split and a nonempty images list.")
        session_ids.add(session_id)
        original = session.get("original_video")
        capture_hash = session.get("original_video_sha256")
        if not isinstance(original, str) or not original.strip() or not is_sha256(capture_hash):
            raise ValueError("Session needs original_video identity and original_video_sha256.")
        for seen, identity in ((capture_splits, capture_hash), (capture_names, original)):
            if identity in seen and seen[identity] != split:
                raise ValueError("The same original capture cannot appear across splits.")
            seen[identity] = split
        for entry in images:
            if not isinstance(entry, dict):
                raise ValueError("Each image needs path, timestamp_s and reviewed sha256.")
            name, timestamp, expected = (
                entry.get("path"),
                entry.get("timestamp_s"),
                entry.get("sha256"),
            )
            if (
                isinstance(timestamp, bool)
                or not isinstance(timestamp, int | float)
                or not math.isfinite(timestamp)
                or timestamp < 0
                or not is_sha256(expected)
            ):
                raise ValueError("Image timestamp_s must be finite/nonnegative and sha256 valid.")
            if (
                not isinstance(name, str)
                or Path(name).is_absolute()
                or ".." in Path(name).parts
                or "\\" in name
            ):
                raise ValueError("Manifest image paths must be relative path strings.")
            image = (root / name).resolve()
            if not image.is_relative_to(root / "images" / split) or image in declared:
                raise ValueError(f"Duplicate image or path outside its session split: {name}")
            declared[image] = {"session_id": session_id, "sha256": expected}

    files, hashes, label_paths, counts = set(), {}, set(), {}
    inventory = []
    for split in SPLITS:
        directory = root / "images" / split
        if any(
            p.is_file() and p.suffix.lower() not in IMAGE_SUFFIXES and p.name != ".DS_Store"
            for p in directory.rglob("*")
        ):
            raise ValueError(f"Image directories accept only JPG/JPEG/PNG: {directory}")
        images = sorted(p for p in directory.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES)
        if not images:
            raise ValueError(f"Empty or missing split: {directory}")
        objects = 0
        for image in images:
            resolved = image.resolve()
            if not resolved.is_relative_to(directory) or resolved not in declared:
                raise ValueError(f"Image missing from manifest or outside dataset: {image}")
            relative = image.relative_to(directory)
            if any(part.startswith(".") for part in relative.parts):
                raise ValueError(f"Hidden image paths are skipped by YOLO: {image}")
            if "images" in relative.parts[:-1]:
                raise ValueError(f"Nested 'images' directory changes YOLO label paths: {image}")
            label = root / "labels" / split / relative.with_suffix(".txt")
            if not label.resolve().is_relative_to(root / "labels" / split):
                raise ValueError(f"Label outside dataset: {label}")
            if label in label_paths:
                raise ValueError(f"Two images share one label path: {label}")
            label_paths.add(label)
            objects += check_labels(label)
            digest = sha256(image)
            if digest != declared[resolved]["sha256"]:
                raise ValueError(f"Image sha256 differs from the reviewed manifest: {image}")
            # ponytail: exact file hashes only; review near-duplicate frames manually.
            if digest in hashes and hashes[digest] != split:
                raise ValueError(f"Identical image content across splits: {image}")
            hashes[digest] = split
            files.add(resolved)
            inventory.append(
                [
                    image.relative_to(root).as_posix(),
                    digest,
                    sha256(label),
                    declared[resolved]["session_id"],
                ]
            )
        if not objects:
            raise ValueError(f"Split needs at least one labeled drone: {split}")
        counts[split] = {"images": len(images), "objects": objects}
    if files != set(declared):
        raise ValueError("Manifest contains missing or unsupported images; use JPG/JPEG/PNG.")
    spec = {"path": str(root), "train": "images/train", "val": "images/val", "names": {0: "drone"}}
    summary = {
        "dataset_version": manifest["dataset_version"],
        "manifest_sha256": sha256(manifest_path),
        "yaml_sha256": sha256(data_path),
        "inventory_sha256": hashlib.sha256(json.dumps(inventory).encode()).hexdigest(),
        "splits": counts,
        "sessions": len(session_ids),
    }
    return spec, summary


def clear_label_caches(spec: dict) -> None:
    """Regenerate vendor caches: its size/path hash misses same-length label edits."""
    root = Path(spec["path"])
    for split in ("train", "val"):
        directory = root / spec[split]
        first = min(p for p in directory.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES)
        label = root / "labels" / split / first.relative_to(directory).with_suffix(".txt")
        label.parent.with_suffix(".cache").unlink(missing_ok=True)
