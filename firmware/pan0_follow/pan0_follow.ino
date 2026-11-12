#include <Wire.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

// Compile-only defaults. A separately confirmed, narrow installation profile is required.
#ifndef PAN0_HARDWARE_ENABLED
#define PAN0_HARDWARE_ENABLED 0
#endif
#ifndef PAN01_CENTER205_ONLY
#define PAN01_CENTER205_ONLY 0
#endif
#ifndef PAN0_MIN_TICKS
#define PAN0_MIN_TICKS 0
#endif
#ifndef PAN0_START_TICKS
#define PAN0_START_TICKS 0
#endif
#ifndef PAN0_MAX_TICKS
#define PAN0_MAX_TICKS 0
#endif
const int MIN_TICKS = PAN0_MIN_TICKS, START_TICKS = PAN0_START_TICKS, MAX_TICKS = PAN0_MAX_TICKS;
const byte ADDRESS = 0x40;
const unsigned long WATCHDOG_MS = 500, STEP_MS = 50, WRITE_MARGIN_MS = 60;
bool ready = false, armed = false;
int outputTicks = -1, targetTicks = -1;
unsigned long lastMotion = 0, lastStep = 0;
char session[9] = "", previousRequest[97] = "", previousReply[112] = "";
uint32_t previousSequence = 0;

bool calibrated() {
  if (PAN01_CENTER205_ONLY != 0 && PAN01_CENTER205_ONLY != 1) return false;
  if (PAN01_CENTER205_ONLY) return PAN0_HARDWARE_ENABLED == 1;
  return PAN0_HARDWARE_ENABLED == 1 && MIN_TICKS >= 80 && MAX_TICKS <= 330 &&
         MIN_TICKS < START_TICKS && START_TICKS < MAX_TICKS && MAX_TICKS - MIN_TICKS <= 10 &&
         (MIN_TICKS >> 8) == (MAX_TICKS >> 8);
}
void hold() { targetTicks = outputTicks; }
void disarm() { hold(); armed = false; }
void fault() { disarm(); ready = false; }
void expire() { if (armed && millis() - lastMotion >= WATCHDOG_MS) disarm(); }
bool readReg(byte reg, byte &value) {
  Wire.beginTransmission(ADDRESS); Wire.write(reg);
  if (Wire.endTransmission(false) != 0 || Wire.requestFrom(ADDRESS, (byte)1) != 1 ||
      Wire.getWireTimeoutFlag()) return false;
  value = Wire.read(); return true;
}
bool writeReg(byte reg, byte value) {
  Wire.beginTransmission(ADDRESS); Wire.write(reg); Wire.write(value);
  return Wire.endTransmission() == 0 && !Wire.getWireTimeoutFlag();
}
bool configured() {
  byte mode, mode2, prescale;
  return readReg(0, mode) && readReg(1, mode2) && readReg(0xFE, prescale) &&
         (mode & 0x70) == 0x20 && mode2 == 0x04 && prescale == 121;
}
bool readChannel(byte channel, uint16_t &on, uint16_t &off) {
  byte values[4];
  for (byte i = 0; i < 4; ++i) if (!readReg(6 + channel * 4 + i, values[i])) return false;
  on = values[0] | ((uint16_t)values[1] << 8);
  off = values[2] | ((uint16_t)values[3] << 8);
  return (on & 0xE000) == 0 && (off & 0xE000) == 0;
}
bool inactive(uint16_t on, uint16_t off) {
  return !(on & 0x1000) && (off & 0x1000);
}
bool matchesOutput() {
  uint16_t on, off;
  if (!readChannel(0, on, off) || !(outputTicks < 0 ? inactive(on, off) : on == 0 && off == outputTicks)) return false;
  return !PAN01_CENTER205_ONLY || (readChannel(1, on, off) &&
         (outputTicks < 0 ? inactive(on, off) : on == 0 && off == outputTicks));
}
void setup() {
  Serial.begin(115200); Wire.begin(); Wire.setWireTimeout(25000, true);
  Serial.println(PAN01_CENTER205_ONLY ? F("BOOT,CENTER01_205") : F("BOOT,PAN0_TEST1"));
  uint16_t on, off;
  // Unknown retained PWM stays locked; an exact validated start can be adopted without writes.
  byte mode, mode2, prescale;
  if (!readChannel(0, on, off) || !readReg(0, mode) || !readReg(1, mode2) || !readReg(0xFE, prescale)) {
    Serial.println(F("PWM0,UNREADABLE")); return;
  }
  // Report retained command registers before any setup writes; this is not position feedback.
  Serial.print(F("PWM0,")); Serial.print(on); Serial.print(','); Serial.print(off); Serial.print(',');
  Serial.print(mode); Serial.print(','); Serial.print(mode2); Serial.print(','); Serial.println(prescale);
  if (!inactive(on, off)) {
    // Adopt only the explicitly configured start command; never write PWM or ARM on reconnect.
    if (!PAN01_CENTER205_ONLY && calibrated() && configured() && on == 0 && off == START_TICKS) {
      outputTicks = targetTicks = START_TICKS; ready = true;
    }
    return;
  }
  if (PAN01_CENTER205_ONLY) {
    if (!readChannel(1, on, off)) { Serial.println(F("PWM1,UNREADABLE")); return; }
    Serial.print(F("PWM1,")); Serial.print(on); Serial.print(','); Serial.println(off);
    if (!inactive(on, off)) return;
  }
  if (!configured()) {
    byte mode, mode2;
    if (!readReg(0, mode) || !readReg(1, mode2) || (mode & 0x40) || mode2 != 0x04) return;
    for (byte channel = 0; channel < 16; ++channel) {
      if (!readChannel(channel, on, off) || !inactive(on, off)) return;
    }
    if (!writeReg(0, 0x30) || !writeReg(0xFE, 121) || !writeReg(0, 0x20)) return;
    delay(1);
    if (!configured()) return;
  }
  ready = true;
}
bool stageInitial(byte channel, int ticks) {
  byte base = 6 + channel * 4;
  if (!writeReg(base + 3, 0x10) || !writeReg(base, 0) || !writeReg(base + 1, 0) ||
      !writeReg(base + 2, ticks & 0xFF) || !writeReg(base + 3, 0x10 | (ticks >> 8))) return false;
  uint16_t on, off;
  return readChannel(channel, on, off) && on == 0 && off == (0x1000 | ticks);
}
bool output(int ticks) {
  if (!calibrated() || (PAN01_CENTER205_ONLY ? ticks != 205 : ticks < MIN_TICKS || ticks > MAX_TICKS) ||
      !configured() || !matchesOutput()) {
    fault(); return false;
  }
  if (!armed || millis() - lastMotion >= WATCHDOG_MS - WRITE_MARGIN_MS) { disarm(); return false; }
  if (outputTicks < 0) {
    if (ticks != (PAN01_CENTER205_ONLY ? 205 : START_TICKS)) { fault(); return false; }
    // Stage the first pulse while FULL_OFF is asserted, then enable only the verified value.
    if (!stageInitial(0, ticks) || (PAN01_CENTER205_ONLY && !stageInitial(1, 205)) ||
        millis() - lastMotion >= WATCHDOG_MS - WRITE_MARGIN_MS) { fault(); return false; }
    if (!writeReg(9, ticks >> 8)) { fault(); return false; }
    if (PAN01_CENTER205_ONLY && !writeReg(13, 0)) { fault(); return false; }
  } else {
    // A <=10-tick band within one high byte makes an interrupted low-byte update bounded.
    if (!writeReg(8, ticks & 0xFF)) { fault(); return false; }
  }
  outputTicks = ticks;
  if (!matchesOutput()) { fault(); return false; }
  lastStep = millis(); return true;
}
bool unsignedNumber(const char *text, uint32_t &value) {
  if (!*text) return false;
  value = 0;
  for (; *text; ++text) {
    if (*text < '0' || *text > '9' || value > (UINT32_MAX - (*text - '0')) / 10) return false;
    value = value * 10 + (*text - '0');
  }
  return true;
}
bool token(const char *text) {
  if (strlen(text) != 8) return false;
  for (byte i = 0; i < 8; ++i) if (!((text[i] >= '0' && text[i] <= '9') ||
                                   (text[i] >= 'A' && text[i] <= 'F'))) return false;
  return true;
}
void response(const char *kind, const char *who, uint32_t seq, const char *state, bool remember) {
  char reply[112], angle[24];
  if (!strcmp(kind, "ACK")) {
    if (outputTicks < 0) strcpy(angle, "NA");
    else snprintf(angle, sizeof(angle), "%ld", (long)(330 - outputTicks) * 72);
    const char *tilt = PAN01_CENTER205_ONLY && outputTicks >= 0 ? "9000" : "NA";
    snprintf(reply, sizeof(reply), "%s,%s,%lu,%s,%s,%s", kind, who, (unsigned long)seq, state, angle, tilt);
  } else snprintf(reply, sizeof(reply), "%s,%s,%lu,%s", kind, who, (unsigned long)seq, state);
  Serial.println(reply);
  if (remember) strcpy(previousReply, reply);
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
    if (strcmp(session, fields[2])) {
      strcpy(session, fields[2]); previousSequence = 0;
    }
    // HELLO never lets an old ARM acknowledgement stand in for a new operator action.
    previousRequest[0] = previousReply[0] = 0;
    Serial.print(F("READY,1,")); Serial.print(session); Serial.print(F(",DISARMED,"));
    const char *profile = PAN01_CENTER205_ONLY ? "CENTER01_205" : "PAN0_TEST1";
    Serial.println(ready && calibrated() ? profile : "UNCALIBRATED");
    Serial.print(F("PROFILE,")); Serial.print(profile); Serial.print(',');
    if (PAN01_CENTER205_ONLY) Serial.println(F("205,205"));
    else { Serial.print(MIN_TICKS); Serial.print(','); Serial.print(START_TICKS); Serial.print(','); Serial.println(MAX_TICKS); }
    return;
  }
  uint32_t seq;
  if (count < 3 || !token(fields[1]) || !unsignedNumber(fields[2], seq) || !seq) {
    disarm(); response("ERR", "UNKNOWN", 0, "BAD_FORMAT", false); return;
  }
  if (strcmp(fields[1], session)) { response("ERR", fields[1], seq, "SESSION_MISMATCH", false); return; }
  if (seq == previousSequence && !strcmp(original, previousRequest)) {
    Serial.println(previousReply); return;  // No motion or watchdog refresh on replay.
  }
  if (seq <= previousSequence) {
    disarm();
    response("ERR", session, seq, seq == previousSequence ? "SEQUENCE_CONFLICT" : "STALE_SEQUENCE", false); return;
  }
  previousSequence = seq; strcpy(previousRequest, original);
  if (count == 3 && !strcmp(fields[0], "DISARM")) {
    disarm(); response("ACK", session, seq, "DISARMED", true); return;
  }
  if (count == 3 && !strcmp(fields[0], "PING")) {
    response("PONG", session, seq, armed ? "ARMED" : "DISARMED", true); return;
  }
  if (!ready || !configured() || !matchesOutput()) {
    fault(); response("ERR", session, seq, "INTERNAL_FAULT", true); return;
  }
  if (!calibrated()) { response("ERR", session, seq, "CALIBRATION_REQUIRED", true); return; }
  expire();
  if (count == 3 && !strcmp(fields[0], "ARM")) {
    if (armed) { response("ERR", session, seq, "BAD_FORMAT", true); return; }
    armed = true; hold(); lastMotion = millis();
    response("ACK", session, seq, "ARMED", true); return;
  }
  if (!armed) { response("ERR", session, seq, "NOT_ARMED", true); return; }
  if (count == 3 && !strcmp(fields[0], "HOLD")) {
    hold(); lastMotion = millis(); response("ACK", session, seq, "HOLD", true); return;
  }
  uint32_t pan, tilt;
  if (count == 5 && !strcmp(fields[0], "SET") && unsignedNumber(fields[3], pan) && pan <= 18000 &&
      unsignedNumber(fields[4], tilt) && tilt == (PAN01_CENTER205_ONLY ? 9000 : 0)) {
    int ticks = 330 - ((long)pan * 250 + 9000) / 18000;
    if ((PAN01_CENTER205_ONLY ? pan != 9000 : ticks < MIN_TICKS || ticks > MAX_TICKS ||
         (outputTicks < 0 && ticks != START_TICKS))) {
      disarm();
      response("ERR", session, seq, "OUT_OF_RANGE", true); return;
    }
    lastMotion = millis(); targetTicks = ticks;
    if (outputTicks < 0 && !output(ticks)) { response("ERR", session, seq, "INTERNAL_FAULT", true); return; }
    response("ACK", session, seq, "TRACK", true); return;
  }
  disarm(); response("ERR", session, seq, "BAD_FORMAT", true);
}
void loop() {
  expire();
  static char line[97]; static byte length = 0; static bool overflow = false, carriage = false;
  // Bound each parser pass so continuous serial input cannot starve the watchdog.
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
  if (armed && outputTicks >= 0 && targetTicks != outputTicks && millis() - lastStep >= STEP_MS) {
    output(outputTicks + (targetTicks > outputTicks ? 1 : -1));
  }
}
