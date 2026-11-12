# Arduino 펌웨어

`vision_follow/vision_follow.ino`는 카메라 GUI의 **실시간 YOLO 통합 시운전** 전용이다.
프로파일 `VISION_TICKS1`, 두 축80~330틱, 기본 출력 허용 false, 명시적 연결·구동 확인을 사용한다.
기존 잔류 명령을 출력 쓰기 없이 이어받고 자동 초기화·원점 복귀하지 않는다.
전체 범위의 바이트 경계는 OCH ACK 모드의4레지스터 쓰기로 처리하며, 오류 뒤 리셋은 잠근다.
기존 시운전·수동·340틱 실험 펌웨어와 호환되지 않는다. [연결 순서와 한계](../docs/vision-motors.md)를 따른다.

`pan0_follow/pan0_follow.ino`는 Uno R3 + PCA9685(0x40)의 **0번 축 한정 시운전**이다.
기본 빌드는 출력 허용이 꺼져 있다. 2축 프로토콜의 완성이나 실물 검증을 뜻하지 않는다.
[시운전 안내](../docs/pan0-motor-test.md)와 [시험 프로파일](../docs/02_serial_protocol.md#7-pan0_test1-시운전-프로파일)을 따른다.

고정 버퍼·세션/순서 검사·최대 1틱/50ms·500ms watchdog을 사용한다.
연결과 ARM은 PWM을 시작하지 않는다. 첫 SET만 별도로 확인한 시작 틱을 출력한다.
일반 PAN0 모드는 채널 1 PWM을 쓰지 않는다. 별도 CENTER01_205 빌드는 명시적 SET으로만
0·1번을 모두 205틱으로 초기화한다. DISARM은 전원 차단이 아닌 마지막 PWM 명령 유지다.
Arduino CLI 1.5.1 / AVR core 1.8.8로 CI에서 컴파일만 하며 장치를 열거나 업로드하지 않는다.
