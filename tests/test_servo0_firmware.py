"""Exercise the actual Arduino sketch against local I2C and serial doubles."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.ci import check_firmware

ROOT = Path(__file__).resolve().parents[1]
SKETCH = ROOT / "firmware/servo0_manual_safe"

WIRE_HEADER = r"""
#pragma once
#include <stdint.h>
#include <stdio.h>
#include <deque>
#include <string>
#include <type_traits>

using byte = uint8_t;
#define F(value) value
#define HEX 16
extern unsigned long fakeTime;
extern unsigned long fakeReadDelay;
extern byte fakeMode1, fakeMode2, fakePrescale;
extern bool i2cOnline, timeoutFlag, timeoutOnRead;
inline unsigned long millis() { return fakeTime; }
inline void delay(unsigned long ms) { fakeTime += ms; }
template<typename T> T constrain(T x, T lo, T hi) { return x < lo ? lo : x > hi ? hi : x; }

struct SerialMock {
    std::deque<char> input;
    std::string output;
    void begin(unsigned long) {}
    size_t available() { return input.size(); }
    char read() { char c = input.front(); input.pop_front(); return c; }
    void print(const char *s) { output += s; }
    void print(char *s) { output += s; }
    void print(char c) { output += c; }
    template<typename T> void print(T n) { output += std::to_string(n); }
    void print(byte n, int) { char s[8]; snprintf(s, sizeof(s), "%X", n); output += s; }
    template<typename T> void println(T value) { print(value); output += '\n'; }
};

struct WireMock {
    byte reg = 0, data = 0, count = 0;
    void begin() {}
    void setWireTimeout(unsigned long, bool) { timeoutFlag = false; }
    bool getWireTimeoutFlag() { return timeoutFlag; }
    void beginTransmission(byte) { count = 0; }
    void write(byte value) { if (count++ == 0) reg = value; else data = value; }
    int endTransmission(bool = true) {
        if (!i2cOnline) return 4;
        if (count == 1 && fakeReadDelay) { fakeTime += fakeReadDelay; fakeReadDelay = 0; }
        if (count > 1 && reg == 1) fakeMode2 = data;
        if (count > 1 && reg == 0) fakeMode1 = data;
        if (count > 1 && reg == 0xFE) fakePrescale = data;
        return 0;
    }
    int requestFrom(byte, byte) {
        if (timeoutOnRead) { timeoutFlag = true; timeoutOnRead = false; }
        return i2cOnline ? 1 : 0;
    }
    byte read() {
        return reg == 0 ? fakeMode1 : reg == 1 ? fakeMode2 : reg == 0xFE ? fakePrescale : 0x10;
    }
};
extern SerialMock Serial;
extern WireMock Wire;
"""

PCA_HEADER = r"""
#pragma once
#include <stdint.h>
#define PCA9685_OK 0
extern uint16_t fakeOn, fakeOff, visibleOff;
extern uint8_t fakeMode2;
extern bool i2cOnline, partialFailure, timeoutFlag, timeoutOnWrite, timeoutOnPcaRead;
extern unsigned long fakeTime, fakeWriteDelay;
extern int pwmWrites;
struct PCA9685 {
    explicit PCA9685(uint8_t) {}
    uint8_t setPWM(uint8_t channel, uint16_t on, uint16_t off) {
        if (channel != 0 || !i2cOnline) return 1;
        fakeTime += fakeWriteDelay;
        fakeWriteDelay = 0;
        if (partialFailure) {
            fakeOff = (fakeOff & 0x1F00) | (off & 0xFF);
            if (!(fakeMode2 & 0x08)) visibleOff = fakeOff;
            return 1;
        }
        fakeOn = on; fakeOff = visibleOff = off; ++pwmWrites;
        if (timeoutOnWrite) { timeoutFlag = true; timeoutOnWrite = false; }
        return 0;
    }
    uint8_t getPWM(uint8_t, uint16_t *on, uint16_t *off) {
        if (!i2cOnline) return 1;
        *on = fakeOn; *off = fakeOff;
        if (timeoutOnPcaRead) { timeoutFlag = true; timeoutOnPcaRead = false; }
        return 0;
    }
};
"""

HARNESS = r"""
#include "Wire.h"
#include "PCA9685.h"
#include <assert.h>
#include <string.h>

unsigned long fakeTime = 0;
unsigned long fakeReadDelay = 0;
unsigned long fakeWriteDelay = 0;
uint16_t fakeOn = 0, fakeOff = 0x1000, visibleOff = 0x1000;
byte fakeMode1 = 0x21, fakeMode2 = 0x04, fakePrescale = 121;
bool i2cOnline = true, partialFailure = false;
bool timeoutFlag = false, timeoutOnRead = false, timeoutOnWrite = false, timeoutOnPcaRead = false;
int pwmWrites = 0;
SerialMock Serial;
WireMock Wire;
#include "servo0_manual_safe.ino"

void command(const char *text) {
    while (*text) Serial.input.push_back(*text++);
    Serial.input.push_back('\n');
    while (Serial.available()) loop();
}

int main(int argc, char **argv) {
    assert(argc == 2);
    if (!strcmp(argv[1], "range")) {
        fakeOff = visibleOff = 410;
        setup();
        assert(!ready && targetTicks == -1 && pwmWrites == 0 && visibleOff == 410);
        command("HELLO,ABCDEF12");
        assert(Serial.output.find("FAULT") != std::string::npos);
        return 0;
    }
    if (!strcmp(argv[1], "inverted")) {
        fakeOff = visibleOff = 0x1000; fakeMode2 = 0x14;
        setup();
        assert(!ready && pwmWrites == 0 && visibleOff == 0x1000);
        return 0;
    }
    if (!strcmp(argv[1], "full-off")) {
        fakeOff = visibleOff = 0x1000;
        setup();
        assert(ready && targetTicks == -1 && pwmWrites == 0);
        return 0;
    }
    if (!strcmp(argv[1], "atomic")) {
        setup(); command("HELLO,ABCDEF12"); command("INIT,ABCDEF12,1,90");
        fakeOff = visibleOff = targetTicks = destinationTicks = 260;
        command("ARM,ABCDEF12,2");
        assert((fakeMode2 & 0x08) && armed);
        command("STEP,ABCDEF12,3,-");
        partialFailure = true; fakeTime += STEP_MS; loop();
        assert(!ready && !armed && visibleOff == 260 && pwmWrites == 1);
        return 0;
    }
    if (!strcmp(argv[1], "i2c")) {
        setup(); command("HELLO,ABCDEF12"); command("INIT,ABCDEF12,1,90");
        command("ARM,ABCDEF12,2");
        assert(armed);
        i2cOnline = false; command("HOLD,ABCDEF12,3");
        assert(!ready && !armed && pwmWrites == 1);
        i2cOnline = true; command("STEP,ABCDEF12,4,+");
        assert(!ready && !armed && pwmWrites == 1);
        return 0;
    }
    if (!strcmp(argv[1], "timeout-read") || !strcmp(argv[1], "timeout-pca-read")) {
        setup(); command("HELLO,ABCDEF12"); command("INIT,ABCDEF12,1,90");
        command("ARM,ABCDEF12,2");
        timeoutOnRead = !strcmp(argv[1], "timeout-read");
        timeoutOnPcaRead = !timeoutOnRead;
        command("HOLD,ABCDEF12,3");
        assert(timeoutFlag && !ready && !armed && pwmWrites == 1);
        command("STEP,ABCDEF12,4,+");
        assert(!ready && !armed && pwmWrites == 1);
        return 0;
    }
    if (!strcmp(argv[1], "timeout-write")) {
        setup(); command("HELLO,ABCDEF12"); command("INIT,ABCDEF12,1,90");
        command("ARM,ABCDEF12,2"); command("STEP,ABCDEF12,3,+");
        timeoutOnWrite = true; fakeTime += STEP_MS; loop();
        assert(timeoutFlag && !ready && !armed && pwmWrites == 2);
        fakeTime += 100; loop(); command("STEP,ABCDEF12,4,+");
        assert(pwmWrites == 2 && visibleOff == 206);
        return 0;
    }
    if (!strcmp(argv[1], "partial-reset") || !strcmp(argv[1], "partial-inrange")) {
        bool inrange = !strcmp(argv[1], "partial-inrange");
        setup(); command("HELLO,ABCDEF12"); command("INIT,ABCDEF12,1,90");
        fakeOff = visibleOff = targetTicks = destinationTicks = inrange ? 260 : 255;
        command("ARM,ABCDEF12,2");
        command(inrange ? "STEP,ABCDEF12,3,-" : "STEP,ABCDEF12,3,+");
        partialFailure = true; fakeTime += STEP_MS; loop();
        assert(!ready && !armed && fakeOff == (inrange ? 259 : 0));
        assert(visibleOff == (inrange ? 260 : 255));
        ready = false; armed = false; session[0] = 0; lastSequence = 0; targetTicks = -1;
        partialFailure = false; setup(); command("HELLO,1234ABCD");
        assert(!ready && targetTicks == -1 && visibleOff == (inrange ? 260 : 255));
        assert(Serial.output.find("READY,SERVO0_SAFE,1234ABCD,FAULT,-1") != std::string::npos);
        return 0;
    }
    if (!strcmp(argv[1], "delay")) {
        setup(); command("HELLO,ABCDEF12"); command("INIT,ABCDEF12,1,90");
        command("ARM,ABCDEF12,2"); command("STEP,ABCDEF12,3,+");
        fakeTime += STEP_MS; fakeReadDelay = 60; loop();
        assert(fakeTime == 110 && fakeOff == 206 && pwmWrites == 2);
        loop(); fakeTime += STEP_MS - 1; loop();
        assert(fakeOff == 206 && pwmWrites == 2);
        fakeTime++; loop();
        assert(fakeOff == 207 && pwmWrites == 3);
        return 0;
    }
    if (!strcmp(argv[1], "deadline")) {
        setup(); command("HELLO,ABCDEF12"); command("INIT,ABCDEF12,1,90");
        command("ARM,ABCDEF12,2"); command("STEP,ABCDEF12,3,+");
        fakeTime += WATCHDOG_MS - 10; fakeReadDelay = 20; loop();
        assert(!armed && ready && fakeOff == 205 && pwmWrites == 1);
        return 0;
    }
    if (!strcmp(argv[1], "write-margin") || !strcmp(argv[1], "write-block")) {
        bool blocked = !strcmp(argv[1], "write-block");
        setup(); command("HELLO,ABCDEF12"); command("INIT,ABCDEF12,1,90");
        command("ARM,ABCDEF12,2"); command("STEP,ABCDEF12,3,+");
        fakeTime = WATCHDOG_MS - WRITE_MARGIN_MS - (blocked ? 0 : 1);
        fakeWriteDelay = 50; loop();
        if (blocked) assert(!armed && fakeOff == 205 && pwmWrites == 1);
        else assert(armed && fakeOff == 206 && pwmWrites == 2 && fakeTime < WATCHDOG_MS);
        return 0;
    }
    setup();
    assert(ready && !armed && targetTicks == -1 && pwmWrites == 0);
    command("STEP,ABCDEF12,1,+");
    assert(pwmWrites == 0 && !armed);
    command("HELLO,ABCDEF12");
    command("ARM,ABCDEF12,1");
    assert(pwmWrites == 0 && !armed);
    command("INIT,ABCDEF12,2,90");
    assert(pwmWrites == 1 && fakeOff == 205 && !armed);
    command("STEP,ABCDEF12,3,+");
    assert(pwmWrites == 1 && !armed);
    command("ARM,ABCDEF12,4");
    command("STEP,ABCDEF12,5,+");
    assert(pwmWrites == 1 && fakeOff == 205 && armed);
    fakeTime += STEP_MS - 1; loop();
    assert(pwmWrites == 1 && fakeOff == 205);
    fakeTime++; loop();
    assert(pwmWrites == 2 && fakeOff == 206);
    command("STEP,ABCDEF12,5,+");
    assert(pwmWrites == 2 && !armed);
    command("ARM,ABCDEF12,6");
    fakeTime += WATCHDOG_MS;
    command("STEP,ABCDEF12,7,+");
    assert(pwmWrites == 2 && !armed);
    command("ARM,ABCDEF12,8");
    command("ST\rEP,ABCDEF12,9,+");
    assert(pwmWrites == 2 && !armed);
    command("HELLO,1234ABCD");
    command("ARM,1234ABCD,1");
    command("SET,1234ABCD,2,180");
    assert(pwmWrites == 2 && fakeOff == 206);
    fakeTime += STEP_MS; loop();
    assert(pwmWrites == 3 && fakeOff == 205);
    command("STOP,1234ABCD");
    fakeTime += STEP_MS * 2; loop();
    assert(pwmWrites == 3 && fakeOff == 205);
    command("HOLD,1234ABCD,3");
    ready = false; armed = false; session[0] = 0; lastSequence = 0; targetTicks = -1;
    setup(); command("HELLO,5678ABCD");
    assert(!ready && !armed && targetTicks == -1 && pwmWrites == 3);
}
"""


def test_actual_sketch_keeps_output_locked_across_startup_timeout_and_reset(tmp_path):
    compiler = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
    if compiler is None:
        pytest.skip("C++ compiler is unavailable for the host sketch test")
    (tmp_path / "Wire.h").write_text(WIRE_HEADER, encoding="utf-8")
    (tmp_path / "PCA9685.h").write_text(PCA_HEADER, encoding="utf-8")
    source = tmp_path / "main.cpp"
    source.write_text(HARNESS, encoding="utf-8")
    binary = tmp_path / "servo0-firmware-test"
    subprocess.run(
        [
            compiler,
            "-std=c++17",
            "-I",
            str(tmp_path),
            "-I",
            str(SKETCH),
            str(source),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    for scenario in (
        "normal",
        "range",
        "inverted",
        "full-off",
        "atomic",
        "i2c",
        "timeout-read",
        "timeout-pca-read",
        "timeout-write",
        "partial-reset",
        "partial-inrange",
        "delay",
        "deadline",
        "write-margin",
        "write-block",
    ):
        subprocess.run([str(binary), scenario], check=True, capture_output=True, text=True)


def test_single_registered_sketch_compiles_without_the_other(tmp_path, monkeypatch):
    sketch = tmp_path / "firmware/servo0_manual_safe/servo0_manual_safe.ino"
    sketch.parent.mkdir(parents=True)
    sketch.write_text("void setup() {}", encoding="utf-8")
    monkeypatch.setattr("tools.ci.shutil.which", lambda _name: "/fake/arduino-cli")

    def cli_output(args, **_kwargs):
        if "version" in args:
            return json.dumps({"VersionString": "1.5.1"})
        return json.dumps({"platforms": [{"id": "arduino:avr", "installed_version": "1.8.8"}]})

    commands = []
    monkeypatch.setattr("tools.ci.subprocess.check_output", cli_output)
    monkeypatch.setattr("tools.ci.subprocess.run", lambda args, **_kwargs: commands.append(args))
    check_firmware(tmp_path)
    assert len(commands) == 1
    assert commands[0][-1] == str(sketch.parent)


DUAL_PCA_HEADER = r"""
#pragma once
#include <stdint.h>
#define PCA9685_OK 0
extern uint16_t ons[2], offs[2];
extern bool i2cOnline, timeoutFlag;
extern int pwmWrites, failOnChannel;
struct PCA9685 {
    explicit PCA9685(uint8_t) {}
    uint8_t setPWM(uint8_t channel, uint16_t on, uint16_t off) {
        if (channel > 1 || !i2cOnline || channel == failOnChannel) return 1;
        ons[channel] = on; offs[channel] = off; ++pwmWrites;
        return 0;
    }
    uint8_t getPWM(uint8_t channel, uint16_t *on, uint16_t *off) {
        if (channel > 1 || !i2cOnline) return 1;
        *on = ons[channel]; *off = offs[channel];
        return 0;
    }
};
"""

DUAL_HARNESS = r"""
#include "Wire.h"
#include "PCA9685.h"
#include <assert.h>
#include <iostream>
#include <string.h>
unsigned long fakeTime = 0, fakeReadDelay = 0;
byte fakeMode1 = 0x21, fakeMode2 = 0x04, fakePrescale = 121;
bool i2cOnline = true, timeoutFlag = false, timeoutOnRead = false;
uint16_t ons[2] = {0, 0}, offs[2] = {0x1000, 0x1000};
int pwmWrites = 0, failOnChannel = -1;
SerialMock Serial;
WireMock Wire;
#include "servo01_manual_safe.ino"
void command(const char *text) {
    while (*text) Serial.input.push_back(*text++);
    Serial.input.push_back('\n');
    while (Serial.available()) loop();
}
int main(int argc, char **argv) {
    assert(argc == 2);
    if (!strcmp(argv[1], "protocol")) {
        setup();
        std::string line;
        while (std::getline(std::cin, line)) {
            if (line.rfind("@TICK,", 0) == 0) {
                unsigned remaining = std::stoul(line.substr(6));
                while (remaining) {
                    unsigned delta = remaining < 10 ? remaining : 10;
                    fakeTime += delta; remaining -= delta; loop();
                }
            } else if (line == "@STATE") {
                std::cout << offs[0] << ':' << offs[1] << ':' << armed << std::endl;
                continue;
            } else if (line != "@POLL") command(line.c_str());
            const char *digits = "0123456789abcdef";
            for (unsigned char c : Serial.output) std::cout << digits[c >> 4] << digits[c & 15];
            Serial.output.clear();
            std::cout << std::endl;
        }
        return 0;
    }
    if (!strcmp(argv[1], "home-uninitialized")) {
        setup(); command("HELLO,ABCDEF12"); command("INIT,ABCDEF12,1,90");
        command("ARM,ABCDEF12,2"); command("HOME,ABCDEF12,3");
        fakeTime += STEP_MS; loop();
        assert(!armed && offs[0] == 205 && offs[1] == 0x1000 && pwmWrites == 1);
        assert(Serial.output.find("NOT_INITIALIZED") != std::string::npos);
        return 0;
    }
    if (!strcmp(argv[1], "home-disarmed") || !strcmp(argv[1], "home-stale")) {
        setup(); command("HELLO,ABCDEF12"); command("INIT,ABCDEF12,1,0");
        command("CHANNEL,ABCDEF12,2,1"); command("INIT,ABCDEF12,3,80");
        if (!strcmp(argv[1], "home-disarmed")) command("HOME,ABCDEF12,4");
        else {
            command("ARM,ABCDEF12,4"); command("HOME,ABCDEF12,5");
            command("HOME,ABCDEF12,5");
        }
        fakeTime += STEP_MS; loop();
        assert(!armed && offs[0] == 330 && offs[1] == 80 && pwmWrites == 2);
        return 0;
    }
    if (!strcmp(argv[1], "home") || !strcmp(argv[1], "home-stop") ||
        !strcmp(argv[1], "home-watchdog") || !strcmp(argv[1], "home-fault")) {
        setup(); command("HELLO,ABCDEF12"); command("INIT,ABCDEF12,1,0");
        command("CHANNEL,ABCDEF12,2,1"); command("INIT,ABCDEF12,3,80");
        command("ARM,ABCDEF12,4"); command("HOME,ABCDEF12,5");
        assert(Serial.output.find("ACK,ABCDEF12,5,HOME,330:80") != std::string::npos);
        if (!strcmp(argv[1], "home-watchdog")) {
            fakeTime += WATCHDOG_MS; loop();
            assert(!armed && offs[0] == 330 && offs[1] == 80);
            return 0;
        }
        if (!strcmp(argv[1], "home-fault")) failOnChannel = 1;
        fakeTime += STEP_MS; loop();
        if (!strcmp(argv[1], "home-fault")) {
            assert(!ready && !armed && offs[0] == 329 && offs[1] == 80);
            return 0;
        }
        assert(offs[0] == 329 && offs[1] == 81);
        if (!strcmp(argv[1], "home-stop")) {
            command("STOP,ABCDEF12");
            fakeTime += STEP_MS; loop();
            assert(offs[0] == 329 && offs[1] == 81);
            return 0;
        }
        unsigned sequence = 6;
        for (int i = 0; i < 124; i++) {
            if (i % 4 == 0) {
                std::string message = "HOME,ABCDEF12," + std::to_string(sequence++);
                command(message.c_str());
            }
            fakeTime += STEP_MS; loop();
        }
        assert(armed && offs[0] == 205 && offs[1] == 205);
        return 0;
    }
    if (!strcmp(argv[1], "pair") || !strcmp(argv[1], "pair-fault")) {
        bool fault = !strcmp(argv[1], "pair-fault");
        setup(); command("HELLO,ABCDEF12"); command("INIT,ABCDEF12,1,90");
        command("CHANNEL,ABCDEF12,2,1"); command("INIT,ABCDEF12,3,205");
        command("ARM,ABCDEF12,4"); command("PAIR,ABCDEF12,5,++");
        assert(Serial.output.find("ACK,ABCDEF12,5,PAIR,205:205") != std::string::npos);
        if (fault) failOnChannel = 1;
        fakeTime += STEP_MS; loop();
        if (fault) {
            assert(!ready && !armed && offs[0] == 206 && offs[1] == 205);
            int writes = pwmWrites; failOnChannel = -1; fakeTime += STEP_MS; loop();
            assert(pwmWrites == writes);
            return 0;
        }
        assert(offs[0] == 206 && offs[1] == 206);
        command("STOP,ABCDEF12,0");
        fakeTime += STEP_MS; loop();
        assert(offs[0] == 206 && offs[1] == 207);
        command("PAIR,ABCDEF12,6,0+");
        fakeTime += STEP_MS; loop();
        assert(offs[0] == 206 && offs[1] == 208);
        command("STOP,ABCDEF12,1");
        fakeTime += STEP_MS; loop();
        assert(offs[0] == 206 && offs[1] == 208);
        command("PAIR,ABCDEF12,7,++");
        std::string expected = "ACK,ABCDEF12,8,DISARMED," +
                               std::to_string(offs[0]) + ":" + std::to_string(offs[1]);
        command("DISARM,ABCDEF12,8");
        assert(Serial.output.find(expected) != std::string::npos && !armed);
        command("ARM,ABCDEF12,9"); command("PAIR,ABCDEF12,10,++");
        fakeTime += WATCHDOG_MS; loop();
        int writes = pwmWrites; fakeTime += STEP_MS; loop();
        assert(!armed && pwmWrites == writes);
        return 0;
    }
    if (!strcmp(argv[1], "pair-uninitialized")) {
        setup(); command("HELLO,ABCDEF12"); command("INIT,ABCDEF12,1,90");
        command("ARM,ABCDEF12,2"); command("PAIR,ABCDEF12,3,++");
        fakeTime += STEP_MS; loop();
        assert(!armed && offs[0] == 205 && offs[1] == 0x1000 && pwmWrites == 1);
        return 0;
    }
    if (!strcmp(argv[1], "pair-set")) {
        setup(); command("HELLO,ABCDEF12"); command("INIT,ABCDEF12,1,90");
        command("CHANNEL,ABCDEF12,2,1"); command("INIT,ABCDEF12,3,205");
        command("ARM,ABCDEF12,4"); command("PAIR,ABCDEF12,5,++");
        fakeTime += STEP_MS; loop(); command("STOP,ABCDEF12,1");
        command("CHANNEL,ABCDEF12,6,0");
        std::string expected = "ACK,ABCDEF12,7,TARGET," +
                               std::to_string(offs[0]) + ":" + std::to_string(offs[1]);
        command("SET,ABCDEF12,7,180");
        assert(Serial.output.find(expected) != std::string::npos);
        return 0;
    }
    if (!strcmp(argv[1], "hello-fresh")) {
        setup(); command("HELLO,ABCDEF12"); command("CHANNEL,ABCDEF12,1,1");
        assert(activeChannel == 1 && pwmWrites == 0);
        command("HELLO,1234ABCD");
        assert(ready && activeChannel == 0 && targetTicks == -1);
        command("INIT,1234ABCD,1,90");
        assert(offs[0] == 205 && offs[1] == 0x1000 && pwmWrites == 1);
        return 0;
    }
    if (!strcmp(argv[1], "hello-after-output")) {
        setup(); command("HELLO,ABCDEF12"); command("INIT,ABCDEF12,1,90");
        command("CHANNEL,ABCDEF12,2,1"); command("INIT,ABCDEF12,3,80");
        command("HELLO,1234ABCD");
        assert(!ready && !armed && offs[0] == 205 && offs[1] == 80 && pwmWrites == 2);
        assert(Serial.output.find("READY,SERVO01_HOME,1234ABCD,FAULT,") != std::string::npos);
        command("ARM,1234ABCD,1"); command("PAIR,1234ABCD,2,++");
        fakeTime += STEP_MS; loop();
        assert(!ready && !armed && pwmWrites == 2);
        return 0;
    }
    if (!strcmp(argv[1], "pair-bounds")) {
        setup(); command("HELLO,ABCDEF12"); command("INIT,ABCDEF12,1,0");
        command("CHANNEL,ABCDEF12,2,1"); command("INIT,ABCDEF12,3,80");
        command("ARM,ABCDEF12,4"); command("PAIR,ABCDEF12,5,+-");
        fakeTime += STEP_MS; loop();
        assert(offs[0] == 330 && offs[1] == 80 && pwmWrites == 2);
        command("PAIR,ABCDEF12,6,-+");
        assert(offs[0] == 329 && offs[1] == 81 && pwmWrites == 4);
        return 0;
    }
    if (!strcmp(argv[1], "active")) {
        offs[1] = 205;
        setup();
        assert(!ready && pwmWrites == 0 && offs[0] == 0x1000 && offs[1] == 205);
        return 0;
    }
    if (!strcmp(argv[1], "min") || !strcmp(argv[1], "max")) {
        bool min = !strcmp(argv[1], "min");
        setup(); command("HELLO,ABCDEF12"); command("CHANNEL,ABCDEF12,1,1");
        command("INIT,ABCDEF12,2,79"); command("INIT,ABCDEF12,3,331");
        assert(pwmWrites == 0 && targetTicks == -1);
        command(min ? "INIT,ABCDEF12,4,80" : "INIT,ABCDEF12,4,330");
        command("ARM,ABCDEF12,5");
        command(min ? "STEP,ABCDEF12,6,-" : "STEP,ABCDEF12,6,+");
        fakeTime += STEP_MS; loop();
        assert(offs[1] == (min ? 80 : 330) && offs[0] == 0x1000 && pwmWrites == 1);
        return 0;
    }
    setup();
    assert(ready && pwmWrites == 0);
    command("HELLO,ABCDEF12");
    command("CHANNEL,ABCDEF12,1,1");
    command("INIT,ABCDEF12,2,79");
    assert(pwmWrites == 0 && targetTicks == -1);
    command("INIT,ABCDEF12,3,205");
    assert(offs[0] == 0x1000 && offs[1] == 205 && pwmWrites == 1);
    command("ARM,ABCDEF12,4");
    command("STEP,ABCDEF12,5,+");
    fakeTime += STEP_MS; loop();
    assert(offs[1] == 206 && offs[0] == 0x1000);
    command("STOP,ABCDEF12");
    fakeTime += STEP_MS; loop();
    assert(offs[1] == 206);
    command("STEP,ABCDEF12,6,+");
    for (int i = 0; i < 10; i++) {
        fakeTime += STEP_MS; loop();
        if (i == 4) command("STEP,ABCDEF12,7,+");
    }
    assert(offs[1] == 217 && offs[0] == 0x1000);
    command("STEP,ABCDEF12,8,+");
    fakeTime += STEP_MS; loop();
    assert(offs[1] == 218);
    command("CHANNEL,ABCDEF12,9,0");
    assert(!armed && offs[0] == 0x1000);
    command("CHANNEL,ABCDEF12,10,0");
    command("INIT,ABCDEF12,11,90");
    assert(offs[0] == 205 && offs[1] == 218);
    command("ARM,ABCDEF12,12");
    command("STEP,ABCDEF12,13,-");
    fakeTime += STEP_MS; loop();
    assert(offs[0] == 204 && offs[1] == 218);
    command("CHANNEL,ABCDEF12,14,1");
    command("STEP,ABCDEF12,15,-");
    fakeTime += STEP_MS; loop();
    assert(offs[0] == 204 && offs[1] == 216);
    fakeTime += WATCHDOG_MS; loop();
    int writes = pwmWrites;
    fakeTime += STEP_MS; loop();
    assert(!armed && pwmWrites == writes);
}
"""


@pytest.fixture(scope="module")
def dual_firmware_binary(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("dual-firmware")
    compiler = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
    if compiler is None:
        pytest.skip("C++ compiler is unavailable for the host sketch test")
    (tmp_path / "Wire.h").write_text(WIRE_HEADER, encoding="utf-8")
    (tmp_path / "PCA9685.h").write_text(DUAL_PCA_HEADER, encoding="utf-8")
    source = tmp_path / "main.cpp"
    source.write_text(DUAL_HARNESS, encoding="utf-8")
    binary = tmp_path / "servo01-firmware-test"
    sketch = ROOT / "firmware/servo01_manual_safe"
    subprocess.run(
        [
            compiler,
            "-std=c++17",
            "-I",
            str(tmp_path),
            "-I",
            str(sketch),
            str(source),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return binary


def test_dual_firmware_isolates_channels_and_enforces_tilt_test_range(dual_firmware_binary):
    for scenario in (
        "normal",
        "active",
        "min",
        "max",
        "pair",
        "pair-fault",
        "pair-uninitialized",
        "pair-set",
        "pair-bounds",
        "hello-fresh",
        "hello-after-output",
        "home-uninitialized",
        "home-disarmed",
        "home-stale",
        "home",
        "home-stop",
        "home-watchdog",
        "home-fault",
    ):
        subprocess.run(
            [str(dual_firmware_binary), scenario], check=True, capture_output=True, text=True
        )


@pytest.mark.parametrize("interrupt", [False, True])
def test_auto_home_gui_and_firmware_exchange_commands(dual_firmware_binary, interrupt):
    from types import SimpleNamespace

    from tools.servo01_manual_gui import Servo01Control, Servo01Link, Servo01Window

    process = subprocess.Popen(
        [str(dual_firmware_binary), "protocol"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    class Device:
        def __init__(self):
            self.buffer = bytearray(self.exchange("@POLL"))

        def exchange(self, line):
            process.stdin.write(line + "\n")
            process.stdin.flush()
            result = process.stdout.readline()
            assert result, "host sketch terminated"
            return bytes.fromhex(result.strip())

        def write(self, data):
            self.buffer.extend(self.exchange(data.decode("ascii").strip()))
            return len(data)

        def read(self, size):
            data = bytes(self.buffer[:size])
            del self.buffer[:size]
            return data

        def advance(self, milliseconds):
            self.buffer.extend(self.exchange(f"@TICK,{milliseconds}"))

        def state(self):
            process.stdin.write("@STATE\n")
            process.stdin.flush()
            return tuple(int(value) for value in process.stdout.readline().strip().split(":"))

        def close(self):
            pass

    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

        def advance(self, seconds):
            self.now += seconds
            device.advance(round(seconds * 1000))

    class Value:
        def __init__(self, value=None):
            self.value = value

        def set(self, value):
            self.value = value

        def get(self):
            return self.value

    try:
        device = Device()
        clock = Clock()
        link = Servo01Link(device, clock)
        control = Servo01Control(link, clock)
        control.poll()
        clock.advance(2)
        control.poll()
        control.poll()
        assert link.ready and link.sketch == "SERVO01_HOME"
        assert link.initialize(0)
        control.poll()
        assert link.select(1)
        control.poll()
        assert link.initialize_tilt(80)
        control.poll()
        assert control.arm()
        control.poll()
        assert device.state() == (330, 80, 1)

        window = Servo01Window.__new__(Servo01Window)
        window.link, window.control = link, control
        window.window = SimpleNamespace(after=lambda *_args: None)
        window.closed, window.reported_fault = False, False
        window.status, window.notice, window.power_reset = Value(), Value(), Value(True)
        window.auto_home, window.auto_home_saved = Value(True), True
        window.auto_start_pending, window.home_flow, window.pending_init = False, None, None
        window.start_auto_home()

        for _ in range(500):
            window.poll()
            clock.advance(0.025)
            if interrupt and device.state()[0] <= 320:
                break
            if not interrupt and window.home_flow is None:
                break
        if interrupt:
            assert device.state()[0] <= 320 and control.homing
            window.stop()
            stopped = device.state()[:2]
            for _ in range(30):
                window.poll()
                clock.advance(0.025)
            assert device.state() == (*stopped, 0) and not link.armed
        else:
            assert window.home_flow is None and link.armed
            assert device.state() == (205, 205, 1)
            assert "205틱 명령 완료" in window.notice.get()
    finally:
        process.stdin.close()
        process.wait(timeout=3)
