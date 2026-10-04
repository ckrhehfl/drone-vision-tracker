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
승인된 깨끗한 최신 main에서 `python -m tools.decision_gate --directory <리뷰 폴더>
--publication-run <게시 run ID>`를 실행한다. 최신 CI, 전체 게시 artifact/status와 로컬 영구
수정 횟수를 재검증한다. 출력 JSON은 판단 시점의 기록이며 재사용 가능한 병합 허가가 아니다.
`tools.local_pipeline`도 게시 후 이 Gate를 호출하며 `decision.json`을 남긴다.
FIX는 남은 한도 내 일반 수정, REVIEW는 미검증 blocker의 분류를 reviewer에게 돌려보내는
경로다. 모호한 항목을 임의로 사람/실물 승인으로 추정하지 않는다. STOP이면 해당 gate를 따른다.
관찰 방법/기대 결과를 그대로 전달하고 승인받은 실물 시험 이후 새 리뷰로 미검증 상태를 갱신한다.
Phase 8 당시 서버 decision-gate status와 merge 기능은 비활성이었다.
검증된 Phase 9의 [별도 병합기](../../../docs/automation/auto-merge.md)는 Gate 게시와 최신
증거·main 보호·활성화 설정·요청자 권한을 다시 검증한다. 이 Skill과 Gate 자체는 병합하지 않는다.
