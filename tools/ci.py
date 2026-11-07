"""Run the same hardware-free checks locally and in GitHub Actions."""

import compileall
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVO_SKETCHES = {
    Path("firmware/servo0_manual_safe/servo0_manual_safe.ino"),
    Path("firmware/servo01_manual_safe/servo01_manual_safe.ino"),
}


def check_firmware(root: Path) -> None:
    sources = [p for p in (root / "firmware").rglob("*") if p.suffix in {".ino", ".cpp", ".c"}]
    if not sources:
        print("SKIP Arduino compile: no firmware source exists.", flush=True)
        return
    present = {source.relative_to(root) for source in sources}
    if not present.issubset(SERVO_SKETCHES):
        raise RuntimeError("Arduino sources detected without a registered compile-only check.")
    cli = os.environ.get("ARDUINO_CLI") or shutil.which("arduino-cli")
    bundled = Path(
        "/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli"
    )
    if not cli and sys.platform == "darwin" and bundled.is_file():
        cli = str(bundled)
    if not cli:
        raise RuntimeError("Install arduino-cli 1.5.1 for the compile-only check.")

    def read_json(*args):
        return json.loads(subprocess.check_output([cli, *args, "--format", "json"], text=True))

    if read_json("version").get("VersionString") != "1.5.1":
        raise RuntimeError("Compile-only check requires arduino-cli 1.5.1.")
    if not any(
        core.get("id") == "arduino:avr" and core.get("installed_version") == "1.8.8"
        for core in read_json("core", "list").get("platforms", [])
    ):
        raise RuntimeError("Compile-only check requires arduino:avr@1.8.8.")
    for sketch in sorted(present):
        with tempfile.TemporaryDirectory(prefix="servo-compile-") as build:
            subprocess.run(
                [
                    cli,
                    "compile",
                    "--fqbn",
                    "arduino:avr:uno",
                    "--build-path",
                    build,
                    str(root / sketch.parent),
                ],
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
    except (subprocess.CalledProcessError, RuntimeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: software checks only; camera, AI accuracy, Serial and motors NOT TESTED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
