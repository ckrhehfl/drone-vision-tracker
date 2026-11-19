"""Actual vision sketch executed against a byte-level PCA9685 ACK-latch model."""

import os
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def vision_firmware(tmp_path_factory):
    compiler = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
    if compiler is None and Path("C:/msys64/mingw64/bin/g++.exe").is_file():
        compiler = "C:/msys64/mingw64/bin/g++.exe"
    assert compiler, "Native C++ compiler required"
    env = {**os.environ, "PATH": str(Path(compiler).parent) + os.pathsep + os.environ["PATH"]}
    folder, binaries = tmp_path_factory.mktemp("vision-native"), {}
    for enabled in (0, 1):
        binary = folder / (f"vision-{enabled}" + (".exe" if os.name == "nt" else ""))
        subprocess.run(
            [
                compiler,
                "-std=c++11",
                "-Wall",
                "-Wextra",
                "-Werror",
                f"-DVISION_HARDWARE_ENABLED={enabled}",
                "-I",
                str(ROOT / "tests/fixtures/vision_follow"),
                "-I",
                str(ROOT / "firmware/vision_follow"),
                str(ROOT / "tests/fixtures/vision_follow/harness.cpp"),
                "-o",
                str(binary),
            ],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        binaries[enabled] = binary
    return binaries, env


@pytest.mark.parametrize(
    "scenario",
    [
        "retained",
        "endpoints",
        "full-off",
        "bad-mode",
        "bad-clock",
        "bad-tick",
        "diag-mode",
        "diag-off",
        "diag-range",
        "diag-clock",
        "diag-restart",
        "diag-i2c",
        "diag-normal",
        "diag-normal-alt",
        "diag-armed",
        "hold",
        "watchdog",
        "ping",
        "replay",
        "rearm",
        "session",
        "stale",
        "malformed",
        "overflow",
        "bounds-low",
        "bounds-high",
        "cross",
        "reverse-cross",
        "slow-read",
        "slow-command",
        "hello",
        "fail0",
        "fail1",
        "fail2",
        "fail3",
        "fail4",
    ],
)
def test_real_vision_firmware_guards(vision_firmware, scenario):
    binaries, env = vision_firmware
    subprocess.run([str(binaries[1]), scenario], check=True, env=env)


def test_default_vision_build_cannot_arm(vision_firmware):
    binaries, env = vision_firmware
    subprocess.run([str(binaries[0]), "disabled"], check=True, env=env)
    subprocess.run([str(binaries[0]), "diag-normal"], check=True, env=env)


@pytest.mark.parametrize("center_after_ack", [False, True])
def test_controller_to_real_vision_sketch_tracks_both_axes_and_holds(
    vision_firmware, center_after_ack
):
    from tools.vision_motors import MotorProfile, MotorWorker, TicksLink

    binaries, env = vision_firmware
    process = subprocess.Popen(
        [str(binaries[1]), "protocol"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
    )

    class Device:
        def __init__(self):
            self.buffer = bytearray(self.exchange("@POLL"))
            self.commands = []

        def exchange(self, line):
            process.stdin.write(line + "\n")
            process.stdin.flush()
            result = process.stdout.readline()
            assert result
            return bytes.fromhex(result.strip())

        def reset_input_buffer(self):
            self.buffer.clear()

        def write(self, data):
            self.commands.append(data.split(b",", 1)[0])
            self.buffer.extend(self.exchange(data.decode().strip()))
            return len(data)

        def read(self, count):
            data = bytes(self.buffer[:count])
            del self.buffer[:count]
            return data

        def close(self):
            process.stdin.close()
            assert process.wait(timeout=3) == 0, process.stderr.read()

    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    device, profile, clock = Device(), MotorProfile(), Clock()
    link = TicksLink(device, profile, clock=clock, sleep=clock.sleep)
    try:
        link.connect()
        assert link.ticks == (208, 205) and not link.armed
        controller = profile.controller(link.ticks)
        controller._clock = clock
        link.request("ARM")
        if center_after_ack:
            worker = MotorWorker("fake", profile)
            worker.controller = controller
            worker.ticks = link.ticks
            worker.session = SimpleNamespace(
                stop=threading.Event(), serial_write_lock=threading.Lock()
            )
            for _ in range(3):
                clock.sleep(0.045)
                controller.update(clock(), (100, 100), (70, 40, 90, 60))
            assert profile.command_ticks(controller.snapshot()) == (207, 205)
            worker._follow_both_cycle(link)
            assert device.commands[-1] == b"SET_TICKS" and worker.ticks == (208, 205)
            device.exchange("@TICK,50")
            clock.sleep(0.04)
            controller.update(clock(), (100, 100), (30, 40, 50, 60))
            clock.sleep(0.01)
            controller.update(clock(), (100, 100), (40, 40, 60, 60))
            assert profile.command_ticks(controller.snapshot()) == (208, 205)
            worker._follow_both_cycle(link)
            assert device.commands[-1] == b"HOLD" and worker.ticks == (207, 205)
            assert profile.command_ticks(controller.snapshot()) == (207, 205)
            device.exchange("@TICK,100")
            assert link.request("HOLD") == (207, 205)
            return

        for _ in range(12):
            clock.sleep(0.05)
            command = controller.update(clock(), (100, 100), (70, 20, 80, 30))
            if command.state == "TRACK":
                link.request("SET_TICKS", *profile.command_ticks(command))
            else:
                link.request("HOLD")
            device.exchange("@TICK,50")
        link.request("HOLD")
        pan, tilt = link.ticks
        assert pan < 208 and tilt > 205  # Rightward and upward camera motion on installed signs.
        device.exchange("@TICK,100")
        assert link.request("HOLD") == (pan, tilt)
        clock.sleep(0.2)
        assert controller.snapshot().state == "HOLD"
        link.request("DISARM")
        process.stdin.write("@STATE\n")
        process.stdin.flush()
        assert process.stdout.readline().strip() == f"{pan},{tilt},0,4"
    finally:
        link.close()
