#include <Wire.h>
#include <stdlib.h>
#include <string.h>

// Isolated manual test profile, not the tracking protocol in docs/02_serial_protocol.md.
// Previous test's PWM command limits; these are NOT measured shaft angles.
const byte ADDRESS = 0x40;
const int MIN_TICKS = 205, MAX_TICKS = 410;
const unsigned long WATCHDOG_MS = 300, STEP_MS = 50;
char session[9] = "", line[97];
byte used = 0;
bool overflow = false, armed = false, pcaReady = false;
int current = -1, target = -1;
int lowLimit = MIN_TICKS, highLimit = MAX_TICKS;
unsigned long lastMotion = 0, lastStep = 0;
uint32_t lastSeq = 0;

bool readReg(byte reg, byte &v) {
  Wire.beginTransmission(ADDRESS); Wire.write(reg);
  if (Wire.endTransmission(false) || Wire.requestFrom(ADDRESS, (byte)1) != 1) return false;
  v = Wire.read(); return true;
}
bool writeReg(byte reg, byte v) {
  Wire.beginTransmission(ADDRESS); Wire.write(reg); Wire.write(v);
  return Wire.endTransmission() == 0;
}
bool configured() {
  byte mode, mode2, prescale;
  return readReg(0x00, mode) && readReg(0x01, mode2) && readReg(0xFE, prescale) &&
         (mode & 0x70) == 0x20 && (mode2 & 0x18) == 0x08 && prescale == 121;
}
bool enableAtomicPulseWrites() {
  byte mode2;
  // OCH=1 applies only after all four PWM bytes are ACKed (PCA9685 §7.3.2).
  return readReg(0x01, mode2) && !(mode2 & 0x10) &&
         ((mode2 & 0x08) || writeReg(0x01, mode2 | 0x08)) && configured();
}
bool writePulse(int ticks) {
  if (ticks < MIN_TICKS || ticks > MAX_TICKS || !configured()) return false;
  Wire.beginTransmission(ADDRESS); Wire.write(0x06);
  Wire.write((byte)0); Wire.write((byte)0);
  Wire.write((byte)(ticks & 255)); Wire.write((byte)(ticks >> 8));
  if (Wire.endTransmission()) return false;
  current = ticks; return true;
}
void stopMotion() { target = current; armed = false; }
bool preparePca() {
  byte mode, mode2, prescale;
  if (!readReg(0x00, mode) || !readReg(0x01, mode2) || !readReg(0xFE, prescale) ||
      (mode & 0x40) || (mode2 & 0x10)) return false;
  if ((mode & 0x30) != 0x20 || prescale != 121) {
    // A frequency change affects every channel; refuse while any output is active.
    for (byte ch = 0; ch < 16; ch++) {
      byte a,b,c,d,reg = 6 + 4*ch;
      if (!readReg(reg,a) || !readReg(reg+1,b) || !readReg(reg+2,c) || !readReg(reg+3,d)) return false;
      if ((b & 16) || (!(d & 16) && (a || b || c || d))) return false;
    }
    byte awake = mode & ~0x90;
    if (!writeReg(0x00, awake | 16) || !writeReg(0xFE,121) || !writeReg(0x00,awake)) return false;
    delay(5);
    if (!writeReg(0x00,awake | 0xA0)) return false;
  }
  byte a,b,c,d;
  if (!readReg(6,a) || !readReg(7,b) || !readReg(8,c) || !readReg(9,d)) return false;
  int ticks = c | ((d & 15) << 8);
  if ((d & 16) || !(a || b || c || d)) {
    current = -1;
    return true;
  }
  if (a != 0 || b != 0 || ticks < MIN_TICKS || ticks > MAX_TICKS) return false;
  current = ticks;
  return true;
}
bool tokenValid(const char *s) {
  if (strlen(s) != 8) return false;
  for (byte i=0;i<8;i++) if (!((s[i]>='0' && s[i]<='9') || (s[i]>='A' && s[i]<='F'))) return false;
  return true;
}
bool sequence(const char *s, uint32_t &n) {
  n=0; if (!*s) return false;
  while (*s) {
    if (*s<'0' || *s>'9' || n > (UINT32_MAX - (*s-'0')) / 10) return false;
    n=n*10+(*s++-'0');
  }
  return n>0;
}
void reply(const char *kind, uint32_t seq, const char *state) {
  Serial.print(kind); Serial.print(','); Serial.print(session); Serial.print(',');
  Serial.print(seq); Serial.print(','); Serial.print(state); Serial.print(','); Serial.println(current);
}
void processLine() {
  if (armed && millis()-lastMotion>=WATCHDOG_MS) stopMotion();
  char *parts[4]; byte count=1; parts[0]=line;
  for (char *p=line;*p;p++) if (*p==',') {
    if (count==4) { stopMotion(); return; }
    *p=0; parts[count++]=p+1;
  }
  if (count==2 && !strcmp(parts[0],"HELLO") && tokenValid(parts[1])) {
    stopMotion(); strcpy(session,parts[1]); lastSeq=0;
    pcaReady=preparePca();
    Serial.print(F("READY,PANKEY2,")); Serial.print(session); Serial.print(',');
    Serial.print(pcaReady ? "OK" : "FAULT"); Serial.print(','); Serial.println(current);
    return;
  }
  uint32_t seq;
  if (count<3 || !*session || strcmp(parts[1],session) || !sequence(parts[2],seq)) { stopMotion(); return; }
  if (seq<=lastSeq) { reply("ERR",seq,"STALE"); return; } // duplicates never extend watchdog
  lastSeq=seq;
  const char *cmd=parts[0];
  if (!strcmp(cmd,"DISARM") && count==3) { stopMotion(); reply("ACK",seq,"DISARMED"); return; }
  if (!pcaReady) { stopMotion(); reply("ERR",seq,"I2C"); return; }
  if (!strcmp(cmd,"INIT") && count==4 && !armed && current<0) {
    uint32_t ticks;
    if (!sequence(parts[3],ticks) || ticks<MIN_TICKS || ticks>MAX_TICKS) {
      reply("ERR",seq,"LIMIT"); return;
    }
    if (!enableAtomicPulseWrites() || !writePulse(ticks)) {
      pcaReady=false; reply("ERR",seq,"I2C"); return;
    }
    reply("ACK",seq,"INITIALIZED"); return;
  }
  if (!strcmp(cmd,"ARM") && count==4) {
    char *separator=strchr(parts[3],':');
    uint32_t low, high;
    if (separator) *separator=0;
    if (!separator || !sequence(parts[3],low) || !sequence(separator+1,high) ||
        low<MIN_TICKS || high>MAX_TICKS || low>=high || current<MIN_TICKS ||
        current<(int)low || current>(int)high) {
      stopMotion(); reply("ERR",seq,"LIMIT"); return;
    }
    if (!enableAtomicPulseWrites()) {
      stopMotion(); pcaReady=false; reply("ERR",seq,"I2C"); return;
    }
    lowLimit=low; highLimit=high;
    target=current; armed=true; lastMotion=lastStep=millis(); reply("ACK",seq,"ARMED"); return;
  }
  if (!armed) { reply("ERR",seq,"NOT_ARMED"); return; }
  if (!strcmp(cmd,"HOLD") && count==3) {
    target=current; lastMotion=millis(); reply("ACK",seq,"HOLD"); return;
  }
  if (!strcmp(cmd,"MOVE") && count==4 && (!strcmp(parts[3],"-1") || !strcmp(parts[3],"1"))) {
    target = !strcmp(parts[3],"-1") ? lowLimit : highLimit;
    lastMotion=millis(); reply("ACK",seq,"MOVING"); return;
  }
  if (!strcmp(cmd,"GOTO") && count==4) {
    uint32_t ticks;
    if (!sequence(parts[3],ticks) || ticks<(uint32_t)lowLimit || ticks>(uint32_t)highLimit) {
      stopMotion(); reply("ERR",seq,"LIMIT"); return;
    }
    target=ticks; lastMotion=millis(); reply("ACK",seq,"TARGET"); return;
  }
  stopMotion(); reply("ERR",seq,"BAD_FORMAT");
}
void setup() {
  Serial.begin(115200); Wire.begin(); Wire.setWireTimeout(25000,true);
  // No automatic output, ARM or motion after boot/reconnection.
  Serial.println(F("BOOT,PANKEY2"));
}
void loop() {
  if (armed && millis()-lastMotion>=WATCHDOG_MS) stopMotion();
  // Bound parsing work so incoming garbage cannot starve the watchdog.
  for (byte budget=0; budget<32 && Serial.available();budget++) {
    char ch=Serial.read();
    if (ch=='\r') continue;
    if (ch=='\n') {
      if (overflow) stopMotion();
      else if (used) { line[used]=0; processLine(); }
      used=0; overflow=false;
    } else if (ch<32 || ch>126) { overflow=true; stopMotion(); }
    else if (used<96 && !overflow) line[used++]=ch;
    else overflow=true;
  }
  unsigned long now=millis();
  if (armed && now-lastMotion>=WATCHDOG_MS) stopMotion();
  if (armed && target != current && now-lastStep>=STEP_MS) {
    lastStep=now;
    int next=current+(target>current ? 1 : -1);
    if (!writePulse(next)) { pcaReady=false; stopMotion(); }
  }
}
