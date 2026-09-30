---
name: fix-findings
description: 검증된 최신 커밋의 reviewer finding만 수정하며 최대 2회 한도와 CI·독립 재리뷰를 지킨다.
---

[운영 규칙](../../../docs/automation/operating-policy.md)을 읽는다.
현재 Phase 6 dry-run 성공 증거와 Fixer 권한이 승인·활성화되기 전에는 실행하지 않는다.
reviewer와 별도 세션에서 시작한다. 자유 문장 대신 검증된 JSON finding을 입력으로 받는다.
reviewed_commit/base가 PR의 현재 head/base와 다르면 수정하지 않고 새 CI/리뷰를 요청한다.
정상적인 코드·테스트 수정은 직접 결정한다. finding과 관련 없는 리팩터링은 넣지 않는다.
요구사항 삭제·성공 기준 하향·테스트 삭제·assertion 약화로 PASS를 만들지 않는다.

PR 단위의 지속 기록에 총 수정 횟수를 저장한다. 프로세스 재시작이나 head 변경으로 초기화하지 않는다.
MAX_AUTO_FIX_ATTEMPTS=2. 수정 커밋을 만들기 전에 횟수를 소비하며 오류/취소로 횟수를 돌려주지 않는다.
매 수정 후 로컬 검사 → feature branch push → 새 SHA의 CI → 새 독립 리뷰를 수행한다.
main 쓰기·force push·이전 PASS 재사용을 금지한다.
2회 후에도 blocking finding이 있으면 HUMAN_DECISION_REQUIRED로 중단한다.
횟수 기록 저장소·branch 전용 credential·재트리거 방식을 구축하기 전에는 자동 Fix가 완성되었다고 쓰지 않는다.
