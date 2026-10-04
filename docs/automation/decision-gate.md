# Phase 8 — 로컬 Decision Gate

Gate는 코드를 작성하거나 수정하지 않고 AI·Fixer·하드웨어·병합을 실행하지 않는다.
승인된 main의 도구로 최신 증거를 조회해 다음 행동만 판정한다. Phase 8 구현을 독립 리뷰/CI로
통합한 뒤 실제 PR에서 확인한다. 완료 증거는 [현재 상태](setup-status.md)를 따른다.

```powershell
.venv/Scripts/python -m tools.decision_gate --directory artifacts/subscription-review/<실행> --publication-run <게시-run-ID>
```

입력은 리뷰 `result.json`/`evidence.json`과 게시 workflow run ID다. CI run/attempt·PR·base/head,
전체 diff coverage, ChatGPT 인증과 reviewer 파일 불변성, 게시 주체·workflow·artifact 원본·
최신 `codex-review` status를 다시 확인한다. 깨끗한 최신 main과 기존 main 보호도 확인한다.
Gate는 Git 객체/임시 artifact를 조회할 수 있지만 저장소 소스·설정·ledger를 수정하지 않는다.
실패/취소/skip/누락/구조 오류/과거 SHA/실행 중 증거 변경은 예외로 종료하며 PASS를 반환하지 않는다.
자동 재시도나 유료 API 전환은 없다.

| 검증된 리뷰와 수정 이력 | Gate 상태 | 다음 행동 |
|---|---|---|
| PASS, blocking 0, 필수 미검증 0 | PASS | PROCEED: 소프트웨어 판단 완료 |
| 일반 blocking finding, 수정 0~1회 | CHANGES_REQUESTED | FIX: driver가 활성화 설정 확인 후 Fixer 호출 |
| CHANGES_REQUESTED에 종류 미분류 필수 증거 존재 | CHANGES_REQUESTED | REVIEW: reviewer가 필수 항목의 성격을 명확히 분류 |
| blocking finding이 수정 2회 후 남음 | HUMAN_DECISION_REQUIRED | STOP: 운영 규칙 11번 |
| 사람이 결정해야 한다는 명시적 리뷰 | HUMAN_DECISION_REQUIRED | STOP: 운영 규칙의 12가지 사유만 요청 |
| 실물 증거가 필요하다는 명시적 리뷰 | PHYSICAL_TEST_REQUIRED | STOP: 관찰 방법과 기대 결과 전달 |

수정 한도 초과 판정은 미분류 항목의 REVIEW보다 우선한다. 2회를 이미 사용해도 새 리뷰가
PASS이면 소프트웨어 Gate는 PASS가 가능하다. 일반 기술 선택을 사람에게 되묻지 않는다.
리뷰 schema의 미검증 항목에는 종류 필드가 없으므로 자연어 키워드로 사람/실물 승인을 추측하지
않는다. 원래 리뷰의 명시적 HUMAN/PHYSICAL 상태를 보존하고 애매한 CHANGES는 REVIEW로 돌린다.
사람 판단이 필요하면 [운영 규칙](operating-policy.md)의 HUMAN_* 형식만 사용한다.

실제 FPS·검출 품질·servo 방향/jitter·Pan/Tilt limit·Serial·physical tracking·laser calibration은
모의 시험으로 완료 처리하지 않는다. 리뷰의 모든 미검증 항목과 `merge_blocker`, `observation`,
`expected_result`를 출력에 보존한다. 실물 미검증이 병합 조건인지 요구사항에서 판단하고, 필요한
사용자 승인/관찰 후 새 독립 리뷰에서 증거를 평가한다. 승인 문자열이나 우회 플래그로 지우지 않는다.
레이저는 현 MVP 밖이며 이 분류는 활성화 승인이 아니다.

driver는 게시 완료 후 Gate를 호출하고 `artifacts/local-pipeline/<실행>/decision.json`에
[schema](../../schemas/decision-gate.schema.json)를 만족하는 판정을 기록한다. 전체 review bundle의
SHA-256, base/head/CI attempt/게시 run, 영구 수정 횟수를 포함한다. hash는 원본 연결용이며
AI 실행의 암호학적 증명이 아니다. CLI 단독 실행은 JSON만 출력한다. PASS만 exit 0이다.

이 결과는 판단 시점의 기록이다. GitHub 상태가 바뀌면 재실행해야 한다. Phase 8에는 merge
명령, 서버 `decision-gate` 필수 check, 자동 병합 활성화가 없다. Phase 9에서 서버 게시와
병합 직전 증거 재검증을 구축하기 전에는 이 JSON을 병합 허가로 사용하지 않는다.
현 단계에 새로운 Secret·권한·GitHub UI 설정·API 비용은 없다.

Phase 9의 별도 서버 게시·조건부 병합 준비는 [병합 계약](auto-merge.md)을 따른다.
위 내용은 Phase 8 Gate 자체의 역할이다. Gate는 Phase 9에서도 병합을 실행하지 않으며,
별도 병합기가 모든 최신 증거·설정·보호 조건을 다시 검증해야 한다.
