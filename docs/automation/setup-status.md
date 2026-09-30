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
4. CI와 읽기 전용 리뷰 workflow. 최신 PR head/CI/작성자 권한을 확인하고
   base의 신뢰된 도구와 지침만 실행한다. draft/fork/설정 누락에서는 AI를 실행하지 않는다.
5. API 사용 비용 승인과 `OPENAI_API_KEY` 등록을 별도 gate로 처리한다.
6. 작은 PR에서 실제 외부 리뷰·JSON·최신 SHA·파일 불변성을 검증한다.
7. 6단계 성공 후에만 Fixer 권한을 논의하고 최대 2회 수정 → CI → 새 리뷰를 구현한다.
8. Decision Gate는 판단만 수행한다. 실물 관찰과 미검증 항목을 명시한다.
9. 모든 증거가 최신 SHA에 묶이는 것을 검증한 뒤 마지막으로 branch rule/auto merge를 활성화한다.

## 현재 경계

외부 AI 리뷰, 자동 Fix, 자동 Merge는 비활성이다. 비용·Secret·권한 승인을 추정하지 않는다.
기존 문서의 수동 병합 원칙은 이번 사용자 요청에 따라 **9단계 검증 완료 후에만**
조건부 자동 병합으로 확장한다. 하드웨어와 MVP 요구사항은 바뀌지 않는다.
원래 `PACKAGE_SHA256.json`은 최초 v1.0 배포 스냅샷의 해시이며 현재 브랜치 manifest가 아니다.
현재 개발 변경에 맞추어 과거 기록을 재생성하지 않는다.

## 실행 결과

- Phase 1 감사 완료, Phase 2 로컬 검증 기반 구현, Phase 3 Skill/구조화 리뷰 도구 구현.
- Phase 4 read-only workflow 작성·정적 검사 완료. 외부 Codex 실행 검증은 아직 아니다.
- Windows Python 3.11.0에서 `python -m tools.ci`: 81 tests PASS,
  Ruff lint/format, syntax/import, 문서·예제 설정 검사 PASS.
- 7개 Skill 원본과 7개 탐색 진입점: bundled quick_validate PASS (Windows UTF-8 모드).
- actionlint 1.7.7 정적 검사 PASS. release checksum 검증 후 artifacts 아래에서만 사용.
  shellcheck/pyflakes는 이 별도 검사에 미사용이며 Python은 Ruff/pytest로 검사했다.
- GitHub CI와 별도 읽기 전용 reviewer: 기반 PR 생성 후 결과를 기록한다.
- Arduino compile: 소스 없음으로 SKIP. 카메라/AI 품질/Serial/서보/펌웨어 업로드/레이저 미실행.
- 다음 최초 gate: Phase 5 API 사용에 새 비용과 Secret이 필요하다.
  비용 승인을 받기 전 Secret 등록이나 외부 리뷰 활성화를 요구하지 않는다.
- Auto Fix 0회, Auto Merge 비활성, main 직접 push/권한 변경 없음.
