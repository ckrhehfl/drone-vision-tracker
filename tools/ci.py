"""Run the same hardware-free checks locally and in GitHub Actions."""

import compileall
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_firmware(root: Path) -> None:
    sources = [p for p in (root / "firmware").rglob("*") if p.suffix in {".ino", ".cpp", ".c"}]
    if sources:
        raise RuntimeError(
            "Arduino sources detected: add a pinned arduino-cli compile-only check "
            "before CI can pass. "
            "Never upload firmware or access a device from CI."
        )
    print("SKIP Arduino compile: no firmware source exists.", flush=True)


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
