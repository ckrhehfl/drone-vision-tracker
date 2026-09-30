# 출처와 근거 구분

확인일: 2026-09-30. 본문 [U1]~[S6]는 아래 자료를 가리킨다. 링크는 확인 당시의 공식 자료이며 설치·제작 때 버전을 다시 기록한다.

## 사용자 제공·승인 정보
**[U1] 사용자 첨부 「노션정리 하드웨어.txt」**
원본 85–98행의 최종 구매 목록, 101행의 사용 예산 763,980원을 사용했다. 5–8행은 감시·검출·좌표·서보 제어 흐름이다. 개별 단가, 최종 조립 치수, 서보 실측값은 원본에 없다. 검토 후보 18–32행을 최종 구매 목록으로 혼합하지 않았다.

**[U2] 본 대화의 승인 범위와 촬영 계획**
실내 지정 드론, 하드웨어 유지, 카메라 자동 추종 MVP, 레이저 후속 분리, GitHub와 Codex 구현/리뷰, 약 1,000장부터 영상 추출로 데이터셋 구성. 자료 수량·시험 성능은 실험 결과가 아니다.

## 공식 기술 참고
**[S1] INNO-MAKER U30CAM-4K-S1 제조사 저장소**
https://github.com/INNO-MAKER/U30CAM-4K-S1
1080p60·4K30 안내와 실제 FPS가 USB·PC 조건에 영향을 받는다는 FAQ를 참고했다. 특정 수령품의 FPS, 렌즈 화각 또는 세부 부품 호환을 실측한 것은 아니다.

**[S2] Adafruit PCA9685 서보 드라이버 연결 가이드**
https://learn.adafruit.com/16-channel-pwm-servo-driver/hooking-it-up
로직 VCC / 서보 V+ 분리와 Arduino에서 서보 전원을 직접 공급하지 않는 원칙을 참고했다. 다른 업체의 VLT-MD032 보드에 동일 보호회로가 있다는 근거로 사용하지 않았다.

**[S3] Ultralytics 공식 Tracking 문서**
https://docs.ultralytics.com/modes/track/
BoT-SORT·ByteTrack의 제공, 카메라 움직임 보정과 ReID, tracker 설정 명시 방식 참고. 기본 tracker가 바뀔 수 있으므로 프로젝트는 이름을 명시한다. 특정 모델 세대나 라이브러리 버전을 자동 확정하지 않았다.

**[S4] Ultralytics Tips for Best Training Results — YOLOv5 문서**
https://docs.ultralytics.com/yolov5/tutorials/tips-for-best-training-results/
다양성, 라벨 일관성, 데이터 분할 누수 방지, 배경 이미지 활용의 일반 원칙 참고. YOLOv5 전용 학습 epoch·모델 수치를 현재 프로젝트에 그대로 적용하지 않았다.

**[S5] OpenAI / Codex AGENTS.md 가이드**
https://developers.openai.com/codex/guides/agents-md/
프로젝트 지침과 코드 리뷰 규칙을 AGENTS.md에 두는 방식 참고. 현재 공식 문서로 리디렉션될 수 있다. 설치·로그인·GitHub 연동은 본 패키지로 수행되지 않는다.

**[S6] Notion 공식 가져오기 안내**
https://www.notion.com/help/import-data-into-notion
표준 Markdown·ZIP 가져오기 방식 참고. 실제 사용자 워크스페이스에 업로드하거나 렌더링을 검사한 것은 아니다.

## 이번 설계에서 새로 구체화한 것
시리얼 메시지, 파일 구조, 정지/세션/watchdog 계약, 예제 설정, 작업 단계, 검증 양식, 수치 목표는 이 프로젝트를 위한 제안이다. 제조사 보장, 이미 완료된 테스트, 사용자에게 따로 확인한 사실로 해석하지 않는다.
