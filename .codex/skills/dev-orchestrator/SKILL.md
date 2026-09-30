---
name: dev-orchestrator
description: 이 저장소의 기능 요청을 구현·로컬 검사·feature branch·PR로 진행하고 승인된 자동화 단계까지 연결한다.
---

[운영 규칙](../../../docs/automation/operating-policy.md)과 [구축 상태](../../../docs/automation/setup-status.md)를 읽는다.
README → 설계 → 결정 기록 → AGENTS 순으로 요구사항과 완료 조건을 확정한다.
일반 기술 선택은 직접 결정한다. 원본 요구사항, test 기준, MVP, 하드웨어는 낮추지 않는다.

깨끗한 상태를 확인하고 main이 아닌 feature branch에서 한 목표를 구현한다.
`python -m tools.ci`를 실행하고 실패 원인을 수정한다. 실제 장치를 열지 않는다.
검증한 파일만 커밋·push하고 PR을 만든다. 미완성 PR은 draft로 유지한다.
실제 base/head SHA와 CI 결과를 별도 reviewer에 전달한다. 작성자의 요약은 증거가 아니다.
리뷰 결과는 `tools.review`로 검증하며 자신이 자신의 변경을 PASS 처리하지 않는다.
현재 단계가 허용하지 않는 외부 AI·Fixer·Merge를 시작하지 않는다.
단계 6 검증 전에는 자동 수정 권한을 활성화하지 않는다.
권한이 생겨도 main 직접 push, force push, 테스트 약화는 금지한다.
종료 시 운영 규칙의 짧은 상태 형식으로 보고한다.
