# Phase 8 실제 PR 검증 기록

이 기록은 자동화 소프트웨어 검증이며 카메라·AI 정확도·Serial·모터 시험이 아니다.
Auto Merge와 GitHub 서버 `decision-gate` 필수 check는 아직 비활성이다.

## 승인된 구현

[PR #10](https://github.com/ckrhehfl/drone-vision-tracker/pull/10)의 head
`166fd7a04495a30d2971e2f445996cab04df6a7e`에서 로컬 및
[CI 37196745152](https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/37196745152)
317 passed/1 skipped, 독립 리뷰 2개 범위 PASS/지적 0을 확인했다.
[게시 37197088719](https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/37197088719)의
전체 artifact가 로컬 JSON과 일치하는지 대조하고 main `67b6d163e01066459300f61a9a105fc527b69dcf`로
통합했다. opt-in GPU smoke는 미실행이며 기존 테스트를 삭제하거나 약화하지 않았다.

## 실제 연결 검증

문서만 변경하는 [PR #11](https://github.com/ckrhehfl/drone-vision-tracker/pull/11)에서
승인된 main의 `tools.local_pipeline`을 실행했다. 아래는 **최초 검증 head**의 기록이다.
이 기록을 추가하는 새 head도 CI·새 독립 리뷰·게시·Gate를 다시 거치며 최신 결과는 PR 본문에 남긴다.

| 증거 | 실제 결과 |
|---|---|
| base | `67b6d163e01066459300f61a9a105fc527b69dcf` |
| 최초 검증 head | `585f57ef36c0e11cdad3e094b48ae7fcc84cf765` |
| CI | [37197250645](https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/37197250645), attempt 1, PASS |
| 새 독립 리뷰 | PASS, blocking 0 / non-blocking 0, reviewer 저장소 불변 |
| 전체 JSON 게시 | [37197391835](https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/37197391835), 원본 대조 PASS |
| Gate | PASS / PROCEED, 위 base/head/CI attempt/게시 run 일치 |
| 자동 수정 | 0회, PR당 최대 2회 유지 |
| 병합 | driver/Gate 결과 `DISABLED`, merge 실행 없음 |

리뷰 bundle SHA-256은 `19ea1f6bef8d7ba9aa1cc5181f92bd1e4b3b0fea2eb432faed1949018d8e18c0`다.
로컬 `artifacts/local-pipeline/pr-11-rj84h6de/`에 review, 전체 게시 bundle, decision/result를
보존했다. `artifacts/phase8-gate-audit.json`은 Gate를 재실행하고 아래 거부 검사를 기록한다.
이 로컬 artifacts는 Git에 올리지 않으며 전체 판정/관찰 항목을 PR 본문에도 기록한다.

| 명시적으로 변조한 로컬 복사본 | 결과 |
|---|---|
| reviewed_commit이 현재 head와 다름 | REJECTED: 다른 base/head 리뷰 |
| repository_unchanged=false | REJECTED: 불변성 증거 부적합 |
| PASS인데 merge_blocker인 실물 FPS 미검증을 추가 | REJECTED: PASS와 필수 증거 모순 |
| 과거 PR #9 게시 run 37195293419 재사용 | REJECTED: 현재 base의 게시 증거가 아님 |

복사본을 실제 AI finding이나 사람 승인으로 게시하지 않았다. Gate 전후 tracked 파일·
작업 상태·Git refs fingerprint와 ledger 파일 SHA-256이 동일했다. 사람/실물 상태, 2회 한도,
CI/증거 변경 중단, Gate 실패 시 Fixer 미실행은 CI의 명시적인 모의 테스트로도 확인한다.
이 테스트를 실제 사람 승인 또는 실물 시험 성공으로 표시하지 않는다.

Phase 8 로컬 판정 검증은 완료했다. 다음 작업은 Phase 9 서버 Gate 게시/필수 check와
병합 직전 최신 증거 확인을 구축·검증하는 것이다. 현재 Auto Merge는 비활성이고 신규 Secret·
계정 권한·API 비용은 없다. 실제 FPS·정확도·Serial·서보·펌웨어와 GPU smoke는 미실행이다.
