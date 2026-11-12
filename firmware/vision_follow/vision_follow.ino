#include <Wire.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>

#ifndef VISION_HARDWARE_ENABLED
#define VISION_HARDWARE_ENABLED 0
#endif
const byte ADDRESS = 0x40;
const int MIN_TICKS = 80, MAX_TICKS = 330;
const unsigned long WATCHDOG_MS = 500, STEP_MS = 50, WRITE_MARGIN_MS = 60;
int outputTicks[2] = {-1, -1}, targetTicks[2] = {-1, -1};
unsigned long lastMotion = 0, lastStep[2] = {0, 0};
bool ready = false, armed = false, ackMode = false;
char session[9] = "", previousRequest[97] = "", previousReply[112] = "";
uint32_t previousSequence = 0;

bool readReg(byte reg, byte &value) {
  Wire.beginTransmission(ADDRESS); Wire.write(reg);
  if (Wire.endTransmission(false) != 0 || Wire.getWireTimeoutFlag()) return false;
  if (Wire.requestFrom(ADDRESS, (byte)1) != 1 || Wire.getWireTimeoutFlag()) return false;
  value = Wire.read(); return true;
}
bool writeReg(byte reg, byte value) {
  Wire.beginTransmission(ADDRESS); Wire.write(reg); Wire.write(value);
  return Wire.endTransmission() == 0 && !Wire.getWireTimeoutFlag();
}
bool configured(byte requiredMode) {
  byte mode1, mode2, prescale;
  return readReg(0, mode1) && readReg(1, mode2) && readReg(0xFE, prescale) &&
         (mode1 & 0x70) == 0x20 && mode2 == requiredMode && prescale == 121;
}
bool readChannel(byte channel, uint16_t &on, uint16_t &off) {
  byte values[4];
  for (byte i = 0; i < 4; ++i) if (!readReg(6 + channel * 4 + i, values[i])) return false;
  on = values[0] | (uint16_t(values[1]) << 8);
  off = values[2] | (uint16_t(values[3]) << 8);
  return true;
}
bool outputsMatch() {
  uint16_t on, off;
  for (byte ch = 0; ch < 2; ++ch)
    if (!readChannel(ch, on, off) || on != 0 || off != outputTicks[ch]) return false;
  return true;
}
void hold() { for (byte ch = 0; ch < 2; ++ch) targetTicks[ch] = outputTicks[ch]; }
void fault() { armed = ready = false; hold(); }
void disarm() {
  armed = false; hold();
  if (ackMode && ready) {
    // Restore restart eligibility only after coherent acknowledged PWM writes.
    if (!configured(0x0C) || !outputsMatch() || !writeReg(1, 4) || !configured(4)) fault();
    else ackMode = false;
  }
}
void expire() { if (armed && millis() - lastMotion >= WATCHDOG_MS) disarm(); }
bool output(byte channel, int ticks) {
  if (ticks < MIN_TICKS || ticks > MAX_TICKS || !configured(0x0C) || !outputsMatch()) {
    fault(); return false;
  }
  if (!armed || millis() - lastMotion >= WATCHDOG_MS - WRITE_MARGIN_MS) {
    disarm(); return false;
  }
  // OCH=1 latches after all four PWM registers: NXP PCA9685 section 7.3.2, note 3.
  Wire.beginTransmission(ADDRESS); Wire.write((byte)(6 + channel * 4));
  Wire.write((byte)0); Wire.write((byte)0); Wire.write((byte)ticks); Wire.write((byte)(ticks >> 8));
  if (Wire.endTransmission() != 0 || Wire.getWireTimeoutFlag()) { fault(); return false; }
  outputTicks[channel] = ticks;
  if (!outputsMatch()) { fault(); return false; }
  lastStep[channel] = millis(); return true;
}
bool token(const char *s) {
  if (strlen(s) != 8) return false;
  for (byte i = 0; i < 8; ++i)
    if (!((s[i] >= '0' && s[i] <= '9') || (s[i] >= 'A' && s[i] <= 'F'))) return false;
  return true;
}
bool number(const char *s, uint32_t &value) {
  if (!*s) return false;
  value = 0;
  for (; *s; ++s) {
    if (*s < '0' || *s > '9' || value > (UINT32_MAX - (*s - '0')) / 10) return false;
    value = value * 10 + (*s - '0');
  }
  return true;
}
void response(const char *kind, const char *who, uint32_t seq, const char *state, bool remember) {
  char reply[112];
  if (!strcmp(kind, "ACK"))
    snprintf(reply, sizeof(reply), "ACK,%s,%lu,%s,%d,%d", who, (unsigned long)seq, state,
             outputTicks[0], outputTicks[1]);
  else snprintf(reply, sizeof(reply), "ERR,%s,%lu,%s", who, (unsigned long)seq, state);
  Serial.println(reply);
  if (remember) strcpy(previousReply, reply);
}
void diagnostic(const char *who, uint32_t seq) {
  if (armed) { response("ERR", who, seq, "DIAG_REQUIRES_DISARM", true); return; }
  byte mode1, mode2, prescale;
  uint16_t on[2], off[2];
  if (!readReg(0, mode1) || !readReg(1, mode2) || !readReg(0xFE, prescale) ||
      !readChannel(0, on[0], off[0]) || !readChannel(1, on[1], off[1])) {
    response("ERR", who, seq, "DIAG_READ_FAILED", true); return;
  }
  snprintf(previousReply, sizeof(previousReply), "DIAG,%s,%lu,%u,%u,%u,%u,%u,%u,%u,%u", who,
           (unsigned long)seq, unsigned(VISION_HARDWARE_ENABLED == 1), unsigned(mode1),
           unsigned(mode2), unsigned(prescale), unsigned(on[0]), unsigned(off[0]),
           unsigned(on[1]), unsigned(off[1]));
  Serial.println(previousReply);
}
void setup() {
  Serial.begin(115200); Wire.begin(); Wire.setWireTimeout(25000, true);
  Serial.println(F("BOOT,VISION_TICKS1"));
  if (VISION_HARDWARE_ENABLED != 1 || !configured(4)) return;
  uint16_t on[2], off[2];
  for (byte ch = 0; ch < 2; ++ch)
    if (!readChannel(ch, on[ch], off[ch]) || on[ch] != 0 ||
        off[ch] < MIN_TICKS || off[ch] > MAX_TICKS) return;
  for (byte ch = 0; ch < 2; ++ch) outputTicks[ch] = targetTicks[ch] = off[ch];
  ready = true;
}
void handle(char *line) {
  expire();
  char original[97]; strcpy(original, line);
  char *fields[5] = {line}; byte count = 1;
  for (char *p = line; *p; ++p) if (*p == ',') {
    if (count == 5) { disarm(); response("ERR", "UNKNOWN", 0, "BAD_FORMAT", false); return; }
    *p = 0; fields[count++] = p + 1;
  }
  if (count == 3 && !strcmp(fields[0], "HELLO") && token(fields[2])) {
    disarm();
    if (strcmp(fields[1], "1")) { response("ERR", fields[2], 0, "UNSUPPORTED_VERSION", false); return; }
    if (ready && (!configured(4) || !outputsMatch())) fault();
    if (strcmp(session, fields[2])) { strcpy(session, fields[2]); previousSequence = 0; }
    previousRequest[0] = previousReply[0] = 0;
    Serial.print(F("READY,1,")); Serial.print(session);
    Serial.print(ready ? F(",DISARMED,VISION_TICKS1,") : F(",LOCKED,VISION_TICKS1,"));
    Serial.print(outputTicks[0]); Serial.print(','); Serial.println(outputTicks[1]);
    Serial.println(F("LIMITS,VISION_TICKS1,80,330")); return;
  }
  uint32_t seq;
  if (count < 3 || !token(fields[1]) || !number(fields[2], seq) || !seq) {
    disarm(); response("ERR", "UNKNOWN", 0, "BAD_FORMAT", false); return;
  }
  if (!*session || strcmp(session, fields[1])) {
    disarm(); response("ERR", fields[1], seq, "SESSION_MISMATCH", false); return;
  }
  if (seq == previousSequence && !strcmp(original, previousRequest)) {
    Serial.println(previousReply); return;
  }
  if (seq <= previousSequence) {
    disarm(); response("ERR", session, seq, "STALE_SEQUENCE", false); return;
  }
  previousSequence = seq; strcpy(previousRequest, original);
  if (count == 3 && !strcmp(fields[0], "DIAG")) { diagnostic(session, seq); return; }
  if (count == 3 && !strcmp(fields[0], "DISARM")) {
    disarm(); response(ready ? "ACK" : "ERR", session, seq,
                       ready ? "DISARMED" : "INTERNAL_FAULT", true); return;
  }
  if (!ready || !configured(ackMode ? 0x0C : 4) || !outputsMatch()) {
    fault(); response("ERR", session, seq, "INTERNAL_FAULT", true); return;
  }
  expire();
  if (count == 3 && !strcmp(fields[0], "PING")) {
    response("ACK", session, seq, armed ? "ARMED" : "DISARMED", true); return;
  }
  if (count == 3 && !strcmp(fields[0], "ARM")) {
    if (armed) { disarm(); response("ERR", session, seq, "ALREADY_ARMED", true); return; }
    // Keep the OCH marker on a failed transition, so reboot cannot silently adopt uncertainty.
    ackMode = true;
    if (!writeReg(1, 0x0C) || !configured(0x0C)) {
      fault(); response("ERR", session, seq, "INTERNAL_FAULT", true); return;
    }
    armed = true; hold(); lastMotion = millis(); lastStep[0] = lastStep[1] = lastMotion;
    response("ACK", session, seq, "ARMED", true); return;
  }
  if (!armed) { response("ERR", session, seq, "NOT_ARMED", true); return; }
  if (count == 3 && !strcmp(fields[0], "HOLD")) {
    hold(); lastMotion = millis(); response("ACK", session, seq, "HOLD", true); return;
  }
  uint32_t pan, tilt;
  if (count == 5 && !strcmp(fields[0], "SET_TICKS") && number(fields[3], pan) && number(fields[4], tilt)) {
    if (pan < MIN_TICKS || pan > MAX_TICKS || tilt < MIN_TICKS || tilt > MAX_TICKS) {
      disarm(); response("ERR", session, seq, "OUT_OF_RANGE", true); return;
    }
    targetTicks[0] = pan; targetTicks[1] = tilt; lastMotion = millis();
    response("ACK", session, seq, "TRACK", true); return;
  }
  disarm(); response("ERR", session, seq, "BAD_FORMAT", true);
}
void loop() {
  expire();
  static char line[97]; static byte length = 0; static bool overflow = false, carriage = false;
  for (byte budget = 0; budget < 32 && Serial.available(); ++budget) {
    expire();
    char c = Serial.read();
    if (carriage && c != '\n') overflow = true;
    if (c == '\r') { carriage = true; continue; }
    if (c == '\n') {
      if (overflow) { disarm(); response("ERR", "UNKNOWN", 0, "LINE_TOO_LONG", false); }
      else if (length) { line[length] = 0; handle(line); }
      length = 0; overflow = carriage = false;
    } else if ((byte)c < 32 || (byte)c > 126 || length >= 96) overflow = true;
    else if (!overflow) line[length++] = c;
  }
  for (byte ch = 0; ch < 2; ++ch) {
    expire();
    if (armed && targetTicks[ch] != outputTicks[ch] && millis() - lastStep[ch] >= STEP_MS)
      output(ch, outputTicks[ch] + (targetTicks[ch] > outputTicks[ch] ? 1 : -1));
  }
}
