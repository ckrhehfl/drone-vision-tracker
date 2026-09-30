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
