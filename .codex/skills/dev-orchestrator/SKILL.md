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
승인된 깨끗한 main checkout에서 `python -m tools.local_pipeline --pr <번호> --head <SHA>`로
최신 CI 대기 → 독립 리뷰 → 전체 결과 게시를 연결한다. 설정이 허용한 때만 Fixer를 호출한다.
게시 후 Decision Gate가 현재 증거를 재검증한다. `next_action=REVIEW`면 필수 미검증 항목을
독립 reviewer가 명확히 분류하도록 하고, FIX/STOP/PASS 판정을 자의적으로 바꾸지 않는다.
Phase 8의 PASS는 병합 허가가 아니다. Auto Merge는 Phase 9 검증 전까지 꺼 둔다.
협업자 요청은 [접수 계약](../../../docs/automation/collaborators.md)을 따른다.
허용된 현재 계정만 요청하고 소유자 PC의 승인된 최신 main에서 `tools.automation_worker`가 처리한다.
요청 이력·PR당 수정 이력을 초기화하지 않는다. 실제 활성화 상태는 main 설정/구축 상태를 확인한다.
`BUILDER_CI_FIX_REQUIRED`는 사람 결정이 아니다. CI 로그로 일반 오류를 수정하고 새 SHA로
다시 실행한다. 이미 Fixer 시도를 사용한 PR에서는 이 경로로 2회 한도를 우회하지 않는다.
timeout/인증/게시 오류는 PASS가 아니다. 기술 오류를 해결한 뒤 현재 SHA의 증거로 재개한다.
진행 중 외부 변경으로 head/base가 바뀌면 이전 결과를 재사용하지 않는다.
권한이 생겨도 main 직접 push, force push, 테스트 약화는 금지한다.
종료 시 운영 규칙의 짧은 상태 형식으로 보고한다.
