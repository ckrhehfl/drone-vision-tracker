# USB Serial 프로토콜 v1 — 설계 규격

상태: 제안 기준선. 구현·실물 검증 전. 카메라 팬·틸트 서보에만 적용하며 레이저 명령은 없다.

## 1. 프레이밍
115200bps, 8N1, ASCII, 줄 끝 LF. CRLF의 CR은 제거한다. 한 줄은 LF 제외 최대 96바이트다. 넘친 줄은 다음 LF까지 버리고 ERR을 기록한다. 고정 버퍼와 비블로킹 파서를 사용한다. Arduino에서 무제한 String 누적이나 긴 delay로 watchdog을 막지 않는다.

쉼표로 필드를 나눈다. 공백·필드 수·정수 범위는 엄격하게 검사한다. 문자열 `NaN`, `Inf`, 소수, 지수표기, 빈 필드는 각도 정수로 수락하지 않는다. 각도 값은 centidegree(0.01도) 단위의 **논리 명령값**이다. 숫자의 해상도가 기구 정확도를 뜻하지 않는다.

## 2. 세션과 순서
호스트는 연결할 때마다 무작위 8자리 16진 session을 생성한다. 이는 오래된 명령을 구분하는 토큰이며 보안 인증이 아니다. sequence는 1부터 증가하는 uint32 정수다. 넘치기 전에 새 세션을 연다.

```text
HELLO,1,A1B2C3D4
READY,1,A1B2C3D4,DISARMED,UNCALIBRATED
ARM,A1B2C3D4,1
SET,A1B2C3D4,2,9000,9000
HOLD,A1B2C3D4,3
DISARM,A1B2C3D4,4
PING,A1B2C3D4,5
ACK,A1B2C3D4,3,HOLD,9000,9000
ERR,A1B2C3D4,2,OUT_OF_RANGE
```

위 값은 **형식 예시**이며 90도가 안전하다는 뜻이 아니다. 보정 완료 전 ARM은 CALIBRATION_REQUIRED로 거부한다. READY의 마지막 필드는 CALIBRATED 또는 UNCALIBRATED다.

HELLO는 기존 궤적을 HOLD하고 구동 잠금을 건 뒤 새 세션을 연다. READY 전에는 ARM·SET을 수락하지 않는다. 수신 세션이 다르면 SESSION_MISMATCH. 역순 seq는 STALE_SEQUENCE. 동일 seq와 동일 payload 재전송은 같은 결과를 응답하되 재구동·watchdog 갱신을 하지 않는다. 같은 seq에 다른 내용은 SEQUENCE_CONFLICT다.

## 3. 명령 의미
| 명령 | 동작 |
|---|---|
| ARM | 로컬에 유효한 보정이 있고 호스트 운영자 승인이 있을 때 허용. 즉시 중립으로 이동하지 않음 |
| SET | 검증된 팬·틸트 절대 목표를 설정. 축별 범위 밖은 거부 |
| HOLD | 진행 중 목표 접근을 취소. 마지막 PWM 명령값을 목표로 유지 |
| DISARM | HOLD + 이후 SET 거부. 전원·PWM 강제 차단과 다름 |
| PING | PONG으로 응답. 모션 watchdog을 갱신하지 않음 |

PONG은 `PONG,session,seq,state`다. ACK는 `ACK,session,seq,state,pan_cd,tilt_cd`이고 마지막 두 필드는 출력한 명령값이다. 아직 출력 이력이 없으면 두 필드를 `NA`로 반환한다. 이는 ACK의 해당 필드에만 허용하며 SET에 사용할 수 없다.

부팅 시 출력 활성화 전 기구부를 지지한다. 실제 PWM 출력 시작은 유효 보정과 현장 준비 후 허용한다. 첫 명령 위치를 알 수 없는 상태에서 고속 이동을 하지 않도록 수동 시운전 절차를 별도 수행한다.

## 4. 시간과 장애
호스트 목표 전송 최대 20Hz. 제어 tick·펌웨어 tick은 별도 기록한다. 펌웨어 모션 watchdog 초기값은 500ms다. ARM 시 타이머를 시작하고 그 뒤 **새롭고 유효한 SET/HOLD**만 타이머를 갱신한다. 중복·잘못된 메시지·PING은 갱신하지 않는다.

watchdog 만료: 내부 목표를 마지막 출력으로 바꾸고 DISARMED 상태로 잠근다. 재ARM 전 SET을 거부한다. 마지막 명령을 계속 증가시키거나 자동 원점 복귀하지 않는다. 소프트웨어 정지는 모터 관성이나 실제 위치를 보장하지 않는다.

호스트는 한 번에 무제한 미응답 SET을 쌓지 않는다. 최신 목표 슬롯 하나와 제한된 pending ACK 상태를 사용한다. ACK 지연·장애 시 FAULT로 진입하고 장치가 응답하면 DISARM을 요청한다. 불통이면 펌웨어 watchdog에 의존하되 이를 물리적 비상정지로 설명하지 않는다.

## 5. 오류 코드
BAD_FORMAT, UNSUPPORTED_VERSION, LINE_TOO_LONG, SESSION_MISMATCH, STALE_SEQUENCE, SEQUENCE_CONFLICT, NOT_ARMED, CALIBRATION_REQUIRED, OUT_OF_RANGE, INTERNAL_FAULT.

파싱하지 못한 session·seq는 ERR에서 `UNKNOWN,0`으로 표시한다. 잘못된 줄을 이유로 현재 출력값을 임의 숫자로 덮어쓰지 않는다. 반복 오류는 로그와 구동 잠금으로 처리한다.

## 6. 필수 시험
정상 명령, 음수·과대값·소수·NaN, 부분 수신, 두 줄 동시 수신, CRLF, 96바이트 경계·초과, 잘못된 세션, 중복·역순 seq, 같은 seq의 다른 payload, 보정 전 ARM, DISARM 후 SET, 프로세스 강제 종료, USB 분리, 연결 복원 후 재ARM을 시험한다.

명령 수락, PWM 갱신, 실제 각도 도달은 서로 다른 사건이다. ACK로 마지막 사건을 주장하지 않는다.

## 7. PAN0_TEST1 시운전 프로파일

`firmware/pan0_follow`와 `tools.pan0_follow`의 0번 단독 시험은 위 프레이밍·세션·명령·
centidegree 눈금·watchdog을 재사용한다. **2축 v1 구현 완료가 아니다.**
1번 미보정을 CALIBRATED로 오인하지 않도록 준비된 빌드도 마지막 READY 필드를 `PAN0_TEST1`로
반환하며 일반 v1 클라이언트는 이를 CALIBRATED로 수락하면 안 된다. 기본 빌드는 UNCALIBRATED다.

```text
READY,1,A1B2C3D4,DISARMED,PAN0_TEST1
PROFILE,PAN0_TEST1,200,205,210
SET,A1B2C3D4,2,9000,0
ACK,A1B2C3D4,2,TRACK,9000,NA
```

PROFILE의 세 값은 하한/시작/상한 틱이며 위 값은 합성 예시다. 호스트는 포트 개방 전에 운영자
확인을 받고, 이후 준비 필드와 PROFILE이 모두 정확히 맞을 때만 ARM과 첫 SET을 진행한다.
SET의 tilt 필드는 예약 정수 0만 허용하고
채널 1 PWM은 쓰지 않는다. ACK의 tilt는 항상 NA다. pan은 `(330 - ticks) * 72` 명령 눈금이며
실제 각도가 아니다. 첫 SET은 확인된 시작 틱만 허용한다. 이후 1틱/50ms로만 접근한다.
HOLD/DISARM은 현재 출력 틱으로 목표를 바꾼다. 같은 세션 HELLO도 DISARM하며 seq를 되돌리지
않고 이전 ACK 캐시를 무효화한다. 잘못된 형식·범위·역순/충돌은 구동을 잠근다.
상세한 빌드와 현장 준비는 [시운전 안내](pan0-motor-test.md)를 따른다.

부팅 시 `BOOT,PAN0_TEST1` 다음에는 `PWM0,on,off,mode1,mode2,prescale` 진단 줄을 출력한다.
모두 초기화 쓰기 전 R/W 레지스터의 원시 정수값이며, I2C 읽기 실패는 `PWM0,UNREADABLE`이다.
출력 허용이나 실제 위치의 확인이 아니다. 일반 호스트는 부팅 대기 후 수신 버퍼를 비우고
HELLO를 시작하므로 기존 READY/PROFILE 교환 순서는 유지된다.

유효한 PAN0_TEST1 빌드의 부팅 시 채널 0의 `on=0, off=START_TICKS`가 읽히고
MODE1/MODE2/PRE_SCALE이 일치하면, PWM 쓰기 없이 그 시작 명령을 이어받아 DISARMED로 준비한다.
다른 활성 값이나 비활성 기본 빌드는 이 값을 자동으로 이어받지 않는다. ARM은 여전히 별도다.

## 8. CENTER01_205 고정 초기화 프로파일

2026-11-11 사용자가 두 축의 80~330틱 움직임 안전을 확인했고 두 채널을 205틱으로
초기화하도록 직접 지시했다. `PAN01_CENTER205_ONLY=1`과 `PAN0_HARDWARE_ENABLED=1`을
함께 지정한 빌드는 기존 세션·ARM·SET·DISARM을 재사용하되 SET은 **9000,9000만** 허용한다.
0번과 1번을 모두 205틱으로 설정하며 다른 목표나 자동 추종은 수행하지 않는다.

```text
READY,1,A1B2C3D4,DISARMED,CENTER01_205
PROFILE,CENTER01_205,205,205
ARM,A1B2C3D4,1
SET,A1B2C3D4,2,9000,9000
ACK,A1B2C3D4,2,TRACK,9000,9000
DISARM,A1B2C3D4,3
ACK,A1B2C3D4,3,DISARMED,9000,9000
```

기본 빌드는 계속 비활성이다. 연결과 ARM은 PWM을 쓰지 않는다. 두 채널 모두 FULL_OFF로
시작해야 하며 SET 때 두 채널을 FULL_OFF 상태에서 준비·검증한 다음 각각 205틱을 활성화한다.
두 채널의 활성화는 원자적이지 않다. 오류 시 한 채널만 205틱이 적용됐을 수도 있으므로
성공 응답과 읽기 검증이 없으면 외부전원을 복구하지 않는다. 부팅 진단에 PWM1 원시 on/off도 추가한다.
PWM 쓰기는 0·1번에 한정되며 설정 후에는 watchdog/DISARM도 두 205틱을 유지한다.
외부전원 인가 시 현재 위치에서 205틱 목표로 움직일 수 있다. 이 첫 이동에는 속도 제한이 없다.
ACK는 검증된 PWM 명령이며 실제 각도나 도달 확인이 아니다. 일반 PAN0 호스트는 이 프로파일을 거부한다.

## 9. VISION_TICKS1 통합 시운전 프로파일

기존 centidegree `SET`과 구분해 원시틱을 쓰는 전용 프로파일이다. 기존 v1 일반 SET이나
PAN0_TEST1·수동/간섭 시험 클라이언트는 이 프로파일에 연결하지 않는다.
GUI의 실시간 YOLO만 별도 모터 worker로 연결하며, 연결·영상 시작으로 자동 ARM하지 않는다.
기본 펌웨어 출력 허용은 false이며 [현장 준비](vision-motors.md)가 필요하다.

```text
HELLO,1,A1B2C3D4
READY,1,A1B2C3D4,DISARMED,VISION_TICKS1,208,205
LIMITS,VISION_TICKS1,80,330
ARM,A1B2C3D4,1
ACK,A1B2C3D4,1,ARMED,208,205
SET_TICKS,A1B2C3D4,2,207,206
ACK,A1B2C3D4,2,TRACK,208,205
HOLD,A1B2C3D4,3
ACK,A1B2C3D4,3,HOLD,208,205
DISARM,A1B2C3D4,4
ACK,A1B2C3D4,4,DISARMED,208,205
```

READY의 두 숫자는 이미 존재하는 PWM 명령이다. FULL_OFF·범위 밖·클록/모드 불일치·읽기 오류면
`DISARMED` 대신 `LOCKED`이며 호스트는 ARM 전에 거부한다. 자동 초기화와205틱 이동은 없다.
SET_TICKS의 두 값은 정수80..330, ACK의 두 값은 마지막 확인 출력 명령이다. ACK는 목표 도달이 아니다.
최대1틱/50ms로 접근하고 HOLD는 그 진행을 현재 출력 명령에서 취소한다.
프레임 신선도100ms, 목표 전송 최대20Hz, ACK150ms, 펌웨어 watchdog500ms는 유지한다.

ARM은 OCH ACK 모드만 설정하고 PWM 명령은 쓰지 않는다. 각 축 PWM의4레지스터를 모두 전송한다.
정상 DISARM은 일관된 PWM 확인 후 OCH STOP 모드로 복원하고, I2C 오류 후에는 복원하지 않아
리셋 재접속도 잠근다. HELLO와 새 세션은 구동을 해제하며, 같은 세션의 sequence를 초기화하지 않는다.
중복은 과거 ACK만 반환하고 이동·watchdog을 갱신하지 않는다. 이전/충돌 sequence와 잘못된
형식·범위는 구동을 잠근다. 진단 PING은 ACK 형식의 ARMED/DISARMED를 반환하되 watchdog을 연장하지 않는다.
호스트는 오류 후 FAULT를 유지하며 DISARM 이외 재전송·재ARM을 하지 않는다.
정리 DISARM의 유효한 과거 ACK만 고정 deadline 안에서 건너뛴다.

### 잠금 진단

구동 해제 또는 LOCKED 상태에서 `DIAG,<session>,<seq>`로 실제 레지스터를 읽을 수 있다.
세션·sequence 검증과 중복 응답 규칙을 그대로 따른다. 출력·모드·클록·ready 상태를 바꾸거나
watchdog을 연장하지 않는다. ARMED에서는 `ERR,...,DIAG_REQUIRES_DISARM`, 읽기 오류는
`ERR,...,DIAG_READ_FAILED`다. 이 진단은 현재 위치·실제 각도·물리적 정지를 측정하지 않는다.

```text
DIAG,A1B2C3D4,1
DIAG,A1B2C3D4,1,1,32,12,121,0,82,0,330
```

응답 필드는 `DIAG,session,seq,hardware_enabled,MODE1,MODE2,PRE_SCALE,ON0,OFF0,ON1,OFF1`다.
숫자는 십진 원시값이며 FULL_OFF 등 비트도 그대로 포함한다. 예제는 MODE2=12 표식이 남아
잠금 상태인 경우이며 자동 복구 허가가 아니다. GUI는 LOCKED 연결 때만 DIAG를 한 번 요청하고
검증한 사유를 표시한 뒤 포트를 닫는다. 진단에만 최대1초를 허용하며 구동 ACK150ms는 유지한다.
