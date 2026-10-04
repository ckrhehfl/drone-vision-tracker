# Phase 7 실제 자동 수정 검증 — 2026-10-04

결과: **PASS**. 승인된 main의 로컬 driver 한 번으로 실제 독립 finding → 별도 Fixer →
후보 sandbox CI → feature push → 새 CI → 새 독립 리뷰 → 전체 결과 게시를 완료했다.
자동 수정은 1회다. Decision Gate/Auto Merge와 드론 실물 검증 완료를 뜻하지 않는다.

## 기준과 범위

- [준비 PR #7](https://github.com/ckrhehfl/drone-vision-tracker/pull/7)의 head `9fec302`는
  [CI 37193775800](https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/37193775800)
  285 passed/1 skipped, 독립 2개 범위 PASS/지적 0을 확인했다.
  [리뷰 게시 37194120485](https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/37194120485)
  artifact 원본 일치 후 main `25f7b9076de4ac1b3af74dcf4890540417e490ef`로 통합했다.
- D23에 따라 기존 로컬 ChatGPT/Git 권한만 사용했다. 새 Secret·API 비용·Actions contents write는 없다.
- [검증 PR #8](https://github.com/ckrhehfl/drone-vision-tracker/pull/8)의 `codex/fix-loop-probe`는
  실물·ledger 예약·Git 쓰기를 하지 않는 순수 함수에 의도적인 상한 경계 오류를 넣은 시험이다.
  실제 제품에서 발견한 결함이 아니며 reviewer JSON을 만들어 AI 발견으로 표시하지 않았다.
- `python -m tools.local_pipeline --pr 8 --head 9e6fa7e028be3a14dd24d874d089e2973e3139b6`를
  깨끗한 승인 main에서 실행했다. 중간 수동 코드 수정이나 과거 PASS 재사용은 없었다.

## 실제 실행 증거

| 단계 | SHA / Actions | 관측 결과 |
|---|---|---|
| 초기 CI | `9e6fa7e` / [37194287000](https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/37194287000) | 293 passed, 1 skipped |
| 최초 독립 리뷰·게시 | `9e6fa7e` / [37194416940](https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/37194416940) | CHANGES_REQUESTED, blocking 1, failure status |
| 독립 Fixer | `9e6fa7e` → `21ed320` | 영구 시도 1회 예약, 허용된 코드 1개와 새 회귀 테스트 1개 수정 |
| 부모의 로컬 sandbox CI | 실제 수정 후보 | 294 passed, 1 skipped, 네트워크 차단 command sandbox |
| 일반 feature push 후 새 CI | `21ed320` / [37194627842](https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/37194627842) | pull_request 재실행, 최신 SHA 294 passed, 1 skipped |
| 새 독립 리뷰·게시 | `21ed320` / [37194730859](https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/37194730859) | PASS, blocking/non-blocking 0, success status |

최초 reviewer는 `may_start_fix(2)`가 세 번째 시도를 허용하는 경계 오류와 해당 테스트 누락을
실제 diff에서 보고했다. Fixer는 `<=`를 `<`로 바꾸고
`tests/test_phase7_probe_boundary.py`에 `may_start_fix(2) is False` 검사를 추가했다.
기존 `tests/test_phase7_probe.py`는 바이트 기준으로 그대로다.

새 head `21ed3202f2de62289d1f3a1ed3f5599f3f025698`의 부모는 정확히 최초 검토 head
`9e6fa7e028be3a14dd24d874d089e2973e3139b6`다. 실제 Git diff는 위 두 파일뿐이다.
main 직접 push·force push·기존 테스트 삭제/약화는 없었다.

두 리뷰의 `repository_unchanged=true`, 게시된 전체 bundle과 로컬 report/evidence의 일치,
최신 SHA의 CI와 codex-review status를 확인했다. 최종 driver 결과는
`status=PASS`, `fix_attempts=1`, `merge=DISABLED`다. 프로세스 종료 뒤 별도 조회에서도
기존 영구 ledger의 PR #8 사용 횟수는 1회이며 초기화하지 않았다.

PR #8은 검증 후 **CLOSED, mergedAt=null**로 닫았다. 검증 코드와 결함은 main에 병합하지 않았다.
전체 structured review와 실행 링크는 PR 및 Actions의 validated-review artifact에 보존했다.
로컬 상세 기록은 무시된 `artifacts/local-pipeline/`, `artifacts/auto-fix/`에 있으며
CLI 진단 로그·계정 인증 자료를 공개 저장소나 Actions에 업로드하지 않았다.

## 완료 범위와 다음 단계

제한된 소프트웨어 PR의 실제 1회 수정 경로를 검증했다. 영구 최대 2회·동시 실행 잠금·
오래된 SHA 거부·후보 파일 제한 등은 기존 회귀 테스트와 이 실제 실행 증거를 구분한다.
임의 변경의 안전성이나 모든 실패 경로를 이 한 사례로 증명한 것은 아니다.

로컬 PC와 구독 인증이 필요하며 상시 서버나 예약 감시 작업은 설치하지 않았다.
GPU smoke는 기존 opt-in 조건에 따라 건너뛰었다. 실제 카메라 FPS·검출 품질·Serial·서보·
펌웨어·추종·전원 시험은 미실행이다. v1.1 단일 카메라/2축 요구와 실물 기본 꺼짐은 유지한다.

다음 한 작업은 Phase 8 Decision Gate다. 현재 driver에는 merge 기능이 없고
`auto_merge_enabled=false`다. Phase 9 검증 전에는 자동 병합을 활성화하지 않는다.
