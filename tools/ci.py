"""Run the same hardware-free checks locally and in GitHub Actions."""

import compileall
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_firmware(root: Path) -> None:
    sources = [p for p in (root / "firmware").rglob("*") if p.suffix in {".ino", ".cpp", ".c"}]
    if not sources:
        print("SKIP Arduino compile: no firmware source exists.", flush=True)
        return
    pan0 = root / "firmware/pan0_follow/pan0_follow.ino"
    vision = root / "firmware/vision_follow/vision_follow.ino"
    if any(source not in {pan0, vision} for source in sources):
        raise RuntimeError("Unexpected firmware source: add an explicit pinned compile-only check.")
    cli = shutil.which("arduino-cli")
    bundled = Path(
        "/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli"
    )
    if cli is None and bundled.is_file():
        cli = str(bundled)
    if cli is None:
        raise RuntimeError("Install arduino-cli 1.5.1 and arduino:avr@1.8.8 for compile-only CI.")
    version = json.loads(subprocess.check_output([cli, "version", "--format", "json"], text=True))
    if version.get("VersionString") != "1.5.1":
        raise RuntimeError("Arduino CLI version must be 1.5.1")
    cores = json.loads(
        subprocess.check_output([cli, "core", "list", "--format", "json"], text=True)
    )
    if not any(
        p["id"] == "arduino:avr" and p.get("installed_version") == "1.8.8"
        for p in cores.get("platforms", [])
    ):
        raise RuntimeError("Arduino AVR core version must be 1.8.8")
    with tempfile.TemporaryDirectory(prefix="pan0-compile-") as directory:
        pan0_builds = (
            ("disabled", ""),
            ("center-disabled", "-DPAN01_CENTER205_ONLY=1"),
            ("center-enabled", "-DPAN01_CENTER205_ONLY=1 -DPAN0_HARDWARE_ENABLED=1"),
            (
                "synthetic-profile",
                "-DPAN0_HARDWARE_ENABLED=1 -DPAN0_MIN_TICKS=200 "
                "-DPAN0_START_TICKS=205 -DPAN0_MAX_TICKS=210",
            ),
        )
        vision_builds = (("disabled", ""), ("enabled", "-DVISION_HARDWARE_ENABLED=1"))
        for source in sorted(sources):
            builds = pan0_builds if source == pan0 else vision_builds
            for name, flags in builds:
                print(
                    f"RUN Arduino Uno compile-only: {source.parent.name}/{name}; no port/upload",
                    flush=True,
                )
                subprocess.run(
                    [
                        cli,
                        "compile",
                        "--fqbn",
                        "arduino:avr:uno",
                        "--build-path",
                        str(Path(directory) / source.parent.name / name),
                        "--build-property",
                        f"compiler.cpp.extra_flags={flags}",
                        str(source.parent),
                    ],
                    cwd=root,
                    check=True,
                )


def main() -> int:
    commands = [
        ["-m", "ruff", "check", "."],
        ["-m", "ruff", "format", "--check", "."],
        ["-m", "tools.check_design_package"],
        ["-m", "tools.validate_config"],
        ["-m", "pytest"],
    ]
    try:
        for args in commands:
            print("RUN:", sys.executable, *args, flush=True)
            subprocess.run([sys.executable, *args], cwd=ROOT, check=True)
        for directory in ("tools", "tests", "src"):
            if not compileall.compile_dir(ROOT / directory, quiet=1):
                raise RuntimeError(f"Syntax check failed: {directory}")
        check_firmware(ROOT)
    except (OSError, ValueError, subprocess.CalledProcessError, RuntimeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: software checks only; camera, AI accuracy, Serial and motors NOT TESTED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
