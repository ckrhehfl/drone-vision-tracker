# 자동 개발 시스템 구축 상태

기준일: 2026-09-30. 이번 작업은 자동화 기반 구축이며 드론 기능 구현이 아니다.

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

유료 API, 무인 리뷰 연결, 자동 Fix, 자동 Merge는 비활성이다. 로컬 구독 리뷰 검증을 준비했다.
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
- 로컬 Python 검사 120 tests PASS. Windows read-only sandbox의 명령 읽기 실행 확인.
- 실제 구독 리뷰 및 Actions 결과 전달은 아직 미검증이다. 전체 Phase 6 완료를 뜻하지 않는다.
- PC 가동이 로컬 리뷰의 전제다. GitHub Actions CI는 PC 없이 실행된다.
