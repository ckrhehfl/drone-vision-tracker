---
name: fix-findings
description: 최신 독립 리뷰 finding만 수정하고 PR 전체 최대 2회 한도와 수정 후 CI·독립 재리뷰를 지킨다.
---

[운영 규칙](../../../docs/automation/operating-policy.md)과 [수정 기록](../../../docs/automation/development.md)을 읽는다.
Reviewer와 별도 세션에서 검증된 JSON finding을 입력으로 받는다.
reviewed_commit/base가 PR의 현재 head/base와 다르면 새 CI 증거와 독립 리뷰부터 받는다.
일반 수정은 직접 결정하며 finding 밖의 리팩터링을 넣지 않는다.
요구사항 삭제·성공 기준 하향·테스트 삭제·assertion 약화로 PASS를 만들지 않는다.

수정 전에 PR 본문과 전체 댓글의 누적 시도 기록을 읽는다.
이전 운영기 이력이 있는 PR은 보존된 기록까지 이어받으며 새 사용자/PC/SHA도 같은 PR 한도를 공유한다.
PR당 MAX_AUTO_FIX_ATTEMPTS=2. 첫 코드 변경 전에 시도 번호·시작 SHA·finding·담당 세션을 PR 댓글로 기록한다.
실패·취소도 소비한 시도다. 기록 누락/상충은 증거로 해소하며 0으로 추정하거나 자동 재시작하지 않는다.
기록이 복구되지 않아 남은 한도를 입증할 수 없으면 한도 초과와 동일하게 사람 판단으로 전환한다.
다른 Fixer의 진행 중 기록이 있으면 중복 실행하지 않는다. 종료/인계를 확인한 한 세션만 수정한다.
이 기록은 협업 절차이며 서버 잠금이나 분산 실행 보장이 아니다.

finding에 필요한 코드와 회귀 테스트를 수정하고 `python -m tools.ci`를 실행한다.
검사 오류 보완도 같은 시도에 기록하고, 관련 없는 새 작업·무제한 재시도는 하지 않는다.
feature branch에 일반 push한 뒤 새 head의 CI → 새로운 독립 reviewer 순서를 지킨다.
완료 댓글에 결과 SHA·검사 결과·새 리뷰 링크·남은 문제를 남긴다.
이전 PASS를 재사용하거나 수정자가 자신의 변경을 승인하지 않는다.
2회 뒤 blocking finding 또는 해결되지 않은 CI 실패가 남으면 HUMAN_DECISION_REQUIRED로 중단한다.
