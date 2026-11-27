#pragma once
#include <stdint.h>
#include <cassert>
#include <deque>
#include <string>
#include <vector>
using byte = uint8_t;
#define F(value) value
extern unsigned long fakeTime, readDelay;
extern bool online, timeoutFlag;
extern byte registers_[256], dirty[2];
extern int pwmWrites, failByte;
extern uint16_t visible[2];
inline unsigned long millis() { return fakeTime; }
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
  template<typename T> void println(T n) { print(n); output += '\n'; }
};
struct WireMock {
  byte reg = 0; std::vector<byte> bytes;
  void begin() {}
  void setWireTimeout(unsigned long, bool) { timeoutFlag = false; }
  bool getWireTimeoutFlag() { return timeoutFlag; }
  void beginTransmission(byte address) { assert(address == 0x40); bytes.clear(); }
  void write(byte value) { bytes.push_back(value); }
  void latch(byte channel) {
    int base = 6 + channel * 4;
    assert(registers_[base] == 0 && registers_[base + 1] == 0);
    uint16_t ticks = registers_[base + 2] | (uint16_t(registers_[base + 3]) << 8);
    assert(ticks >= 80 && ticks <= 330);
    int delta = int(ticks) - int(visible[channel]);
    assert(delta >= -1 && delta <= 1);
    visible[channel] = ticks; dirty[channel] = 0; ++pwmWrites;
  }
  int endTransmission(bool = true) {
    if (!online) { timeoutFlag = true; return 4; }
    assert(!bytes.empty()); reg = bytes[0];
    if (bytes.size() == 1) { fakeTime += readDelay; readDelay = 0; return 0; }
    if (reg == 1 && bytes.size() == 2) {
      assert(bytes[1] == 4 || bytes[1] == 12);
      registers_[1] = bytes[1];
      if (bytes[1] == 4) for (byte ch = 0; ch < 2; ++ch) if (dirty[ch]) latch(ch);
      return 0;
    }
    assert((reg == 6 || reg == 10) && bytes.size() == 5 && registers_[1] == 12);
    byte ch = (reg - 6) / 4;
    // NXP 7.3.2 note 3: partial register writes do not latch a new PWM output in ACK mode.
    for (byte i = 0; i < 4; ++i) {
      if (i == failByte) return 4;
      registers_[reg + i] = bytes[i + 1]; dirty[ch] |= 1 << i;
    }
    latch(ch);
    return failByte == 4 ? 4 : 0;
  }
  byte requestFrom(byte, byte) {
    assert(reg < 0xFA || reg > 0xFD);
    return online ? 1 : 0;
  }
  byte read() { return registers_[reg]; }
};
extern SerialMock Serial;
extern WireMock Wire;
