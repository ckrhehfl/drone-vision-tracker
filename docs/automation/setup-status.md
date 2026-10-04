# 자동 개발 시스템 구축 상태

갱신일: 2026-10-04. 아래 이전 날짜의 기록은 당시 상태이며 최신 상태는 이 요약을 따른다.

현재: Phase 6·7·8의 제한된 실제 PR 검증 완료. [Phase 7 증거](fix-dry-run.md)와
[Phase 8 증거](decision-gate-verification.md)를 따른다.
PR #5와 v1.1 PR #6은 CI/독립 리뷰 PASS 후 병합했다. 원본 v1.1 폴더는 백업 대조 후 삭제했다.
PR #4도 승인된 추가 1회 보완, CI 250 passed/1 skipped, 독립 리뷰 3개 범위 PASS 후 병합했다.
PR #7은 driver/활성화 준비를 검증해 병합했다. PR #8은 실제 Fixer 1회 → 새 CI → 새 리뷰 PASS를
확인한 뒤 병합 없이 닫았다. 운영 최대 2회와 승인된 main에서만 실행하는 경계를 유지한다.
현재 auto_fix_enabled=true, auto_merge_enabled=false다. Phase 8 로컬 Decision Gate 구현을
PR #10의 CI/독립 리뷰 PASS 후 통합했다. [Gate 계약](decision-gate.md)과
[실제 PR 검증 기록](decision-gate-verification.md)을 따른다. PR #11 최초 검증 head에서
CI → 독립 리뷰 → 전체 게시 대조 → Gate PASS와 변조된 증거 4종 거부, 저장소/ledger 불변을 확인했다.
현재 문서 head의 최신 결과는 PR #11에 연결한다.

Phase 9 준비: 사용자 A 승인(D25)으로 현재 협업자 3명과 소유자의 요청을 소유자 PC 한 대에서
처리하는 접수/영구 중복 방지, 서버 Gate 게시, 별도 조건부 병합기를 구현한다.
[협업자 안내](collaborators.md)와 [검증 순서](auto-merge.md)를 따른다.
운영 Auto Merge와 주기 접수 실행은 아직 꺼져 있다. 서버 필수 검사는 기존 CI/리뷰 2개이며,
실제 Gate 게시 확인 후 3개로 늘린다. 실제 협업자 계정에서의 UI 실행은 아직 확인하지 않았다.

## Phase 1 감사

| 항목 | 확인 결과 |
|---|---|
| 시작 Git 상태 | `main`, `46da1c8`, `origin/main`과 일치, 미커밋 변경 없음 |
| 원격 | `https://github.com/ckrhehfl/drone-vision-tracker.git`, 공개 저장소 |
| 작업 브랜치 | `automation/ci-review-foundation` |
| 구조 | 설계·규격·예제 설정·기록 양식·프롬프트, 총 34개 파일 |
| Python 코드 | 문서 검사 `tools/check_design_package.py`만 존재 |
| 기능 / 펌웨어 | 비전·제어·Serial 런타임 및 Arduino 소스 없음 |
| 기존 테스트 | 문서 검사만 존재, pytest 테스트 없음 |
| 기존 CI | `.github`에는 Issue/PR 템플릿만 존재, workflow 없음 |
| OS / Python | Windows build 26200 / CPython 3.11.0 |
| CLI | Git 2.52.0.windows.1, gh 2.92.0, Codex CLI 0.130.0 |
| 없는 도구 | Ruff·pytest(초기 환경), arduino-cli, uv |
| GitHub 인증 | 현재 계정 저장소 ADMIN 권한 확인, 인증 값은 수집하지 않음 |
| Actions | 활성화됨, 기본 GITHUB_TOKEN read, PR 승인 권한 꺼짐 |
| Secrets / Variables | 이름 목록 기준 모두 없음. Secret 값 조회 안 함 |
| Rulesets | 없음. 아직 main 직접 push를 서버에서 차단하지 않음 |

README → 시스템 설계 → 결정 기록 → AGENTS → 통신·데이터·구현 계획을 확인했다.
MVP는 실내 단일 지정 드론의 카메라 추종이며 레이저·요격·자동 수색은 제외한다.
현재 예제는 hardware/serial 비활성, 보정 미완료다. 이 안전 기본값을 유지한다.

## 확정한 단계와 완료 조건

1. 감사 기록. 기존 Markdown/DOCX 설계 원본 보존.
2. 독립 `.venv`, 검증한 의존성 버전 고정, 로컬/CI 공통 `python -m tools.ci`.
   Ruff, syntax/import, pytest, 설정 검증, 기존 문서 검사 포함.
   Arduino 소스가 새로 생기면 compile check 미설정을 성공으로 숨기지 않는다.
3. 요청한 `.codex/skills`의 7개 Skill, 리뷰 분할·JSON Schema·SHA 검사·통합 도구.
4. GitHub CI와 로컬 읽기 전용 구독 리뷰. 최신 PR head/CI/작성자 권한을 확인하고
   base의 신뢰된 지침만 실행한다. draft/fork/인증 누락에서는 AI를 실행하지 않는다.
5. 기존 ChatGPT 로그인 확인. 사용자 선택에 따라 API 비용/Secret 등록 절차는 제외한다.
6. 작은 PR에서 실제 구독 리뷰·JSON·최신 SHA·파일 불변성과 Actions 결과 전달을 검증한다.
7. 6단계 성공 후에만 Fixer 권한을 논의하고 최대 2회 수정 → CI → 새 리뷰를 구현한다.
8. Decision Gate는 판단만 수행한다. 실물 관찰과 미검증 항목을 명시한다.
9. 모든 증거가 최신 SHA에 묶이는 것을 검증한 뒤 마지막으로 branch rule/auto merge를 활성화한다.

## 현재 경계

유료 API, 자동 Merge는 비활성이다. 로컬 구독 리뷰와 Actions 결과 게시를 검증했다.
자동 Fix는 승인된 main에서만 최대 2회 실행한다. Phase 8 설정은 `local_decision_gate_validation`이다.
기존 ChatGPT 구독 사용은 사용자 선택이며 새 비용·Secret·권한 승인을 추정하지 않는다.
기존 문서의 수동 병합 원칙은 이번 사용자 요청에 따라 **9단계 검증 완료 후에만**
조건부 자동 병합으로 확장한다. 하드웨어와 MVP 요구사항은 바뀌지 않는다.
원래 `PACKAGE_SHA256.json`은 최초 v1.0 배포 스냅샷의 해시이며 현재 브랜치 manifest가 아니다.
현재 개발 변경에 맞추어 과거 기록을 재생성하지 않는다.

## 실행 결과

- Phase 1 감사 완료, Phase 2 로컬 검증 기반 구현, Phase 3 Skill/구조화 리뷰 도구 구현.
- Phase 4 read-only workflow 작성·정적 검사 완료. 외부 Codex 실행 검증은 아직 아니다.
- Windows Python 3.11.0에서 `python -m tools.ci`: 112 tests PASS,
  Ruff lint/format, syntax/import, 문서·예제 설정 검사 PASS.
- 7개 Skill 원본과 7개 탐색 진입점: bundled quick_validate PASS (Windows UTF-8 모드).
- actionlint 1.7.7 정적 검사 PASS. release checksum 검증 후 artifacts 아래에서만 사용.
  shellcheck/pyflakes는 이 별도 검사에 미사용이며 Python은 Ruff/pytest로 검사했다.
- 기반 [draft PR #1](https://github.com/ckrhehfl/drone-vision-tracker/pull/1)을 생성했다.
  첫 [CI run](https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/36727001553)은
  GitHub에서 success로 표시되었으나 조회된 job step/log에 테스트 완료 증거가 빠져 있었다.
  이를 원격 81개 테스트 통과의 증거로 간주하지 않는다. 최신 실행 증거는 PR에서 확인한다.
- 독립 read-only reviewer가 `969e345` 전체 diff와 로컬 81개 테스트를 확인했다.
  P2 1개(같은 SHA의 과거 성공 run 재사용), P3 1개(CI FAIL 집계 우선순위)를 제기했다.
  Builder가 최신 run/attempt·필수 job/step 검사와 FAIL 우선 집계로 수정하고 회귀 테스트를 추가했다.
  최신 commit의 재검토 결과는 PR 설명에서 SHA와 함께 관리한다. 이 문서는 승인 토큰이 아니다.
- 새 독립 reviewer가 `4bac353`을 재검토하여 과거 run ID의 나중 attempt도 고려해야 한다는
  P2 1개를 확인했다. Builder의 두 번째 수정은 각 run 최신 attempt의 시작 시각을 비교하고,
  불완전한 실행 이력·시각 누락·동률에서 승인하지 않는다. 진행/실패/취소/skip 회귀 테스트 포함.
- `4bac353`의 [GitHub CI 로그](https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/36727871329)에서
  103 passed를 확인했다. job API의 steps 빈 목록은 별도 미검증 제한으로 남기며,
  리뷰 preflight는 필수 step 증거를 얻지 못하면 중단한다.
- 리뷰 scope는 실제 논리 영역으로 묶어 현재 기반 변경을 3개 영역으로 분할한다.
  작은 문서 하나마다 독립 API 실행을 만들지 않는다.
- Arduino compile: 소스 없음으로 SKIP. 카메라/AI 품질/Serial/서보/펌웨어 업로드/레이저 미실행.
- 이전 API 비용/Secret gate는 2026-09-30 사용자의 구독 방식 선택으로 철회했다.
- 설치된 Auto Fixer 실행 0회. 기반 구축 중 Builder 리뷰 수정 2회.
  Auto Merge 비활성, main 직접 push/권한 변경 없음.

## 구독 방식 전환 — 2026-09-30

- `codex login status`: Logged in using ChatGPT. 새 API Secret 불필요.
- CI는 공개 저장소의 표준 Ubuntu runner를 유지한다. 별도 AI API 요금과 runner 실행 시간을 구분한다.
- API를 호출하던 workflow를 제거하고 미구축 상태를 실패로 알리는 read-only 대기 workflow로 교체했다.
- `tools.subscription_review`는 로컬 구독 인증·새 reviewer 세션·JSON Schema·최신 CI/SHA·clone 불변성을
  검증한다. 인증 파일을 CI에 전달하지 않으며 유료 fallback은 없다.
- 로컬 Python 검사 122 tests PASS. Windows read-only sandbox의 명령 읽기 실행 확인.
- `c5183f8`에 대해 실제 ChatGPT 구독 리뷰 3개 scope를 실행했다. JSON Schema·최신 CI/SHA·clone 불변성
  검증 후 CHANGES_REQUESTED를 반환했다. P3를 blocking에 넣을 수 있는 결함 1개를 찾아
  schema의 허용 severity를 제한하고 blocking 거부/non-blocking 보존 회귀 테스트를 추가했다.
  수정 후 최신 SHA의 CI·독립 재리뷰 증거는 [PR #1](https://github.com/ckrhehfl/drone-vision-tracker/pull/1)에 기록한다.
- Actions 결과 전달과 작은 테스트 PR의 전체 dry-run은 아직 미검증이다. Phase 6 완료가 아니다.
- 2026-10-01 사용자가 별도 결과 게시 job의 `statuses: write`를 승인했다.
- PC 가동이 로컬 리뷰의 전제다. GitHub Actions CI는 PC 없이 실행된다.

## 결과 게시 연결 — 2026-10-01

- PR #1의 `cb16cd7` CI 122 tests 및 독립 3개 scope PASS를 재확인하고 초기 통합했다.
  main 통합 커밋은 `56794fb`다. 운영 Auto Merge 활성화와는 별개인 bootstrap이다.
- 결과 게시 workflow를 별도 PR에서 구현한다. 검증 job은 read, 게시 job만 statuses write다.
- 모든 JSON과 증거를 기본 브랜치 코드로 검증한다. 소유자의 main dispatch만 허용하며,
  CLI 진단 로그·구독 인증 파일·head 코드 실행은 전달하지 않는다.
- 작은 PR을 이용한 실제 Actions 전달/성공/실패/새 SHA 검증 후 Phase 6 완료를 기록한다.

## Phase 6 실제 검증 완료 — 2026-10-01

결과 게시 [PR #2](https://github.com/ckrhehfl/drone-vision-tracker/pull/2)는 CI 134 tests,
독립 2개 scope PASS/blocking 0 확인 후 `35e23af`로 통합했다. 승인 내용이 reviewer에
누락된 것과 잘못된 문서 행 지적은 실제 사용자 승인/파일을 제공한 독립 재검토에서 해소했다.
이 과정에서 이미 허용한 statuses 권한을 사용자에게 다시 요청하지 않았다.

작은 문서 [PR #3](https://github.com/ckrhehfl/drone-vision-tracker/pull/3)로 다음을 확인했다.

| 시험 | SHA / Actions run | 결과 |
|---|---|---|
| 최초 CI | `e5c27e6` / [36746109069](https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/36746109069) | 134 tests PASS |
| 실제 구독 리뷰 게시 | `e5c27e6` / [36746369366](https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/36746369366) | PASS, blocker 0, 전체 artifact 원본 일치 |
| 합성 실패 fixture | `e5c27e6` / [36746512500](https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/36746512500) | CHANGES_REQUESTED, blocker 1, 실패 status, 전체 finding 보존 |
| 새 head에 이전 JSON 제출 | `ac5c31c` / [36746720558](https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/36746720558) | 검증 실패, 게시 skip, 새 head status 없음 |
| 최신 CI | `ac5c31c` / [36746672087](https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/36746672087) | 134 tests PASS |
| 새 독립 리뷰 게시 | `ac5c31c` / [36747003424](https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/36747003424) | PASS, blocker 0, 전체 artifact 원본 일치 |

`ac5c31cd985e334f934ca4684520c1b71192dc4b`의 실제 리뷰는 새 세션이며 clone 불변성 증거를
검증했다. 합성 실패 finding은 AI가 찾은 제품 결함이 아니다. 실제 AI finding/수정/재리뷰는
PR #1에 별도로 기록돼 있다. PR #3은 검증 후 `b9913e6`으로 bootstrap 통합했다.
이 통합은 운영 자동 병합을 활성화한 것이 아니다. 실물 시험은 모두 미실행이다.

## Phase 7 준비 — 비활성

`tools.auto_fix`와 `tools.fix_attempts`에 최신 finding 검사, PR별 영구 최대 2회 예약,
별도 수정 세션, 후보 변경 검사, 로컬 CI, feature branch publisher 준비 코드를 추가한다.
`execute`는 활성화 설정 전에 프로세스·네트워크·시도 예약을 시작하지 않는다.
[정확한 권한 범위와 미완료 사항](fixer-activation.md)을 따른다. 실제 자동 수정 0회다.
GitHub Actions contents write, 새로운 Secret, Auto Merge는 변경하지 않았다.
Windows 쓰기 sandbox의 일회용 파일 작성 시험은 PASS다. 기존 Windows sandbox 선택을
명시해야 하며 전체 사용자 설정을 다시 로드하거나 sandbox를 우회하지 않는다.
기존 관리자 권한으로 main 보호를 설정하고 API로 재확인했다. 관리자 포함 PR 필수,
최신 `software-checks`/`codex-review` 필수(출처 GitHub Actions app 15368), force/delete 금지다.
사람의 코드 승인 수는 0이며 소스 리뷰를 사용자에게 요구하지 않는다.
Decision Gate check는 Phase 8 검증 후 추가한다. 현재 운영 Auto Merge는 꺼져 있다.
준비 코드의 첫 유효 독립 리뷰에서 blocking 3개(Git hook 권한 경계, finding 파일 범위,
실행되지 않는 assertion)가 확인됐다. Builder가 해당 경계와 회귀 테스트를 보완한다.
전체 167개 테스트의 Windows command sandbox 실행과 외부 연결 거부를 실제 확인했다.
이 검사는 실제 PR 자동 수정·push 재트리거 또는 최신 SHA 리뷰 PASS를 대신하지 않는다.
재리뷰의 원격 branch rewind 경계 finding은 부모가 생성한 pre-push SHA 검사로 보완한다.
실제 로컬 bare remote 시험에서 rewind/삭제/다른 branch 거부와 정확한 이전 SHA 갱신을 확인한다.
force push 또는 force-with-lease는 사용하지 않는다.

## 2026-10-04 확인과 PR 처리 순서

- 최초 확인 main: `b9913e691b5c460336a48200828deb54a0ad18d5`.
  PR #5 통합 후 문서 변경의 base는 `0b28a930fef89714cb0779ba24d4a035de713235`다.
- Phase 6: [PR #3](https://github.com/ckrhehfl/drone-vision-tracker/pull/3)의 실제 구독 PASS 게시
  [36746369366](https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/36746369366),
  명시적 합성 실패 [36746512500](https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/36746512500),
  stale SHA 거부 [36746720558](https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/36746720558),
  새 SHA PASS [36747003424](https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/36747003424) 검증 완료.
- [PR #4](https://github.com/ckrhehfl/drone-vision-tracker/pull/4), head `46727fa`:
  CI 170 tests PASS, 독립 리뷰 CHANGES_REQUESTED/blocking 1. 직접 import한 pytest skip/xfail을
  후보 검사에서 놓치는 지적이 남았다. 두 차례 Builder 보완 이후 중단했다. 설치된 Fixer는
  실행 0회지만 구축 보완에도 같은 2회 중단 기준을 적용했다. 별도 승인 없이 세 번째 보완을 하지 않는다.
- [PR #5](https://github.com/ckrhehfl/drone-vision-tracker/pull/5): 원래 `14ed93f`의 신규 리뷰에서
  원본 촬영 출처/시각/검수 해시 누락을 찾아 1회 보완했다. 새 head `4da3f26`은 로컬 및
  [CI 37189467772](https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/37189467772)
  181 passed/1 skipped, 독립 2개 scope PASS/blocking 0이다. 전체 JSON과
  [게시 artifact 37189830651](https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/37189830651)
  일치를 확인하고 `0b28a93`으로 squash 병합했다. 이번 GPU smoke는 미실행이며 기존 GPU 기록과 구분한다.
- 순서: 학습 PR 통합 뒤 [v1.1 PR #6](https://github.com/ckrhehfl/drone-vision-tracker/pull/6)에
  최신 main을 일반 merge하여 기존 학습 문서/코드를 보존한다. 새로운 base/head의 CI와 독립 리뷰를
  확인한 뒤 병합한다. 이전 base의 리뷰를 재사용하지 않으며 운영 Auto Merge 활성화와는 별개다.
  PR #4는 별도 한도 결정을 기다리며 실패 status를 우회하거나 자동화 설정을 활성화하지 않는다.
- 미게시 `automation/local-development-loop`의 `1d4662f`는 별도 작업 폴더에 보존했다.
  main에 통합하지 않았고 활성화 설정도 실행하지 않았다. v1.1로 초기 ZIP 상태를 덮어쓰지 않는다.
- main 보호를 API로 재확인했다: 관리자 포함 PR 필수, strict software-checks/codex-review,
  force/delete 금지. 이번 문서 반영에서 새 Secret·권한·유료 API·GitHub 설정을 추가하지 않는다.
- 실물 카메라·정확도·Serial·모터·펌웨어·레이저 미실행. SC01–SC08은 관련 런타임 구현 전 계획이다.

## 2026-10-04 PR #4 추가 1회 승인 — D22

사용자가 HUMAN_DECISION_REQUIRED의 A를 선택했다. 이 준비 PR의 남은 skip/xfail 검사
finding에 한해 Builder 보완을 1회 더 수행하고 새로운 CI와 독립 리뷰를 받는다.
이전 두 회를 초기화하지 않으며 같은 PR에 자동으로 네 번째 보완을 시작하지 않는다.
설치된 Fixer의 실제 실행은 여전히 0회이고, 운영 MAX_AUTO_FIX_ATTEMPTS=2는 바꾸지 않는다.

최신 main `464e15b`를 일반 merge해 PR #5 학습 도구와 v1.1을 보존한다. 명시적인
pytest skip/xfail 직접 import·별칭·와일드카드 및 importorskip를 후보 검사에서 거부하고,
staged/untracked 새 테스트의 실패 경로와 기존 승인 skip 테스트 불변을 회귀 검사한다.
동적인 모든 테스트 실행 경로를 정적 검사로 증명하지는 않으며 새 독립 리뷰가 계속 필요하다.
이번 head의 CI·리뷰·병합 결과는 [PR #4](https://github.com/ckrhehfl/drone-vision-tracker/pull/4)에
SHA와 함께 게시한다. 이전 170 tests 결과를 새 통합 head의 증거로 사용하지 않는다.

## Phase 7 단계 연결과 제한된 활성화

`tools.local_pipeline`에 CI 대기, 새 독립 리뷰, 전체 JSON 게시/다운로드 대조,
설정이 허용한 Fixer 및 새 SHA 재검증 순서를 연결한다. auto_fix만 true로 전환할 준비다.
이 변경의 CI·독립 리뷰를 확인하고 main에 통합한 뒤에만 활성화된다. auto_merge는 계속 false다.
프로세스 재개 시에도 PR별 ledger 한도를 유지하고 CI 실패는 Builder에 넘긴다.
현재는 모의 state-machine 검증 단계이며 실제 PR 자동 수정 검증 완료를 뜻하지 않는다.

2026-10-04: PR #4 head `07acc82`의 [CI 37191906899](https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/37191906899)
250 passed/1 skipped, 독립 3개 범위 PASS/지적 0, [게시 37192343330](https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/37192343330)
artifact 원본 일치를 확인하고 main `f37bdd2`로 병합했다. 사용자가 다음 단계 진행을 요청했다.
기존 미게시 `1d4662f`는 보존하고 driver 변경만 최신 main 기반 `codex/local-development-loop`로
가져왔다. PR #4의 모든 검사 보완, PR #5 학습 도구와 v1.1을 유지한다.
새 driver PR을 CI/독립 리뷰로 통합한 뒤 실물과 무관한 검증 PR에서 실제 reviewer finding →
Fix → 새 SHA CI → 새 독립 리뷰를 시험한다. 의도적으로 넣은 검증용 결함은 실제 제품 결함과
구분하며 JSON finding을 사람이 만들어 실제 AI 검출로 표시하지 않는다. 검증 PR은 완료 후
닫고, 하드웨어 기능이나 검증용 결함을 main에 병합하지 않는다.

## Phase 7 실제 검증 완료 — 2026-10-04

준비 [PR #7](https://github.com/ckrhehfl/drone-vision-tracker/pull/7)을 main `25f7b90`으로
통합한 뒤 [PR #8](https://github.com/ckrhehfl/drone-vision-tracker/pull/8)의 의도적인 비하드웨어
시험 결함을 실제 독립 reviewer가 발견했다. driver가 결과 게시/원본 대조를 확인하고 별도
Fixer를 1회 실행했다. 허용된 코드와 새 테스트만 바꾸었으며 기존 테스트는 보존했다.
새 head `21ed320`의 sandbox 로컬 검사와 GitHub CI는 294 passed/1 skipped,
새 독립 리뷰/게시 결과는 PASS/지적 0이었다. 사용 횟수 1회는 영구 ledger에 남아 있다.
PR #8은 닫았고 검증 코드는 main에 병합하지 않았다. [전체 증거와 한계](fix-dry-run.md)를 참조한다.
이 완료는 Phase 8/9나 드론 실물 기능 완료가 아니다. 새 Secret·권한·API 비용은 없다.
