---
name: review-tests
description: 테스트·설정·CI 변경에서 요구사항 누락과 실패 경로, 리뷰 증거의 SHA·권한 경계를 검토한다.
---

[리뷰 계약](../review-orchestrator/SKILL.md)을 따른다. 테스트 삭제나 assertion 약화로 성공을 만들지 않는다.

요구사항 대비 happy/failure path, timeout, invalid config, disconnected hardware,
stale target, out-of-range control, 예외와 자원 해제 테스트가 있는지 확인한다.
테스트가 구현 문구만 복제하는지, 실제 동작을 검증하는지 확인한다.
[SC01–SC08](../../../docs/07_single_camera_operating_envelope.md)을 해당 런타임이 구현된 범위에
대조한다. 포화/소실 구분, 누적 방지, 안쪽 복귀, 2축 조합, 재연결/미보정/dry-run을 확인한다.
미구현 항목은 계획/미실행으로 남긴다. 문서 CI를 실물 또는 추종 동작 시험의 PASS로 집계하지 않는다.
시험 영역/분모를 사후 축소하거나 거리·추가 축을 새 필수 요구로 만들지 않는다.
CI는 로컬과 같은 명령을 실행해야 한다. compile 미구현을 성공으로 위장하지 않는다.
구조화 결과에서 누락·잘못된 JSON·stale base/head·scope 누락·취소/실패한 CI는 승인 불가다.
Secret 있는 환경에서 PR 코드를 실행하는지, checkout credentials·권한 확대·main 쓰기를 확인한다.
draft에는 불필요한 반복 AI 리뷰를 시작하지 않는다. API 과금이나 다른 사람의 인증으로 전환하지 않는다.
Fixer는 PR 전체 최대 2회, 매 수정 SHA에 새로운 CI와 독립 리뷰가 필요하다.
스킬의 협업 절차를 서버에서 강제되는 승인·잠금·자동 병합 기능으로 설명하지 않는다.
승인된 기능 폐기라면 해당 기능 전용 테스트의 제거 사유를 확인하고, 유지 기능의 검사를 보존한다.
근거 있는 finding과 실행하지 못한 검사만 JSON으로 반환한다.
