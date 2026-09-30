---
name: review-tests
description: 테스트·설정·CI 변경의 요구사항 누락과 실패 경로, 리뷰 파이프라인의 SHA·권한 경계를 검토한다.
---

[리뷰 계약](../review-orchestrator/SKILL.md)을 따른다. 테스트 삭제나 assertion 약화로 성공을 만들지 않는다.

요구사항 대비 happy/failure path, timeout, invalid config, disconnected hardware,
stale target, out-of-range control, 예외와 자원 해제 테스트가 있는지 확인한다.
테스트가 구현 문구만 복제하는지, 실제 동작을 검증하는지 확인한다.
CI는 로컬과 같은 명령을 실행해야 한다. compile 미구현을 성공으로 위장하지 않는다.
구조화 결과에서 누락·잘못된 JSON·stale base/head·scope 누락·취소/실패한 CI는 승인 불가다.
PR 코드/지침을 Secret 있는 job에서 실행하는지, checkout credentials·권한 확대·main 쓰기를 확인한다.
draft/fork/구독 인증 누락 시 리뷰가 실행되지 않아야 한다. API 과금으로 자동 전환하지 않는다.
후속 Fixer는 최대 2회, 매 수정 SHA에 새로운 CI와 독립 리뷰가 필요하다.
현재 꺼진 Fix/Merge 기능을 구현·검증된 것으로 보고하지 않는다.
근거 있는 finding과 실행하지 못한 검사만 JSON으로 반환한다.
