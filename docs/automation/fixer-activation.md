# Phase 7 Fixer 준비안

Phase 6은 [PR #3](https://github.com/ckrhehfl/drone-vision-tracker/pull/3)에서 검증했다.
이 문서와 코드는 다음 쓰기 권한의 구체적인 준비안이며 **활성화 기록이 아니다**.
`config/automation.json`의 `auto_fix_enabled=false`, `auto_merge_enabled=false`를 유지한다.

## 권한 분리

이미 승인한 GitHub 게시 job의 `statuses: write`는 그대로 둔다.
다음 단계는 독립 로컬 Fixer의 임시 checkout 파일 수정과, 별도 로컬 publisher의
해당 PR feature branch 갱신이다. 사용자가 요청한 단계별 자동 수정 범위에서 기존 로컬
권한을 사용하며 GitHub Actions의 `contents: write`는 추가하지 않는다.
리뷰어는 계속 read-only이고 Fixer는 별도 ChatGPT 구독 세션이다. 새 API Secret은 필요 없다.

Fixer 자식은 workspace-write sandbox, network 비활성, 승인 never로 실행한다.
GitHub/API 환경변수를 전달하지 않고 commit/push·계정 정보 조회·하드웨어를 금지한다.
로컬 로그인 저장소 전체를 OS 계정 수준에서 분리한 격리는 아니다. sandbox 쓰기 실행은
Phase 6 성공 후 격리된 모의 대상에서 검증하며 새로운 계정/Secret 권한이 필요하면 요청한다.

parent publisher가 기존 로컬 Git 인증으로 HTTPS 일반 push를 수행한다.
새 GITHUB_TOKEN이나 PAT를 전달하지 않으므로 GITHUB_TOKEN push의 CI 억제를 이용하지 않는다.
현재 로컬 인증은 관리자 계정이다. 코드의 main/force 금지만으로 branch 전용 credential과
동등하다고 주장하지 않는다. 실제 활성화 전 main의 관리자 포함 PR 보호와 required checks,
일반 feature push 후 CI 재실행을 확인해야 한다. 전용 credential이 필요하면 별도로 요청한다.

## 제한과 처리 순서

1. 현재 CI/base/head/PR에 맞는 전체 JSON을 검증한다. 실제 blocking finding만 수정한다.
   사람/실물 blocker나 전달 시험용 합성 finding으로 Fixer를 시작하지 않는다.
2. `%LOCALAPPDATA%/drone-vision-tracker/automation/fix-attempts.sqlite3`에 PR당 시도를 원자적으로
   예약한다. Windows 이외에는 `~/.local/share/drone-vision-tracker/automation/`을 사용한다.
   프로세스 재시작·새 head에서 초기화하지 않으며 실패/취소에도 횟수를 돌려주지 않는다.
   데이터베이스를 삭제하거나 다른 위치로 옮겨 제한을 우회하지 않는다.
3. 독립 임시 clone에서 새 Fixer가 finding 관련 파일과 회귀 테스트만 수정한다.
4. Git HEAD/refs/config 변경, 파일 삭제, symlink/외부 경로, 기존 assertion/raises 변경,
   새 skip/xfail을 검사한다. 새 regression test는 허용한다. 이 정적 검사가 모든 의미적
   테스트 약화를 판별하는 것은 아니므로 새 독립 리뷰가 반드시 필요하다.
5. 부모 프로세스가 같은 `python -m tools.ci`를 실행한다. 실패나 PR 변경이면 push하지 않는다.
6. 부모가 수정 커밋을 만들고 해당 feature branch에 일반 push한다. main/force 경로는 없다.
7. 결과는 `CI_AND_INDEPENDENT_REVIEW_REQUIRED`다. dev-orchestrator가 새 SHA의 CI를 기다리고
   새 독립 reviewer를 실행한다. Fixer 자체는 리뷰나 병합을 하지 않는다.
8. 두 시도 후 미해결 문제는 HUMAN_DECISION_REQUIRED다. 자동 재시도·횟수 초기화는 없다.

CI/리뷰를 기다리는 전체 무인 driver, 보호 규칙, 쓰기 sandbox의 실제 수정·push 재트리거 검증은
아직 미완료다. 현재 코드를 완성된 자동 Fix 파이프라인이라고 사용하지 않는다.

## 활성화 전 확인

- 승인 범위: 사용자 요청의 임시 파일 수정 + 기존 PR feature branch의 제한된 갱신.
- main 보호: PR 필수, 관리자 우회 금지, force push/delete 금지, 현재 준비된 required checks.
- 권한이 부족해 GitHub 설정을 직접 바꿀 수 없으면 정확한 UI 설정을 HUMAN_ACTION_REQUIRED로 안내.
- 승인된 main의 설정/schema에서만 활성화. PR head의 설정 변경을 활성화 승인으로 신뢰하지 않는다.
- 작은 검증 PR에서 실제 Fix → CI → 새로운 독립 Review를 확인한 뒤 다음 단계로 진행.
- Decision Gate와 운영 Auto Merge는 Phase 8/9에서 별도로 구축·검증한다.
