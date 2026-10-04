---
name: dev-orchestrator
description: 기능 요청을 구현·CI·독립 리뷰·최대 2회 수정으로 진행하고, 요청받은 경우 보호된 PR을 병합한다.
---

[운영 규칙](../../../docs/automation/operating-policy.md)과 [실행 안내](../../../docs/automation/development.md)를 읽는다.
README → 설계 → 결정 기록 → AGENTS 순으로 범위와 완료 조건을 확인한다.
각 사용자의 Codex와 GitHub 인증, 현재 저장소 권한을 사용한다. 소유자 PC나 별도 서버는 필요 없다.
일반 기술 선택은 직접 결정한다. 비용·권한·범위·실물 판단은 운영 규칙의 gate를 따른다.

1. Git 상태를 확인하고 사용자 변경을 보존한 feature branch에서 구현한다.
   `python -m tools.ci`로 검사한다. 실제 장치를 열지 않는다.
2. 허용된 파일만 커밋·push하고 PR을 만든다. 미완성은 draft로 유지한다.
   CI 실패는 로그와 요구사항으로 고친다. 테스트를 약화하지 않는다.
3. review 가능한 PR의 최신 CI 성공을 확인한 뒤, 작성·수정 세션과 분리된 새 세션에
   [review-orchestrator](../review-orchestrator/SKILL.md)를 맡긴다.
   PR 번호, 정확한 base/head, 실제 diff, CI URL/attempt, 승인된 요구사항을 전달한다.
   자신의 변경을 직접 PASS 처리하지 않는다.
4. 모든 scope JSON을 `tools.review merge`로 검증·통합하고 원문을 PR에 기록한다.
   blocking finding이 있으면 [fix-findings](../fix-findings/SKILL.md)로 전환한다.
   PR의 누적 수정 기록을 이어받으며 최대 2회다. 매 수정 뒤 CI와 새 독립 리뷰를 반복한다.
5. [decision-gate](../decision-gate/SKILL.md)로 현재 증거를 판정한다.
   Gate는 판단만 하고 코드를 수정하거나 병합하지 않는다.
6. 사용자 요청에 병합까지 포함되어 있고 Gate PASS이면, 직전에 GitHub의 base/head,
   최신 CI 실행·attempt와 보호 상태를 다시 확인하고 정확한 head로 일반 PR 병합을 요청한다.
   SHA 변경, 새 CI 실패/진행 중, 충돌, 미해결 대화가 있으면 해결·재검증한다.
   main 직접 push, force push, 관리자 우회, 보호 설정 변경으로 통과시키지 않는다.

새 독립 세션이 없으면 리뷰 인계 자료를 남기고 해당 세션에서 이어간다.
재시도·게시·세션 변경으로 수정 한도를 초기화하지 않는다. 증거 누락은 PASS가 아니다.
background polling, Actions AI 실행, 소유자 계정 대행, 무인 자동 병합은 이 Skill의 동작이 아니다.
기존 사용자 승인 범위에서 진행하고 운영 규칙의 짧은 결과 형식으로 보고한다.
