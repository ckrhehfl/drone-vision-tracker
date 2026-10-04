---
name: review-control
description: Pan/Tilt 제어·Serial·펌웨어 변경의 범위 제한·watchdog·재연결·모의 실행 안전성을 검토한다.
---

[리뷰 계약](../review-orchestrator/SKILL.md)과 docs/02_serial_protocol.md를 기준으로 읽기 전용 검토한다.
실제 포트 개방, ARM, 펌웨어 업로드, 모터·레이저 구동을 하지 않는다.

- Pan/Tilt 범위·clamp·정수 검증, dt·좌표계·부호·속도 제한.
- hardware_enabled=false와 dry-run에서 실제 hardware command가 생기는 경로.
- calibration.completed만 검사하는 오류, 축별 null·순서·부호·펄스·프로토콜·운영자 확인.
- stale target/관측 누락 시 SET 갱신 중단, 명령 큐 누적 및 ACK timeout.
- disconnect·hardware unavailable·재연결 뒤 자동 ARM/자동 이동.
- session·seq·부분 수신·최대 줄 길이·역순·중복 처리.
- PING·중복·잘못된 명령의 watchdog 연장 여부.
- 명령값·ACK를 실제 각도·도달·물리적 정지로 설명하는 오류.
- v1.1의 축 보정과 별도 2축 운용 범위, 바깥 오차 지속 시 내부 목표/속도 누적 방지.
- 유효한 안쪽 목표 복귀에서 구동 허가·속도·변화율 제한, 명령 포화와 관측 소실의 구분.
- 포화를 실제 스톨/끝단/머리 위 위치로 오인하거나 자동 뒤집기·급회전을 추가하는 경로.

거리·추가 축의 부재는 결함이 아니다. [SC01–SC08](../../../docs/07_single_camera_operating_envelope.md)을
관련 구현에 연결하되 아직 없는 런타임의 시험을 문서 CI로 검증했다고 쓰지 않는다.

JSON finding에 재현 조건·영향·수정 방향·검증 테스트를 포함한다.
실제 방향·jitter·limit·Serial 응답은 별도의 현장 관찰로 남긴다.
