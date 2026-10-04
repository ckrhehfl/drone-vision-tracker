# Phase 9 실제 PR 검증

Phase 6/7/8 증거는 기존 기록을 보존한다. 이 문서는 Phase 9의 실제 관측만 기록한다.

## 접수 및 Gate 검증

작은 문서 변경 PR에서 Automation Request의 main 실행 → 소유자 PC의 한 번 처리 → 최신 CI →
독립 읽기 전용 리뷰 → 전체 리뷰 게시 → 서버 Decision Gate 게시 순서를 검증한다.
이 단계에서 auto_merge_enabled=false를 유지하며 병합은 실행하지 않는다.
다른 협업자 세 명의 직접 UI 실행을 수행했다고 주장하지 않는다.
동일 접수 경로의 소유자 실행과 계정/권한 경계 테스트를 구분한다.

2026-10-04 실제 결과:

- 준비 [PR #12](https://github.com/ckrhehfl/drone-vision-tracker/pull/12):
  head `17b2568cc767208eb0e043e92f6c9aaf661af10c`, CI `37202038360` 385 passed/1 skipped,
  독립 3개 scope PASS/blocking 0, 게시 `37202771434`. 수정 0회로 보호된 squash 병합.
- 검증 [PR #13](https://github.com/ckrhehfl/drone-vision-tracker/pull/13):
  base `41fb5d4fd8d8175ad256f5ed9d507841de6ce3c2`,
  head `d2861b3716c633ebc825bbb8fc7d5071d5073ca7`.
  실제 접수 `37202898904`, CI `37202890249` PASS,
  새 독립 리뷰 PASS/blocking 0, 전체 리뷰 게시 `37203027250`, 서버 Gate `37203129841` PASS.
  원본 artifact와 status를 대조했고 수정 0회, worker의 merge는 DISABLED였다.
- 실제 허용 4계정의 현재 쓰기 권한을 조회했다. 미등록 identity 거부와 같은 run의
  ALREADY_PROCESSED 반환(추가 리뷰 없음)을 확인했다.
- 서버 Gate를 필수 check로 추가하기 전 실제 병합 준비 검사는 거부됐다.
  기존 보호의 다른 필드를 보존하며 `decision-gate`를 추가한 후 같은 증거의 읽기 전용
  준비 검사는 PASS였다. 세 필수 검사 모두 Actions app 15368, strict/admin 적용을 확인했다.
- 준비 상태에서 실제 merge 함수는 `Automatic merge is disabled in approved main`으로
  병합 전에 거부했다. 이후 PR #13을 세 필수 검사와 정확한 SHA로 일반 squash 병합했다.
  이 병합은 자동 병합 검증으로 세지 않는다.
- 로컬 전체 증거: `artifacts/phase9-request-audit.json`,
  `artifacts/phase9-protection-before.json`, `artifacts/phase9-protection-after.json`,
  `artifacts/local-pipeline/pr-13-g1c1fgtd/`. 전체 Gate JSON도 PR #13 본문에 보존한다.

## 병합 검증

실제 Gate 게시가 검증된 뒤 서버 필수 검사를 세 개로 맞추고 정확한 최신 SHA에서
읽기 전용 병합 준비 검사를 실행한다. 별도 활성화 PR의 CI/리뷰/Gate 통과 후에만 실제
조건부 병합 검증 PR을 처리한다. 모든 결과는 실물 카메라·검출·통신·서보 시험과 별개다.
