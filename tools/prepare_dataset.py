"""Prepare Seraphim stage-one data or grouped real photos; never start training."""

import argparse
import hashlib
import json
import math
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

import yaml

from tools.training_data import BOX_EDGE_TOLERANCE, IMAGE_SUFFIXES, SPLITS, check_dataset, sha256

DATASET = "lgrzybowski/seraphim-drone-detection-dataset"
REVISION = "5b1c8348bf67e6836e3dbc42b053a5a4bfdec925"
ARCHIVES = {
    "train/images/batch_001.zip": (
        "77acad100d29009c5fd1da9576a82c104e4d20d9799eb1c8ab6d125f04f02eb0"
    ),
    "train/images/batch_002.zip": (
        "bb639b4b1cc4f8a9d3c73a9d49aa73d70200823e0b08316443f7f21aaf2fa5e3"
    ),
    "train/images/batch_003.zip": (
        "2cb7e04a7171d37ecd22f62fd94a30cd88c08a31c0dedbd49aa65c7e47f1f8a3"
    ),
    "train/images/batch_004.zip": (
        "359015cb9be1378709ef7de3cb90ecfd28e2a1fc9105a4e4d77c0afac3b865a9"
    ),
    "train/labels/batch_001.zip": (
        "92118e616f1c27fd4ea1ea582606b7a19d2bb1925809f54d6994cfb2496c235b"
    ),
    "test/images/batch_001.zip": "b7814e04e1f71507e81fa82c1d3ba81d329be4d1b74b516e03486fb61d1e9f66",
    "test/labels/batch_001.zip": "acc23d50d24ff7b5f2ec212907e3b951451b8c94686234eae9b52379aed40350",
}


def verify_archives(source: Path, download: bool) -> None:
    for name, digest in ARCHIVES.items():
        path = source / name
        if not path.exists() and download:
            path.parent.mkdir(parents=True, exist_ok=True)
            partial = path.with_suffix(".zip.part")
            with (
                urllib.request.urlopen(
                    f"https://huggingface.co/datasets/{DATASET}/resolve/{REVISION}/{name}",
                    timeout=60,
                ) as response,
                partial.open("xb") as stream,
            ):
                shutil.copyfileobj(response, stream)
            if sha256(partial) != digest:
                raise ValueError(f"Downloaded archive checksum mismatch: {partial}")
            partial.rename(path)
        if not path.is_file() or sha256(path) != digest:
            raise ValueError(f"Missing or changed pinned archive: {path}")
        print(f"Verified: {name}", flush=True)


def flat_members(archive: zipfile.ZipFile, suffixes: set[str]) -> list[str]:
    names = archive.namelist()
    if len(names) != len(set(names)) or any(
        Path(name).name != name
        or "\\" in name
        or name.startswith(".")
        or Path(name).suffix.lower() not in suffixes
        for name in names
    ):
        raise ValueError("Archives must contain unique flat image/label filenames.")
    return sorted(names)


def detection_labels(content: bytes) -> tuple[bytes, int]:
    """Convert valid YOLO polygons to enclosing boxes; preserve existing box labels."""
    rows, converted = [], 0
    for row in content.decode("utf-8").splitlines():
        fields = row.split()
        if len(fields) > 5:
            coords = list(map(float, fields[1:]))
            if (
                fields[0] != "0"
                or len(coords) < 6
                or len(coords) % 2
                or not all(math.isfinite(v) and 0 <= v <= 1 for v in coords)
            ):
                raise ValueError("Invalid class-0 normalized polygon label.")
            left, right = min(coords[::2]), max(coords[::2])
            top, bottom = min(coords[1::2]), max(coords[1::2])
            row = "0 " + " ".join(
                str(v) for v in ((left + right) / 2, (top + bottom) / 2, right - left, bottom - top)
            )
            converted += 1
        rows.append(row)
    return (("\n".join(rows) + "\n").encode() if converted else content), converted


def write_metadata(root: Path, version: str, sessions: list[dict], **metadata) -> dict:
    data, manifest = root / "dataset.yaml", root / "manifest.json"
    if data.exists() or manifest.exists():
        raise ValueError("Dataset metadata already exists; preserve it and use a new version.")
    data.write_text(
        yaml.safe_dump({"path": ".", **{s: f"images/{s}" for s in SPLITS}, "names": {0: "drone"}}),
        encoding="utf-8",
    )
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "needs_review",
                "dataset_version": version,
                "sessions": sessions,
                **metadata,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    # Preparation validates integrity without claiming that a person reviewed the labels.
    return check_dataset(data, manifest, require_ready=False)[1]


def prepare_seraphim(args) -> dict:
    if not 0 < args.val_fraction < 1 or args.seed < 0:
        raise ValueError("val_fraction must be between 0 and 1; seed must be nonnegative.")
    root = args.output.resolve()
    if root.exists():
        raise ValueError("Output already exists; use a new dataset version directory.")
    verify_archives(args.source.resolve(), args.download)
    root.mkdir(parents=True, exist_ok=False)
    converted_labels = {}
    sessions = {
        split: {
            "session_id": f"seraphim-{split}",
            "split": split,
            "source_type": "public_dataset",
            "source_dataset": DATASET,
            "source_revision": REVISION,
            "split_independence": "unverified",
            "images": [],
        }
        for split in SPLITS
    }
    for original_split in ("train", "test"):
        with zipfile.ZipFile(args.source / original_split / "labels/batch_001.zip") as labels:
            remaining = set(flat_members(labels, {".txt"}))
            for archive_name in ARCHIVES:
                if not archive_name.startswith(f"{original_split}/images/"):
                    continue
                with zipfile.ZipFile(args.source / archive_name) as images:
                    for name in flat_members(images, IMAGE_SUFFIXES):
                        label_name = str(Path(name).with_suffix(".txt"))
                        if label_name not in remaining:
                            raise ValueError(f"Missing, repeated or shared label: {name}")
                        remaining.remove(label_name)
                        content = images.read(name)
                        digest = hashlib.sha256(content).hexdigest()
                        # ponytail: content-hash split; unknown capture groups stay unverified.
                        bucket = int(
                            hashlib.sha256(f"{args.seed}:{digest}".encode()).hexdigest(), 16
                        )
                        split = (
                            "test"
                            if original_split == "test"
                            else "val"
                            if bucket / (1 << 256) < args.val_fraction
                            else "train"
                        )
                        image = root / "images" / split / name
                        label = root / "labels" / split / label_name
                        if image.exists() or label.exists():
                            raise ValueError(f"Repeated output filename: {name}")
                        image.parent.mkdir(parents=True, exist_ok=True)
                        label.parent.mkdir(parents=True, exist_ok=True)
                        image.write_bytes(content)
                        label_content, converted = detection_labels(labels.read(label_name))
                        label.write_bytes(label_content)
                        if converted:
                            converted_labels[f"{original_split}/labels/{label_name}"] = converted
                        sessions[split]["images"].append(
                            {
                                "path": image.relative_to(root).as_posix(),
                                "source_path": f"{original_split}/images/{name}",
                                "sha256": digest,
                            }
                        )
            if remaining:
                raise ValueError("Archive contains labels without images.")
    return write_metadata(
        root,
        args.version,
        list(sessions.values()),
        source_archives=ARCHIVES,
        attribution={
            "author": "Łukasz Grzybowski / Seraphim Defence Systems",
            "url": f"https://huggingface.co/datasets/{DATASET}/tree/{REVISION}",
            "license": "CC-BY-4.0",
        },
        split_policy={"val_fraction": args.val_fraction, "seed": args.seed, "test": "upstream"},
        label_preparation={
            "polygon_to_box": converted_labels,
            "box_edge_tolerance": BOX_EDGE_TOLERANCE,
        },
    )


def prepare_photos(args) -> dict:
    root = args.output.resolve()
    sessions = []
    for split in SPLITS:
        groups = {}
        for image in sorted((root / "images" / split).rglob("*")):
            if image.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            relative = image.relative_to(root / "images" / split)
            if len(relative.parts) < 2:
                raise ValueError("Put photos inside images/<split>/<capture_id>/ folders.")
            capture_id = relative.parts[0]
            groups.setdefault(capture_id, []).append(
                {
                    "path": image.relative_to(root).as_posix(),
                    "source_path": relative.as_posix(),
                    "sha256": sha256(image),
                }
            )
        sessions.extend(
            {
                "session_id": f"{split}-{capture_id}",
                "split": split,
                "source_type": "photos",
                "capture_id": capture_id,
                "images": entries,
            }
            for capture_id, entries in groups.items()
        )
    return write_metadata(root, args.version, sessions)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="kind", required=True)
    for kind in ("seraphim", "photos"):
        subparser = subparsers.add_parser(kind)
        subparser.add_argument("--output", type=Path, required=True)
        subparser.add_argument("--version", required=True)
        if kind == "seraphim":
            subparser.add_argument("--source", type=Path, required=True, help="Pinned ZIP cache")
            subparser.add_argument("--download", action="store_true", help="Fetch missing ZIPs")
            subparser.add_argument("--val-fraction", type=float, default=0.1)
            subparser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        summary = prepare_seraphim(args) if args.kind == "seraphim" else prepare_photos(args)
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        print(f"Prepared: {args.output.resolve()}. Review manifest/labels before setting ready.")
    except (OSError, ValueError, zipfile.BadZipFile, yaml.YAMLError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
