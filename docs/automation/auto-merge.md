# Phase 9 — 검증 후 조건부 병합

승인: D25. 준비 설정은 `collaborative_merge_validation`, `auto_merge_enabled=false`다.
이 문서와 병합 구현이 존재하는 것만으로 활성화된 것이 아니다.

1. 접수/게시/병합 준비 구현을 CI·새 독립 리뷰로 통합한다.
2. 작은 비하드웨어 PR에서 Automation Request → 로컬 실행기 → CI/리뷰 → 서버 Gate 게시를
   확인한다. 이때 자동 병합을 실행하지 않는다.
3. 실제 게시가 검증되면 기존 main 보호를 유지하고 `decision-gate`를 필수 검사로 추가한다.
4. 병합 직전 검증을 실제 PR에서 읽기 전용 실행하고 부정 경로 테스트를 통과시킨다.
5. 별도 활성화 PR을 새 CI/리뷰/Gate로 확인한 후 통합한다. 이후 작은 PR에서 실제 조건부 병합을
   확인하고 증거와 주기 접수 설정을 기록한다.

필수 검사는 `software-checks`, `codex-review`, `decision-gate`이며 모두 Actions app 15368에
연결한다. 최신 main 기준 strict 검사·PR 필수·관리자 적용·force/delete 금지를 유지한다.
필요한 기존 관리자 설정은 에이전트가 적용하되 새 권한이 필요할 때만 HUMAN_ACTION_REQUIRED다.

병합기는 최신 승인 main에서만 실행한다. 허용 요청자의 계정/현재 쓰기 권한, 최신 CI attempt와
정확한 PR/base/head, 전체 리뷰/게시 artifact, 필수 미검증 항목, 영구 수정 횟수를 다시 검사한다.
Gate의 full JSON artifact와 최신 status까지 대조하고 현재 PR이 clean하게 병합 가능한지 확인한다.
PASS·blocking 0·필수 미검증 0이고 세 필수 검사가 유지될 때에만 정확한 head로 squash 병합한다.
Gate는 여전히 판단만 수행한다. Gate JSON의 `merge=DISABLED`는 Gate 자신이 병합하지 않는다는
뜻이며 worker의 별도 merge 결과로 실제 병합 여부를 기록한다. 과거 JSON만으로는 병합할 수 없다.
대기 중 커밋/CI/base 변경, 누락/불일치, 권한 취소, 응답 불명확 시 자동 병합하지 않는다.
실제 병합 응답의 SHA와 PR의 merged 상태를 확인한다. 알 수 없는 결과를 재시도하지 않는다.

서버 Gate는 소유자가 제출한 수정 횟수를 전체 리뷰와 다시 계산해 게시한다.
영구 수정 ledger의 실제 값은 로컬 병합기가 다시 대조하며 협업자가 수정 횟수를 제출할 수 없다.
서버 status만으로 AI 수행을 암호학적으로 증명하는 구조는 아니다. 실제 리뷰의 read-only 증거와
완전한 artifact 대조를 함께 적용하며 동일 모델의 독립 세션 리뷰 한계를 유지한다.
카메라 FPS·검출 정확도·Serial·서보/기구·레이저 시험을 소프트웨어 PASS로 완료 처리하지 않는다.
