"""Compile and exercise the real sketch with byte-level I2C failure injection."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def firmware(tmp_path_factory):
    directory = tmp_path_factory.mktemp("pan0-native")
    compiler = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
    if compiler is None and Path("C:/msys64/mingw64/bin/g++.exe").is_file():
        compiler = "C:/msys64/mingw64/bin/g++.exe"
    assert compiler, "Native C++ compiler is required for the firmware regression tests"
    environment = {
        **os.environ,
        "PATH": str(Path(compiler).parent) + os.pathsep + os.environ["PATH"],
    }
    binaries = {}
    for name, flags in {
        "enabled": [
            "-DPAN0_HARDWARE_ENABLED=1",
            "-DPAN0_MIN_TICKS=200",
            "-DPAN0_START_TICKS=205",
            "-DPAN0_MAX_TICKS=210",
        ],
        "disabled": [],
        "center-enabled": ["-DPAN01_CENTER205_ONLY=1", "-DPAN0_HARDWARE_ENABLED=1"],
        "center-disabled": ["-DPAN01_CENTER205_ONLY=1"],
        "invalid-center": ["-DPAN01_CENTER205_ONLY=2", "-DPAN0_HARDWARE_ENABLED=1"],
        "cross-byte": [
            "-DPAN0_HARDWARE_ENABLED=1",
            "-DPAN0_MIN_TICKS=250",
            "-DPAN0_START_TICKS=255",
            "-DPAN0_MAX_TICKS=260",
        ],
    }.items():
        binary = directory / (name + (".exe" if os.name == "nt" else ""))
        subprocess.run(
            [
                compiler,
                "-std=c++11",
                "-Wall",
                "-Wextra",
                "-Werror",
                *flags,
                "-I",
                str(ROOT / "tests/fixtures/pan0_follow"),
                "-I",
                str(ROOT / "firmware/pan0_follow"),
                str(ROOT / "tests/fixtures/pan0_follow/harness.cpp"),
                "-o",
                str(binary),
            ],
            check=True,
            env=environment,
            capture_output=True,
            text=True,
        )
        binaries[name] = binary
    return binaries, environment


@pytest.mark.parametrize(
    "mode",
    [
        "retained",
        "retained-start",
        "unreadable",
        "other-active",
        "equal-counters",
        "bad-mode",
        "initial",
        "track-hold",
        "replay",
        "ping",
        "hello",
        "conflict",
        "watchdog",
        "slow-read",
        "i2c",
        "partial",
        "mismatch",
        "bad-set",
        "overflow",
        "partial-line",
        "flood",
    ],
)
def test_real_sketch_guards(firmware, mode):
    binaries, environment = firmware
    subprocess.run([str(binaries["enabled"]), mode], check=True, env=environment)


@pytest.mark.parametrize("name", ["disabled", "cross-byte", "center-disabled", "invalid-center"])
def test_uncalibrated_builds_cannot_arm(firmware, name):
    binaries, environment = firmware
    subprocess.run([str(binaries[name]), "disabled"], check=True, env=environment)


def test_disabled_build_cannot_adopt_retained_start(firmware):
    binaries, environment = firmware
    subprocess.run([str(binaries["disabled"]), "retained-disabled"], check=True, env=environment)


@pytest.mark.parametrize("mode", ["center", "center-reject", "center-partial", "center-mismatch"])
def test_centering_only_uses_both_205_ticks(firmware, mode):
    binaries, environment = firmware
    subprocess.run([str(binaries["center-enabled"]), mode], check=True, env=environment)
