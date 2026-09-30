"""Check documentation-package integrity only; never access hardware or network."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
LOCAL_DIRECTORIES = {".git", ".venv", "venv", "artifacts", ".pytest_cache", ".ruff_cache"}
REQUIRED = (
    "README.md",
    "AGENTS.md",
    "docs/01_system_design.md",
    "docs/02_serial_protocol.md",
    "docs/03_dataset_and_evaluation.md",
    "docs/04_implementation_plan.md",
    "docs/05_decisions.md",
    "docs/06_sources.md",
    "docs/AI_드론_비전_추적_설계서_v1.0.docx",
    "config/project.example.json",
    "config/calibration.example.json",
    "prompts/implementation.md",
    "prompts/review.md",
    "prompts/handoff.md",
    ".gitignore",
    ".github/pull_request_template.md",
)


def markdown_files(root: Path):
    """Only inspect project documents, not installed tools or generated artifacts."""
    for directory, children, files in os.walk(root):
        if Path(directory) == root:
            children[:] = [name for name in children if name not in LOCAL_DIRECTORIES]
        for name in files:
            if name.endswith(".md"):
                yield Path(directory) / name


def main() -> int:
    errors: list[str] = []
    for name in REQUIRED:
        path = ROOT / name
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"Missing/empty: {name}")
    for path in markdown_files(ROOT):
        text = path.read_text(encoding="utf-8")
        if "" in text or "" in text:
            errors.append(f"Unresolved chat citation: {path.relative_to(ROOT)}")
        for dest in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            if re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:", dest) or dest.startswith("#"):
                continue
            dest = unquote(dest.split("#", 1)[0])
            if not (path.parent / dest).exists():
                errors.append(f"Broken local link: {path.relative_to(ROOT)} -> {dest}")
    try:
        cfg = json.loads((ROOT / "config/project.example.json").read_text(encoding="utf-8"))
        cal = json.loads((ROOT / "config/calibration.example.json").read_text(encoding="utf-8"))
        checks = {
            "hardware stays disabled": cfg["hardware_enabled"] is False,
            "serial stays disabled": cfg["serial"]["enabled"] is False,
            "explicit ARM required": cfg["serial"]["require_explicit_arm"] is True,
            "no prediction-only motion": cfg["tracking"]["move_on_predicted_only"] is False,
            "no automatic laser output": cfg["optional_indicator"]["automatic_laser_output"]
            is False,
            "calibration not fabricated": cal["completed"] is False,
            "latest-frame queue": cfg["input"]["max_queued_frames"] == 1,
        }
        for label, ok in checks.items():
            if not ok:
                errors.append("Unsafe example default: " + label)
        for axis in ("pan", "tilt"):
            for key in ("min_cd", "neutral_cd", "max_cd", "direction_sign"):
                if cal[axis][key] is not None:
                    errors.append(f"Unexpected measured example value: {axis}.{key}")
        with ZipFile(ROOT / "docs/AI_드론_비전_추적_설계서_v1.0.docx") as zf:
            if zf.testzip() is not None:
                errors.append("DOCX archive corrupted")
            if "word/document.xml" not in zf.namelist():
                errors.append("DOCX document part missing")
    except (OSError, KeyError, ValueError) as exc:
        errors.append(f"Package data read error: {exc}")
    if errors:
        for msg in errors:
            print("FAIL:", msg)
        return 1
    print("PASS: required files, Markdown links, example defaults and DOCX container.")
    print("NOT TESTED: application, AI accuracy, firmware, Serial, physical motors, agent setup.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
