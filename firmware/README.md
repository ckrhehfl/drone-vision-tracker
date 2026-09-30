# Arduino 펌웨어 — 구현 예정

Uno R3 + PCA9685용. 아직 .ino 코드·컴파일 결과·실물 시험은 없다. docs/02_serial_protocol.md와 공통 안전 규칙을 기준으로 고정 버퍼, 비블로킹 파서, 자체 각도·속도 제한, watchdog, 재ARM 요구를 구현한다. 모터가 없는 상태의 프로토콜 시험을 먼저 만든다.
