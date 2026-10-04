# 스킬의 Decision Gate

이 Gate는 코드·설정·PR을 바꾸지 않고 현재 증거만 판정한다.
별도 서버나 필수 GitHub status 게시기는 없다. 적용 절차는 [운영 규칙](operating-policy.md)과
[공유 스킬](development.md)을 따른다.

확인할 증거:

- PR의 현재 base/head와 draft/병합 상태.
- 정확한 head의 최신 CI 실행·attempt 및 software-checks 실제 검사 단계 성공.
- 모든 범위를 포함한 독립 리뷰 JSON과 reviewer 세션 식별, base/head 일치.
- PR 전체 수정 이력, 최대 2회 한도, 진행 중인 다른 수정 여부.
- 미해결 사람 판단, 필수 실물 시험의 관찰 방법·기대 결과·실제 증거.
- 기존 PR 보호와 미해결 대화. 보호를 우회해 병합하지 않는다.

기록 예시(placeholder는 실제 값으로 대체):

```json
{
  "reviewed_base": "<40자리 SHA>",
  "reviewed_commit": "<40자리 SHA>",
  "status": "PASS",
  "ci_run": "<URL>",
  "ci_attempt": 1,
  "review": "<전체 JSON 링크>",
  "fix_attempts": 0,
  "reasons": [],
  "unverified_items": [],
  "next_action": "MERGE_IF_REQUESTED"
}
```

status는 PASS / CHANGES_REQUESTED / HUMAN_DECISION_REQUIRED / PHYSICAL_TEST_REQUIRED다.
일반 수정은 CHANGES_REQUESTED와 FIX, 오래된·누락된 증거는 CHANGES_REQUESTED와
REFRESH_EVIDENCE로 반환한다. 진행 중 수정은 완료를 기다린다.
실제 계정 인증 등 사용자 직접 조작이 필요하면 운영 규칙의 HUMAN_ACTION_REQUIRED를 출력한다.
CI 실패·진행 중·취소·skip을 PASS로 판정하지 않는다. 새 base/head는 새 CI·독립 리뷰가 필요하다.

실물 미검증 항목마다 item, merge_blocker, observation, expected_result와 실제 증거 링크를 기록한다.
MVP 필수 실물 시험을 소프트웨어 PASS로 대신하지 않는다. 이번 변경의 merge blocker가 아닌
실물 항목은 미검증으로 계속 남기면서 소프트웨어 판단은 진행할 수 있다.
레이저는 현 MVP 밖이며 시험 분류가 사용 허가를 뜻하지 않는다.

PASS는 현재 증거에 대한 판단이다. 병합 권한이나 재사용 가능한 토큰이 아니다.
병합 담당 Codex는 실행 직전에 증거를 다시 확인한다.
