#include "Wire.h"
#include <iostream>
#include <cstring>
unsigned long fakeTime = 0, readDelay = 0;
bool online = true, timeoutFlag = false;
byte registers_[256] = {}, dirty[2] = {};
int pwmWrites = 0, failByte = -1;
uint16_t visible[2] = {208, 205};
SerialMock Serial;
WireMock Wire;
#include "vision_follow.ino"
void send(const std::string &s) {
  for (char c : s + "\n") Serial.input.push_back(c);
  while (Serial.available()) loop();
}
void initialize(int pan = 208, int tilt = 205) {
  registers_[0] = 32; registers_[1] = 4; registers_[0xFE] = 121;
  registers_[8] = pan & 255; registers_[9] = pan >> 8;
  registers_[12] = tilt & 255; registers_[13] = tilt >> 8;
  visible[0] = pan; visible[1] = tilt;
}
void start() {
  setup(); send("HELLO,1,ABCDEF12");
  assert(ready && !armed && pwmWrites == 0);
  send("ARM,ABCDEF12,1"); assert(armed && registers_[1] == 12 && pwmWrites == 0);
}
void tick(unsigned ms) {
  while (ms) { unsigned dt = ms < 10 ? ms : 10; fakeTime += dt; ms -= dt; loop(); }
}
int main(int argc, char **argv) {
  assert(argc == 2); initialize(); const std::string mode = argv[1];
  if (mode == "protocol") {
    setup(); std::string line;
    while (std::getline(std::cin, line)) {
      if (line.rfind("@TICK,", 0) == 0) tick(std::stoul(line.substr(6)));
      else if (line == "@STATE") {
        std::cout << outputTicks[0] << ',' << outputTicks[1] << ',' << armed << ',' << int(registers_[1]) << std::endl;
        continue;
      } else if (line != "@POLL") send(line);
      const char *digits = "0123456789abcdef";
      for (unsigned char c : Serial.output) std::cout << digits[c >> 4] << digits[c & 15];
      Serial.output.clear(); std::cout << std::endl;
    }
    return 0;
  }
  if (mode.rfind("diag-", 0) == 0 && mode != "diag-armed") {
    if (mode == "diag-mode") registers_[1] = 12;
    if (mode == "diag-off") registers_[9] = 16;
    if (mode == "diag-range") { registers_[12] = 340 & 255; registers_[13] = 340 >> 8; }
    if (mode == "diag-clock") registers_[0xFE] = 30;
    if (mode == "diag-i2c") online = false;
    const std::vector<byte> before(registers_, registers_ + 256);
    setup(); send("HELLO,1,ABCDEF12");
    bool wasReady = ready;
    send("DIAG,ABCDEF12,1");
    assert(!armed && ready == wasReady && pwmWrites == 0);
    assert(std::vector<byte>(registers_, registers_ + 256) == before);
    if (mode == "diag-i2c") assert(Serial.output.find("ERR,ABCDEF12,1,DIAG_READ_FAILED") != std::string::npos);
    else {
      std::string expected = "DIAG,ABCDEF12,1," + std::to_string(VISION_HARDWARE_ENABLED == 1) + ",32," +
        std::to_string(registers_[1]) + "," + std::to_string(registers_[0xFE]) + ",0," +
        std::to_string(registers_[8] | (uint16_t(registers_[9]) << 8)) + ",0," +
        std::to_string(registers_[12] | (uint16_t(registers_[13]) << 8));
      assert(Serial.output.find(expected + "\n") != std::string::npos);
    }
    if (!wasReady) { send("ARM,ABCDEF12,2"); assert(!armed && pwmWrites == 0); }
    return 0;
  }
  if (mode == "disabled" || mode == "full-off" || mode == "bad-mode" || mode == "bad-clock" || mode == "bad-tick") {
    if (mode == "full-off") registers_[9] = 0x10;
    if (mode == "bad-mode") registers_[1] = 12;
    if (mode == "bad-clock") registers_[0xFE] = 30;
    if (mode == "bad-tick") registers_[12] = 79;
    setup(); send("HELLO,1,ABCDEF12"); send("ARM,ABCDEF12,1");
    assert(!ready && !armed && pwmWrites == 0); return 0;
  }
  if (mode == "endpoints") initialize(80, 330);
  if (mode == "cross" || mode.rfind("fail", 0) == 0) initialize(255, 255);
  if (mode == "reverse-cross") initialize(256, 256);
  start();
  if (mode == "diag-armed") {
    send("SET_TICKS,ABCDEF12,2,80,330");
    const std::vector<byte> before(registers_, registers_ + 256);
    send("DIAG,ABCDEF12,3");
    assert(armed && pwmWrites == 0 && targetTicks[0] == 80 && targetTicks[1] == 330);
    assert(std::vector<byte>(registers_, registers_ + 256) == before);
    assert(Serial.output.find("DIAG_REQUIRES_DISARM") != std::string::npos);
    fakeTime = 500; loop(); assert(!armed && targetTicks[0] == outputTicks[0]);
  } else if (mode == "retained" || mode == "endpoints") {
    send("DISARM,ABCDEF12,2");
    assert(!armed && ready && registers_[1] == 4 && pwmWrites == 0);
  } else if (mode == "hold") {
    send("SET_TICKS,ABCDEF12,2,200,210"); tick(50);
    assert(outputTicks[0] == 207 && outputTicks[1] == 206);
    send("HOLD,ABCDEF12,3"); tick(100);
    assert(targetTicks[0] == 207 && targetTicks[1] == 206 && pwmWrites == 2);
  } else if (mode == "watchdog" || mode == "ping" || mode == "replay") {
    send("SET_TICKS,ABCDEF12,2,80,330"); tick(400);
    if (mode == "ping") send("PING,ABCDEF12,3");
    if (mode == "replay") send("SET_TICKS,ABCDEF12,2,80,330");
    tick(100); assert(!armed && targetTicks[0] == outputTicks[0] && registers_[1] == 4);
    int writes = pwmWrites; tick(500); assert(pwmWrites == writes);
  } else if (mode == "rearm") {
    send("SET_TICKS,ABCDEF12,2,80,330");
    send("ARM,ABCDEF12,3"); assert(!armed && targetTicks[0] == outputTicks[0]);
    tick(100); assert(pwmWrites == 0 && registers_[1] == 4);
  } else if (mode == "session" || mode == "stale" || mode == "malformed" || mode == "overflow") {
    send("SET_TICKS,ABCDEF12,2,80,330");
    if (mode == "session") send("HOLD,12345678,3");
    if (mode == "stale") send("HOLD,ABCDEF12,1");
    if (mode == "malformed") send("SET_TICKS,ABCDEF12,3,NaN,205");
    if (mode == "overflow") send(std::string(97, 'X'));
    assert(!armed); tick(100); assert(pwmWrites == 0);
  } else if (mode == "bounds-low" || mode == "bounds-high") {
    send(mode == "bounds-low" ? "SET_TICKS,ABCDEF12,2,79,205" : "SET_TICKS,ABCDEF12,2,208,331");
    assert(!armed && pwmWrites == 0);
  } else if (mode == "cross" || mode == "reverse-cross" || mode.rfind("fail", 0) == 0) {
    if (mode.rfind("fail", 0) == 0) failByte = std::stoi(mode.substr(4));
    send(mode == "reverse-cross" ? "SET_TICKS,ABCDEF12,2,255,255" : "SET_TICKS,ABCDEF12,2,256,256");
    tick(50);
    if (failByte >= 0) {
      assert(!ready && !armed && registers_[1] == 12);
      assert(visible[0] == (failByte == 4 ? 256 : 255) && visible[1] == 255);
      send("DISARM,ABCDEF12,3"); assert(registers_[1] == 12);
      ready = armed = false; outputTicks[0] = outputTicks[1] = -1;
      setup(); assert(!ready && !armed);
    } else {
      int expected = mode == "reverse-cross" ? 255 : 256;
      assert(visible[0] == expected && visible[1] == expected);
    }
  } else if (mode == "slow-read") {
    send("SET_TICKS,ABCDEF12,2,80,330"); readDelay = 501; tick(50);
    assert(!armed && pwmWrites == 0);
  } else if (mode == "slow-command") {
    readDelay = 501; send("HOLD,ABCDEF12,2");
    assert(!armed && registers_[1] == 4 && pwmWrites == 0);
    assert(Serial.output.find("NOT_ARMED") != std::string::npos);
  } else if (mode == "hello") {
    send("SET_TICKS,ABCDEF12,2,80,330"); send("HELLO,1,ABCDEF12");
    send("ARM,ABCDEF12,1"); assert(!armed && pwmWrites == 0);
  } else assert(false);
  return 0;
}
