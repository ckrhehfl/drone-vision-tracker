---
name: decision-gate
description: CI·독립 리뷰·사람 승인·실물 증거를 확인해 진행 또는 중단을 판단하며 코드를 수정하지 않는다.
---

[운영 규칙](../../../docs/automation/operating-policy.md)을 읽는다.
코드 구현·수정·commit·push를 하지 않는다. 같은 최신 base/head에 묶인 증거만 평가한다.
일반 구조·버그·lint·타입·테스트·mock·logging·성능 선택은 에이전트가 해결하게 한다.
운영 규칙의 12가지 사람 판단 사유만 HUMAN_DECISION_REQUIRED로 전환한다.
단순 Secret 등록/인증/규칙 설정은 HUMAN_ACTION_REQUIRED로 안내한다. 키 값은 요구하지 않는다.

실제 카메라 FPS, detection 품질, servo 방향/jitter, Pan/Tilt limit, Serial 연결,
physical tracking response, laser calibration은 PHYSICAL_TEST_REQUIRED다.
관찰 방법과 기대 결과를 적고 merge blocker 여부를 요구사항에서 결정한다.
레이저는 MVP 밖이며 이 Skill이 활성화 권한을 주지 않는다.
모의 시험이나 ACK를 실물 검증으로 간주하지 않는다.

Phase 9 활성화 후에만 최신 CI PASS + 최신 Review PASS + blocking 0 + Decision PASS +
미해결 사람 결정 없음 + blocker 실물 시험 완료를 병합 조건으로 인정한다.
누락/실패/취소/skip/stale/서로 다른 SHA는 PASS가 아니다.
현재는 Gate/Merge 실행 권한과 자동화가 활성화되지 않았으므로 판단 지침만 제공한다.
