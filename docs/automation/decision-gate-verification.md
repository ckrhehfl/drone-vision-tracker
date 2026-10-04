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

문서만 변경하는 작은 PR에서 승인된 main의 `tools.local_pipeline`을 실행한다.
새 CI·새 독립 리뷰·전체 게시 결과 확인 후 Gate가 같은 base/head/CI attempt를
기록하고 PASS를 반환해야 한다. 실제 결과를 확인한 뒤 아래 검증 기록을 갱신한다.

거부 검사는 실제 리뷰 JSON의 로컬 복사본만 변조한다. 다른 SHA, 저장소 불변성 false,
PASS와 필수 실물 미검증의 모순, 다른 base의 과거 게시 run을 각각 거부해야 한다.
이 복사본은 실제 AI finding이나 사람 승인으로 게시하지 않는다. Gate 전후 저장소 파일·
Git refs·수정 ledger의 불변성도 비교한다. 영구 수정 횟수와 auto_merge=false를 유지한다.

실제 PASS 증거와 명시적인 음성 fixture 검증을 구분하며 완료 전에는 성공으로 기록하지 않는다.
