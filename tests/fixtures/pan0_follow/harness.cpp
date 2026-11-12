#include "Wire.h"
#include <cstring>
#include <cstdio>
unsigned long fakeTime = 0, readDelay = 0;
bool timeoutFlag = false, online = true, failAfterApply = false;
int failReg = -1, failValue = -1;
bool centerMode = false;
byte registers_[256] = {};
std::vector<std::pair<int, int>> writes;
SerialMock Serial;
WireMock Wire;
#include "pan0_follow.ino"

void command(const std::string &text) {
  for (char c : text + "\n") Serial.input.push_back(c);
  while (Serial.available()) loop();
}
void begin() {
  setup(); command("HELLO,1,ABCDEF12");
}
void start() {
  begin(); command("ARM,ABCDEF12,1");
  assert(armed && writes.size() == 3);  // Only clock setup; ARM must not output PWM.
  command("SET,ABCDEF12,2,9000,0");
  assert(armed && outputTicks == 205 && targetTicks == 205);
  assert(Serial.output.find("ACK,ABCDEF12,2,TRACK,9000,NA\n") != std::string::npos);
}
int main(int argc, char **argv) {
  assert(argc == 2);
  registers_[0] = 0x11; registers_[1] = 4; registers_[0xFE] = 30;
  for (int channel = 0; channel < 16; ++channel) registers_[9 + channel * 4] = 0x10;
  const std::string mode = argv[1];
  centerMode = PAN01_CENTER205_ONLY != 0;
  if (mode == "retained") {
    registers_[8] = 206; registers_[9] = 0;
    registers_[0] = 0x21; registers_[0xFE] = 121;
    setup(); assert(!ready && writes.empty());
    assert(Serial.output.find("PWM0,0,206,33,4,121\n") != std::string::npos);
    return 0;
  }
  if (mode == "retained-start") {
    registers_[8] = 205; registers_[9] = 0;
    registers_[12] = 205; registers_[13] = 0;
    registers_[0] = 0x21; registers_[0xFE] = 121;
    begin(); assert(ready && !armed && outputTicks == 205 && targetTicks == 205 && writes.empty());
    command("ARM,ABCDEF12,1"); assert(armed && writes.empty());
    command("SET,ABCDEF12,2,9000,0"); assert(writes.empty());
    command("DISARM,ABCDEF12,3"); assert(!armed && writes.empty() && registers_[12] == 205);
    return 0;
  }
  if (mode == "retained-disabled") {
    registers_[8] = 205; registers_[9] = 0;
    registers_[0] = 0x21; registers_[0xFE] = 121;
    begin(); command("ARM,ABCDEF12,1"); assert(!ready && !armed && writes.empty());
    return 0;
  }
  if (mode == "center" || mode == "center-reject" || mode == "center-partial" || mode == "center-mismatch") {
    begin(); assert(Serial.output.find("DISARMED,CENTER01_205") != std::string::npos);
    command("ARM,ABCDEF12,1"); assert(armed && writes.size() == 3);
    if (mode == "center-reject") {
      command("SET,ABCDEF12,2,8999,9000");
      assert(!armed && outputTicks == -1 && writes.size() == 3); return 0;
    }
    if (mode == "center-partial") { failReg = 13; failValue = 0; failAfterApply = true; }
    command("SET,ABCDEF12,2,9000,9000");
    if (mode == "center-partial") { assert(!armed && !ready); return 0; }
    assert(outputTicks == 205 && targetTicks == 205 && registers_[8] == 205 && registers_[9] == 0);
    assert(registers_[12] == 205 && registers_[13] == 0);
    assert(Serial.output.find("ACK,ABCDEF12,2,TRACK,9000,9000\n") != std::string::npos);
    size_t count = writes.size();
    command("SET,ABCDEF12,2,9000,9000"); assert(writes.size() == count);
    if (mode == "center-mismatch") {
      registers_[12] = 206; command("HOLD,ABCDEF12,3"); assert(!ready && !armed); return 0;
    }
    fakeTime += 500; loop(); assert(!armed && writes.size() == count);
    command("SET,ABCDEF12,3,9000,9000"); assert(!armed && writes.size() == count);
    for (auto write : writes) assert(write.first == 0 || write.first == 0xFE ||
                                    (write.first >= 6 && write.first <= 13));
    return 0;
  }
  if (mode == "unreadable") {
    online = false; setup(); assert(!ready && writes.empty());
    assert(Serial.output.find("PWM0,UNREADABLE\n") != std::string::npos);
    return 0;
  }
  if (mode == "other-active") {
    registers_[12] = 205; registers_[13] = 0;
    setup(); assert(!ready && writes.empty()); return 0;
  }
  if (mode == "equal-counters") {
    registers_[9] = 0; setup(); assert(!ready && writes.empty()); return 0;
  }
  if (mode == "bad-mode") {
    registers_[1] = 0x14; setup(); assert(!ready && writes.empty()); return 0;
  }
  if (mode == "disabled") {
    begin(); command("ARM,ABCDEF12,1");
    assert(!armed && outputTicks == -1);
    assert(Serial.output.find("UNCALIBRATED") != std::string::npos);
    assert(Serial.output.find("CALIBRATION_REQUIRED") != std::string::npos);
    assert(Serial.output.find("PWM0,0,4096,17,4,30\n") != std::string::npos);
    return 0;
  }
  if (mode == "initial") {
    begin(); command("ARM,ABCDEF12,1"); assert(armed && outputTicks == -1);
    command("SET,ABCDEF12,2,8640,0");
    assert(!armed && outputTicks == -1 && writes.size() == 3);
    return 0;
  }
  start();
  if (mode == "track-hold") {
    command("SET,ABCDEF12,3,9360,0"); assert(targetTicks == 200 && outputTicks == 205);
    fakeTime += 50; loop(); assert(outputTicks == 204);
    command("HOLD,ABCDEF12,4");
    fakeTime += 100; loop(); assert(armed && outputTicks == 204 && targetTicks == 204);
    command("DISARM,ABCDEF12,5"); command("SET,ABCDEF12,6,9000,0");
    assert(!armed && outputTicks == 204);
  } else if (mode == "replay") {
    fakeTime += 400; command("SET,ABCDEF12,2,9000,0");
    assert(armed && lastMotion == 1);
    fakeTime += 100; loop(); assert(!armed);
    command("SET,ABCDEF12,3,9000,0"); assert(!armed);
  } else if (mode == "ping") {
    fakeTime += 400; command("PING,ABCDEF12,3");
    assert(Serial.output.find("PONG,ABCDEF12,3,ARMED\n") != std::string::npos);
    fakeTime += 100; loop(); assert(!armed);
  } else if (mode == "hello") {
    command("HELLO,1,ABCDEF12"); assert(!armed && previousSequence == 2);
    command("ARM,ABCDEF12,1"); assert(!armed);
    command("ARM,ABCDEF12,3"); assert(armed);
    command("HELLO,1,12345678"); assert(!armed && previousSequence == 0);
    command("SET,ABCDEF12,4,9000,0"); assert(!armed);
  } else if (mode == "conflict") {
    command("HOLD,ABCDEF12,2"); assert(!armed);
    assert(Serial.output.find("SEQUENCE_CONFLICT") != std::string::npos);
  } else if (mode == "watchdog") {
    command("SET,ABCDEF12,3,9360,0");
    fakeTime += 500; loop(); assert(!armed && targetTicks == outputTicks && outputTicks == 205);
    command("ARM,ABCDEF12,4"); assert(armed && outputTicks == 205);
  } else if (mode == "slow-read") {
    readDelay = 501; command("HOLD,ABCDEF12,3"); assert(!armed);
    assert(Serial.output.find("NOT_ARMED") != std::string::npos);
  } else if (mode == "i2c") {
    command("SET,ABCDEF12,3,9360,0"); online = false;
    fakeTime += 50; loop(); assert(!armed && !ready && outputTicks == 205);
    online = true; command("ARM,ABCDEF12,4"); assert(!armed);
  } else if (mode == "partial") {
    command("SET,ABCDEF12,3,9360,0"); failReg = 8; failAfterApply = true;
    fakeTime += 50; loop(); assert(!armed && !ready);
    assert(registers_[8] == 204);  // Error may still deliver one bounded tick.
  } else if (mode == "mismatch") {
    registers_[8] = 206; command("HOLD,ABCDEF12,3"); assert(!ready && !armed);
  } else if (mode == "bad-set") {
    for (const char *value : {"-1", "18001", "9000.0", "NaN", "", "9000,1", "9000,0,x"}) {
      command(std::string("SET,ABCDEF12,3,") + value + ",0"); assert(!armed);
    }
  } else if (mode == "overflow") {
    command(std::string(97, 'X')); assert(!armed && outputTicks == 205);
    assert(Serial.output.find("LINE_TOO_LONG") != std::string::npos);
  } else if (mode == "partial-line") {
    for (char c : std::string("HOLD,ABCDEF12,3\r\nPING,ABCDEF12,4\n")) {
      Serial.input.push_back(c); loop();
    }
    assert(armed && previousSequence == 4);
    command("SE\rT,ABCDEF12,5,9000,0"); assert(!armed);
  } else if (mode == "flood") {
    for (int i = 0; i < 10000; ++i) Serial.input.push_back('x');
    fakeTime += 500; loop(); assert(!armed && Serial.available() > 0);
  } else assert(false);
  for (auto write : writes) assert(write.first == 0 || write.first == 0xFE ||
                                  (write.first >= 6 && write.first <= 9));
  return 0;
}
