#include <Wire.h>
#include <PCA9685.h>  // Arduino Library Manager의 PCA9685_RT, Rob Tillaart.
#include <stdlib.h>
#include <string.h>

/*
 * servo0_manual.ino에서 유래한 0번 축 키보드 수동 시험용 스케치.
 * 시작/리셋 시 PWM을 새로 출력하지 않으며, 세션과 구동 허가가 필요하다.
 * 유효한 이전 목표가 없으면 사용자가 명시적으로 초기 목표를 지정해야 한다.
 * 0~180  : 왼쪽 끝 0도(330틱), 중간 90도(205틱), 오른쪽 끝 180도(80틱).
 * 숫자는 고정된 명령각 좌표이며, 마지막 위치에 누적하지 않는다.
 * GUI는 한 번에 한 명령만 보내며 유효한 HOLD/STEP/SET으로 허가를 유지한다.
 *
 * 실물 시험 기록 (영점 307틱, 현재 장착 기준, 각도는 명령값):
 * 왼쪽 +20 (330틱) 정상, +25 (335틱) 움직임 불분명, +30 (341틱) 걸림 소리.
 * 70틱에서 추가 회전/소리 없음. 재검증 후 사용자가 오른쪽 임시 끝점을 80틱으로 지정했다.
 * 임시 사용 범위는 오른쪽 80틱~왼쪽 330틱. 양 끝의 산술 중간값은 205틱이다.
 * 이전 임시 영점 307틱은 이 고정 좌표에서 약 17도 명령에 해당한다.
 * 0~180은 사용자가 정한 제어 눈금이다. 실제 회전각과 선형성은 측정해 보정한다.
 * PAN_LEFT/RIGHT_TICKS는 현재 장착에서 사용자가 지정한 임시 사용 끝점이다.
 */

const byte PCA_ADDRESS = 0x40;
const byte PAN_CHANNEL = 0;
const int PAN_LEFT_TICKS = 330;     // 0도 명령. 341틱에서 걸림 소리가 났다.
const int PAN_RIGHT_TICKS = 80;     // 180도 명령. 사용자가 지정한 오른쪽 임시 끝점.
const int JOG_TICKS = 10;
const unsigned long WATCHDOG_MS = 500;
const unsigned long STEP_MS = 50;
// AVR Wire can wait up to 25 ms for bus readiness and another 25 ms for the write.
const unsigned long WRITE_MARGIN_MS = 60;

static_assert(PAN_RIGHT_TICKS > 0 && PAN_LEFT_TICKS < 4096 &&
              PAN_RIGHT_TICKS < PAN_LEFT_TICKS, "Invalid PAN PWM endpoints");

PCA9685 pwm(PCA_ADDRESS);

bool ready = false;
bool armed = false;
int targetTicks = -1;  // 마지막 PWM 목표. 센서로 읽은 축 위치는 아니다.
int destinationTicks = -1;
char session[9] = "";
uint32_t lastSequence = 0;
unsigned long lastCommand = 0;
unsigned long lastStep = 0;

bool readRegister(byte reg, byte &value) {
  Wire.beginTransmission(PCA_ADDRESS);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0 || Wire.getWireTimeoutFlag()) return false;
  if (Wire.requestFrom(PCA_ADDRESS, (byte)1) != 1 || Wire.getWireTimeoutFlag()) return false;
  value = Wire.read();
  return true;
}

bool writeRegister(byte reg, byte value) {
  Wire.beginTransmission(PCA_ADDRESS);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0 && !Wire.getWireTimeoutFlag();
}

void fail() {
  ready = false;
  armed = false;
  destinationTicks = targetTicks;
  Serial.println(F("I2C error. Previous PWM may still be active."));
}

void stopMotion() {
  armed = false;
  destinationTicks = targetTicks;
}

bool configured(bool requireAtomic) {
  byte mode, mode2, prescale;
  return readRegister(0x00, mode) && readRegister(0x01, mode2) &&
         readRegister(0xFE, prescale) && (mode & 0x70) == 0x20 &&
         (mode2 & 0x10) == 0 && (!requireAtomic || (mode2 & 0x08)) && prescale == 121;
}

bool enableAtomicWrites() {
  byte mode2;
  if (!readRegister(0x01, mode2) || (mode2 & 0x10)) return false;
  if (!(mode2 & 0x08) && !writeRegister(0x01, mode2 | 0x08)) return false;
  return configured(true);
}

bool currentOutputMatches() {
  uint16_t on, off;
  if (pwm.getPWM(PAN_CHANNEL, &on, &off) != PCA9685_OK || Wire.getWireTimeoutFlag()) return false;
  if (targetTicks < 0) return (off & 0x1000) != 0;
  return on == 0 && off == targetTicks;
}

// shortcut: 명령각을 두 PWM 끝점에 선형 대응한다. 실제 각도를 측정해 보정한다.
int angleToTicks(int degrees) {
  long offset = ((long)degrees * (PAN_LEFT_TICKS - PAN_RIGHT_TICKS) + 90) / 180;
  return PAN_LEFT_TICKS - offset;
}

bool moveTo(int ticks, bool requireMotionLease) {
  // 숫자 입력과 미세 이동 모두 사용자가 지정한 절대 PWM 끝점으로 제한한다.
  if (ticks < PAN_RIGHT_TICKS || ticks > PAN_LEFT_TICKS) {
    Serial.println(F("Target outside PAN_RIGHT_TICKS..PAN_LEFT_TICKS."));
    return false;
  }
  if (!configured(true) || !currentOutputMatches()) {
    fail();
    return false;
  }
  if (requireMotionLease &&
      (!armed || millis() - lastCommand >= WATCHDOG_MS - WRITE_MARGIN_MS)) {
    stopMotion();
    return false;
  }
  if (pwm.setPWM(PAN_CHANNEL, 0, ticks) != PCA9685_OK || Wire.getWireTimeoutFlag()) {
    fail();
    return false;
  }
  // 레지스터에 명령이 적용됐는지 확인한다. 실제 축 각도는 눈으로 확인한다.
  uint16_t on, off;
  if (pwm.getPWM(PAN_CHANNEL, &on, &off) != PCA9685_OK || Wire.getWireTimeoutFlag() ||
      on != 0 || off != ticks) {
    fail();
    return false;
  }
  targetTicks = ticks;
  return true;
}

void reply(const char *kind, uint32_t sequence, const char *state) {
  Serial.print(kind); Serial.print(','); Serial.print(session); Serial.print(',');
  Serial.print(sequence); Serial.print(','); Serial.print(state); Serial.print(',');
  Serial.println(targetTicks);
}

void setup() {
  Serial.begin(115200);
  Serial.println(F("BOOT,SERVO0_SAFE"));
  Wire.begin();
  Wire.setWireTimeout(25000, true);

  byte mode, mode2, prescale;
  if (!readRegister(0x00, mode) || !readRegister(0x01, mode2) ||
      !readRegister(0xFE, prescale) || (mode2 & 0x10)) {
    fail();
    return;
  }
  Serial.print(F("MODE1="));
  Serial.print(mode, HEX);
  Serial.print(F(" PRESCALE="));
  Serial.println(prescale);

  // 주파수는 공통이므로 초기 상태에서 모든 출력이 꺼져 있을 때만 설정한다.
  if (mode == 0x11 && (prescale == 30 || prescale == 121)) {
    for (byte reg = 0x09; reg <= 0x45; reg += 4) {
      byte off;
      if (!readRegister(reg, off)) {
        fail();
        return;
      }
      if ((off & 0x10) == 0) {
        Serial.println(F("An output is enabled; leaving the shared frequency unchanged."));
        return;
      }
    }
    if (!writeRegister(0xFE, 121) || !writeRegister(0x00, 0x21)) {
      fail();
      return;
    }
    delay(1);
    if (!readRegister(0x00, mode) || !readRegister(0xFE, prescale)) {
      fail();
      return;
    }
  }
  if ((mode & 0x50) != 0 || prescale != 121) {
    Serial.println(F("PCA9685 must use awake internal-clock 50Hz setup; no movement command sent."));
    return;
  }
  if ((mode & 0x20) == 0 && !writeRegister(0x00, (mode & 0x7F) | 0x20)) {
    fail();
    return;
  }
  // PCA9685_RT의 생성자는 출력을 바꾸지 않는다. 위에서 확인한 50Hz를 그대로 쓴다.
  // setPWM/getPWM은 별도 begin 없이도 사용할 수 있고 오류를 반환한다.
  uint16_t on, off;
  if (pwm.getPWM(PAN_CHANNEL, &on, &off) != PCA9685_OK || Wire.getWireTimeoutFlag()) {
    fail();
    return;
  }
  // A register read cannot prove the last latched output after an interrupted write.
  if (!(off & 0x1000)) {
    Serial.println(F("Existing channel 0 output requires a PCA power reset."));
    return;
  }
  destinationTicks = targetTicks;
  ready = true;
}

bool validToken(const char *token) {
  if (strlen(token) != 8) return false;
  for (byte i = 0; i < 8; i++) {
    if (!((token[i] >= '0' && token[i] <= '9') || (token[i] >= 'A' && token[i] <= 'F'))) return false;
  }
  return true;
}

bool parseUnsigned(const char *text, uint32_t &value) {
  if (!*text) return false;
  value = 0;
  while (*text) {
    if (*text < '0' || *text > '9' || value > (UINT32_MAX - (*text - '0')) / 10) return false;
    value = value * 10 + (*text++ - '0');
  }
  return true;
}

void handleCommand(char *line) {
  if (armed && millis() - lastCommand >= WATCHDOG_MS) stopMotion();
  char *parts[4] = {line, NULL, NULL, NULL};
  byte count = 1;
  for (char *p = line; *p; p++) {
    if (*p == ',') {
      if (count == 4) { stopMotion(); return; }
      *p = '\0';
      parts[count++] = p + 1;
    }
  }
  if (count == 2 && strcmp(parts[0], "HELLO") == 0 && validToken(parts[1])) {
    stopMotion();
    if (ready && (!configured(false) || !currentOutputMatches())) fail();
    strcpy(session, parts[1]);
    lastSequence = 0;
    Serial.print(F("READY,SERVO0_SAFE,")); Serial.print(session); Serial.print(',');
    Serial.print(ready ? F("OK,") : F("FAULT,")); Serial.println(targetTicks);
    return;
  }
  // STOP can overtake an outstanding ACK; it never refreshes the watchdog.
  if (count == 2 && strcmp(parts[0], "STOP") == 0 && *session &&
      strcmp(parts[1], session) == 0) {
    destinationTicks = targetTicks;
    return;
  }

  uint32_t sequence;
  if (count < 3 || !*session || strcmp(parts[1], session) != 0 ||
      !parseUnsigned(parts[2], sequence) || sequence == 0) {
    stopMotion();
    return;
  }
  if (sequence <= lastSequence) {
    stopMotion();
    reply("ERR", sequence, "STALE");
    return;
  }
  lastSequence = sequence;
  if (count == 3 && strcmp(parts[0], "DISARM") == 0) {
    stopMotion();
    reply("ACK", sequence, "DISARMED");
    return;
  }
  if (!ready) { stopMotion(); reply("ERR", sequence, "I2C"); return; }
  if (count == 4 && strcmp(parts[0], "INIT") == 0 && !armed && targetTicks < 0) {
    uint32_t angle;
    if (!parseUnsigned(parts[3], angle) || angle > 180) { reply("ERR", sequence, "LIMIT"); return; }
    if (!enableAtomicWrites() || !moveTo(angleToTicks(angle), false)) {
      fail(); reply("ERR", sequence, "I2C"); return;
    }
    destinationTicks = targetTicks;
    reply("ACK", sequence, "INITIALIZED");
    return;
  }
  if (count == 3 && strcmp(parts[0], "ARM") == 0 && targetTicks >= 0) {
    if (!enableAtomicWrites() || !currentOutputMatches()) {
      fail(); reply("ERR", sequence, "I2C"); return;
    }
    armed = true;
    destinationTicks = targetTicks;
    lastCommand = lastStep = millis();
    reply("ACK", sequence, "ARMED");
    return;
  }
  if (!armed) { reply("ERR", sequence, "NOT_ARMED"); return; }
  if (!configured(true) || !currentOutputMatches()) {
    fail(); reply("ERR", sequence, "I2C"); return;
  }
  if (millis() - lastCommand >= WATCHDOG_MS) {
    stopMotion(); reply("ERR", sequence, "TIMEOUT"); return;
  }
  if (count == 3 && strcmp(parts[0], "HOLD") == 0) {
    destinationTicks = targetTicks;
    lastCommand = millis();
    reply("ACK", sequence, "HOLD");
    return;
  }
  if (count == 4 && strcmp(parts[0], "STEP") == 0 &&
      (strcmp(parts[3], "+") == 0 || strcmp(parts[3], "-") == 0)) {
    destinationTicks = constrain(targetTicks + (parts[3][0] == '+' ? JOG_TICKS : -JOG_TICKS),
                                 PAN_RIGHT_TICKS, PAN_LEFT_TICKS);
    lastCommand = millis();
    reply("ACK", sequence, "STEP");
    return;
  }
  if (count == 4 && strcmp(parts[0], "SET") == 0) {
    uint32_t angle;
    if (!parseUnsigned(parts[3], angle) || angle > 180) {
      stopMotion(); reply("ERR", sequence, "LIMIT"); return;
    }
    destinationTicks = angleToTicks(angle);
    lastCommand = millis();
    reply("ACK", sequence, "TARGET");
    return;
  }
  stopMotion();
  reply("ERR", sequence, "BAD_FORMAT");
}

void loop() {
  static char line[97];
  static byte length = 0;
  static bool invalid = false, sawCR = false;
  if (armed && millis() - lastCommand >= WATCHDOG_MS) stopMotion();
  for (byte budget = 0; budget < 32 && Serial.available() > 0; budget++) {
    char input = Serial.read();
    if (input == '\n') {
      if (invalid) stopMotion();
      else if (length > 0) { line[length] = '\0'; handleCommand(line); }
      length = 0; invalid = false; sawCR = false;
    } else if (input == '\r' && !sawCR) {
      sawCR = true;
    } else if (!sawCR && length < sizeof(line) - 1 && input >= 32 && input <= 126) {
      line[length++] = input;
    } else {
      invalid = true;
      stopMotion();
    }
  }
  unsigned long now = millis();
  if (armed && now - lastCommand >= WATCHDOG_MS) stopMotion();
  if (armed && destinationTicks != targetTicks && now - lastStep >= STEP_MS) {
    int next = targetTicks + (destinationTicks > targetTicks ? 1 : -1);
    if (moveTo(next, true)) lastStep = millis();
  }
}
