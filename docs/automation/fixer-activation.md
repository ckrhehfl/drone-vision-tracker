> 과거 검증 기록(D26 이전). 여기에 나오는 중앙 실행기·설정·명령은 폐기했다.
> 현재는 [공유 스킬 안내](development.md)를 따른다. 아래 내용은 당시 증거로 보존한다.

# Phase 7 제한된 로컬 Fixer

Phase 6은 [PR #3](https://github.com/ckrhehfl/drone-vision-tracker/pull/3)에서 검증했다.
Phase 7의 driver와 활성화 설정은 PR #7에서 CI/독립 리뷰 후 main에 통합했다.
승인된 main의 `auto_fix_enabled=true`에서만 실행하며 PR head의 설정은 신뢰하지 않는다.
PR #8에서 실제 1회 수정·push·새 CI·새 리뷰를 확인했다. [전체 증거](fix-dry-run.md)를 따른다.
`auto_merge_enabled=false`를 유지한다. Decision Gate/Auto Merge는 후속 단계다.

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
Windows에서는 이 PC에 이미 설정된 `windows.sandbox="elevated"`를 명시한다.
`--ignore-user-config`로 이 선택이 누락되면 read-only로 내려가는 것을 실제 시험에서 확인했다.
기존 sandbox 선택을 복구한 뒤 일회용 checkout에서 지정 파일 작성/README 불변을 확인했다.
이는 실제 PR 자동 수정·push 검증이 아니며 unsandboxed fallback도 사용하지 않았다.

parent publisher가 기존 로컬 Git 인증으로 HTTPS 일반 push를 수행한다.
새 GITHUB_TOKEN이나 PAT를 전달하지 않으므로 GITHUB_TOKEN push의 CI 억제를 이용하지 않는다.
현재 로컬 인증은 관리자 계정이다. 코드의 main/force 금지만으로 branch 전용 credential과
동등하다고 주장하지 않는다. main의 관리자 포함 PR 보호와 `software-checks`/`codex-review`
필수 검사는 기존 권한으로 설정·확인했다. 실제 활성화 전 일반 feature push 후 CI 재실행도
확인해야 한다. 전용 credential이 필요하면 별도로 요청한다.

## 제한과 처리 순서

1. 현재 CI/base/head/PR에 맞는 전체 JSON을 검증한다. 실제 blocking finding만 수정한다.
   사람/실물 blocker나 전달 시험용 합성 finding으로 Fixer를 시작하지 않는다.
2. `%LOCALAPPDATA%/drone-vision-tracker/automation/fix-attempts.sqlite3`에 PR당 시도를 원자적으로
   예약한다. Windows 이외에는 `~/.local/share/drone-vision-tracker/automation/`을 사용한다.
   프로세스 재시작·새 head에서 초기화하지 않으며 실패/취소에도 횟수를 돌려주지 않는다.
   데이터베이스를 삭제하거나 다른 위치로 옮겨 제한을 우회하지 않는다.
3. PR별 OS 파일 잠금으로 동시에 두 Fixer가 시작하지 못하게 한다. 프로세스 종료 시 잠금은
   해제되지만 시도 횟수는 남는다. 독립 임시 clone에서 새 Fixer가 실제 finding의 `file`과
   새 `tests/**/test_*.py` 파일만 수정한다. 일반 Builder 작업과 달리 이 한정된 Fixer에서는
   기존 테스트 파일을 수정하지 않으며 필요한 회귀 테스트는 새 파일로 작성한다.
4. Git 메타데이터 전체(모드·hooks·index 포함)의 변경, 파일 삭제, symlink/외부 경로,
   허용 목록 밖 변경, 기존 테스트 변경, 새 skip/xfail/importorskip을 거부한다.
   명시적 속성 접근과 직접 import(별칭 포함), pytest/_pytest 와일드카드 import를 검사한다.
   이미 승인된 테스트의 skip은 그대로 보존한다. 동적 호출이나 임의 테스트 무력화를 모두
   증명하는 검사는 아니며 아래 새 독립 리뷰를 대체하지 않는다. 부모 Git의 hooks 경로는
   별도 디렉터리로 고정한다. commit 시 비어 있으며 push 전에 부모의 SHA 검증 hook만 둔다.
   이 검사만으로 모든 의미적 결함을 판별하지는 못하므로
   새 독립 리뷰가 반드시 필요하다.
5. 부모가 `python -m tools.ci`를 Codex command sandbox 안에서 실행한다. 네트워크를 막고
   알려진 Codex/GitHub 인증 파일 경로를 읽지 못하게 하며 pytest 임시 파일도 후보 안에 둔다.
   설치된 CLI 0.130.0의 deny-read 값은 최신 문서의 `deny`가 아닌 `none`이다.
   제한 시간 초과 시 프로세스 트리를 종료한다. 실패나 PR 변경이면 push하지 않는다.
6. 부모가 수정 커밋을 만들고 해당 feature branch에 일반 push한다. main/force 경로는 없다.
   신뢰된 pre-push hook이 서버가 알린 remote old SHA를 reviewed head와 비교한다.
   브랜치 rewind/삭제도 거부하고, 광고 이후 변경은 Git 서버의 old OID 비교에서 거부한다.
7. 결과는 `CI_AND_INDEPENDENT_REVIEW_REQUIRED`다. dev-orchestrator가 새 SHA의 CI를 기다리고
   새 독립 reviewer를 실행한다. Fixer 자체는 리뷰나 병합을 하지 않는다.
8. 두 시도 후 미해결 문제는 HUMAN_DECISION_REQUIRED다. 자동 재시도·횟수 초기화는 없다.

`tools.local_pipeline`이 CI 대기 → 새 리뷰 → 검증된 전체 결과 게시 → 설정이 허용한 Fixer →
새 SHA의 CI/리뷰 순서를 연결한다. 승인된 main 설정에서만 수정한다. CI 실패는 Builder가
기존 횟수 제한을 유지하며 해결한다. 제한된 실제 PR의 수정·push 재트리거 검증은 완료했다.
후속 Decision Gate/Auto Merge와 모든 실물 시험은 미완료이며 전체 자동 개발 완료로 표현하지 않는다.

독립 리뷰에서 Git hook 경계, 허용 파일 범위, 실행되지 않는 assertion 보존 문제가 제기됐다.
각각 Git 메타데이터 전체 검사/빈 hooks, finding 파일 허용 목록, 기존 테스트 파일 불변으로
보완하고 관련 실패 경로를 회귀 테스트로 추가했다. 네트워크 접근 거부와 전체 테스트의
실제 Windows command sandbox 실행도 확인했다. 최신 커밋의 재리뷰는 별도로 필요하다.

버전 근거: [CLI 0.130.0 filesystem 접근 값](https://github.com/openai/codex/blob/rust-v0.130.0/codex-rs/protocol/src/permissions.rs).

## 활성화 전 확인

- 승인 범위: 사용자 요청의 임시 파일 수정 + 기존 PR feature branch의 제한된 갱신.
- main 보호: PR 필수, 관리자 우회 금지, force push/delete 금지, 현재 준비된 required checks.
- 권한이 부족해 GitHub 설정을 직접 바꿀 수 없으면 정확한 UI 설정을 HUMAN_ACTION_REQUIRED로 안내.
- 승인된 main의 설정/schema에서만 활성화. PR head의 설정 변경을 활성화 승인으로 신뢰하지 않는다.
- 작은 검증 PR에서 실제 Fix → CI → 새로운 독립 Review를 확인한 뒤 다음 단계로 진행.
- Decision Gate와 운영 Auto Merge는 Phase 8/9에서 별도로 구축·검증한다.
