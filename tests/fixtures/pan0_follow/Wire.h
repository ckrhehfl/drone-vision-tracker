#pragma once
#include <stdint.h>
#include <deque>
#include <string>
#include <vector>
#include <utility>
#include <cassert>
using byte = uint8_t;
#define F(value) value
extern unsigned long fakeTime, readDelay;
extern bool timeoutFlag, online, failAfterApply;
extern int failReg, failValue;
extern bool centerMode;
extern byte registers_[256];
extern std::vector<std::pair<int, int>> writes;
inline unsigned long millis() { return fakeTime; }
inline void delay(unsigned long ms) { fakeTime += ms; }
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
  template<typename T> void println(T v) { print(v); output += '\n'; }
};
struct WireMock {
  byte reg = 0, value = 0, count = 0;
  void begin() {}
  void setWireTimeout(unsigned long, bool) { timeoutFlag = false; }
  bool getWireTimeoutFlag() { return timeoutFlag; }
  void beginTransmission(byte address) { assert(address == 0x40); count = 0; }
  void write(byte v) { if (!count++) reg = v; else value = v; }
  int endTransmission(bool = true) {
    if (!online) { timeoutFlag = true; return 5; }
    if (count == 1) { fakeTime += readDelay; readDelay = 0; }
    if (count > 1) {
      bool failing = reg == failReg && (failValue < 0 || value == failValue);
      if (failing && !failAfterApply) return 4;
      registers_[reg] = value;
      writes.push_back({reg, value});
      // Check every intermediate PWM, including a write followed by an I2C error.
      int on = registers_[6] | (registers_[7] << 8);
      int off = registers_[8] | (registers_[9] << 8);
      if (!(off & 0x1000) && on != off) {
        assert(on == 0 && off >= 200 && off <= 210);
      }
      if (!centerMode) assert(reg < 10 || reg > 13);
      int tiltOn = registers_[10] | (registers_[11] << 8);
      int tiltOff = registers_[12] | (registers_[13] << 8);
      if (centerMode && !(tiltOff & 0x1000) && tiltOn != tiltOff) {
        assert(tiltOn == 0 && tiltOff == 205);
      }
      if (failing) return 4;
    }
    return 0;
  }
  byte requestFrom(byte, byte) {
    assert(reg < 0xFA || reg > 0xFD);  // ALL_LED registers are write-only, not readable state.
    return online ? 1 : 0;
  }
  byte read() { return registers_[reg]; }
};
extern SerialMock Serial;
extern WireMock Wire;
