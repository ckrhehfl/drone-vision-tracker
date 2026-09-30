# 자동화 기반 실행과 검증

## 로컬 환경

검증 기준은 CPython 3.11.0이다. 드론 모델·GPU backend 의존성은 아직 선택하지 않는다.
Ruff 0.11.13 / pytest 8.3.5 / jsonschema 4.23.0 / PyYAML 6.0.2를 격리 환경에 설치하고
전이 의존성까지 `requirements-dev.txt`에 고정했다. 자동 업그레이드는 하지 않는다.

Windows PowerShell:

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python -m tools.ci
```

Linux:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m tools.ci
```

CI는 Ubuntu 24.04 / Python 3.11.0에서 `python -m tools.ci`를 실행한다.
검사: Ruff lint/format, 기존 문서 검사, offline 설정 검증, pytest(import 검사 포함),
Python compile, Arduino 소스 유무 검사. 현재 Arduino 소스가 없어서 compile은 명시적으로 skip한다.
앞으로 `.ino`/`.cpp`/`.c`가 추가되면 CI를 실패시키며 고정 toolchain의 compile-only 단계를
같은 runner에 추가해야 한다. 펌웨어 업로드나 포트 접근은 허용하지 않는다.

설정 검증은 **예제의 비활성 상태**를 위한 것이다. `hardware_enabled=true`인 runtime을
허가하는 함수가 아니며 실제 통신·보정·운영자 ARM 검증은 이후 기능 구현 범위다.

## 리뷰 계약

실제 base/head의 40자리 SHA를 사용한다. PR diff는 merge-base → head이며 base SHA도 기록한다.

```bash
python -m tools.review plan --base <base-sha> --head <head-sha> --output artifacts/plan.json
python -m tools.review validate --base <base-sha> --head <head-sha> --scope scope-0 --report artifacts/scope-0.json --output artifacts/validated.json
python -m tools.review merge --base <base-sha> --head <head-sha> --reports-dir artifacts --output artifacts/result.json
```

이 명령은 AI를 호출하지 않는다. [JSON Schema](../../schemas/review.schema.json)로 구조를 검사하고,
다른 SHA·scope 누락·PASS와 blocker의 모순을 거부한다. PASS만 exit 0, 다른 review status는 exit 1이다.
정확한 중복만 합쳐 별개 finding을 보존한다. 불완전한 JSON/실패/timeout은 승인 결과가 아니다.
모든 finding은 위치·원인·수정 방향·검증 방법을 포함한다. 실물 미검증은 관찰/기대 결과와
merge blocker 여부를 함께 기록한다.

`.codex/skills`에 사용자가 요청한 원본을 저장한다. 현재 공식 탐색 경로 `.agents/skills`에는
원본을 읽도록 하는 짧은 진입점을 둔다. symlink 권한이나 별도 플러그인 설치가 필요 없다.

## GitHub 리뷰 활성화 전 조건

현재 Secret과 활성화 변수는 없다. 외부 AI를 호출하지 않았다.
비용 승인 후 사용자는 Settings → Secrets and variables → Actions → New repository secret에서
`OPENAI_API_KEY`를 등록한다. Secret 값은 채팅에 전달하지 않는다.
기술적인 모델 선택은 계정의 사용 가능 모델·비용 범위를 확인해 에이전트가 고정한다.
`CODEX_REVIEW_MODEL` 변수와 `CODEX_REVIEW_ENABLED=true`, trusted base의
`config/automation.json` 활성화/스키마 변경이 모두 준비되어야 실행한다.
임의로 현재 false 설정을 바꾸면 안 된다. 예산/호출 한도도 승인 범위로 먼저 설정한다.
workflow의 20분 timeout과 병렬 2개 제한은 금액 상한이 아니다.

리뷰는 default branch의 workflow_run으로 성공 CI 이후 시작한다. 매 scope는 fresh Codex 세션이다.
현재 API에서 PR이 열림/non-draft/동일 저장소/write 이상 작성자/최신 head/main base인지 다시 확인한다.
판정 전에도 현재 SHA를 다시 검사한다. reviewer는 base checkout과 git 객체를 읽고
PR의 스크립트·설정·AGENTS를 실행하지 않는다. sandbox=read-only, safety-strategy=drop-sudo,
checkout credential 비저장, 모든 GITHUB_TOKEN 권한 read, 별도 임시 output 경로를 사용한다.
tracked 파일/작업 트리/Git refs의 fingerprint 변화도 검사한다.

현재 결과 게시 범위는 Actions summary와 7일 artifact다. PR 댓글/commit status 쓰기는 하지 않는다.
리뷰 workflow가 skipped된 것을 PR merge 승인으로 사용할 수 없다.

## 단계 6 검증 계획 (아직 실행하지 않음)

기반 PR bootstrap 통합 후 작은 모의 코드와 실패 경로 테스트 PR을 만들고 CI를 확인한다.
승인된 범위 안에서 외부 리뷰를 한 번 실행해 JSON·artifact·상태·파일 불변성을 확인한다.
의도된 작은 결함 또는 schema fixture로 CHANGES_REQUESTED 판정도 확인한다.
새 head로 갱신해 이전 리뷰가 무효가 되고 새 CI/독립 리뷰가 필요한지 확인한다.
실행당 head/base/run ID와 비용 증거를 남긴다. 이를 통과한 뒤 Phase 7로 진행한다.

## 공식 근거

- [Codex Action](https://learn.chatgpt.com/docs/github-action): API Secret, read-only sandbox, drop-sudo, output schema.
- [Codex Skill 탐색](https://learn.chatgpt.com/docs/build-skills): repository `.agents/skills`, SKILL.md frontmatter.
- [Codex 비대화형 실행](https://learn.chatgpt.com/docs/non-interactive-mode): 별도 exec 세션과 structured output.
- [GitHub workflow_run](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run): default branch 및 비신뢰 코드 주의.

Action은 GitHub에서 확인한 commit SHA에 고정했다. Codex CLI는 실제 로컬 확인 버전 0.130.0에
고정했지만 GitHub API 리뷰 동작 자체는 단계 6 전까지 미검증이다.
