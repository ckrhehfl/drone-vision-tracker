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

## ChatGPT 구독 리뷰

사용자 선택에 따라 별도 유료 API와 GitHub API Secret은 사용하지 않는다.
공개 저장소의 표준 Ubuntu GitHub runner에서 CI를 실행하고, 현재 PC에 로그인된
Codex CLI 0.130.0으로 독립 리뷰를 실행한다. AI 호출은 구독 사용 한도를 소모한다.
한도/인증/timeout 실패 시 중단하며 유료 API나 추가 크레딧 구매로 자동 전환하지 않는다.
PC가 꺼져 있으면 CI는 진행할 수 있지만 로컬 리뷰는 진행하지 못한다.

```powershell
codex login status
.venv/Scripts/python -m tools.subscription_review --ci-run <최신-CI-run-ID>
```

CLI는 기존 ChatGPT 인증을 사용하고 API 인증은 거부한다. 인증 파일을 조회·복사·업로드하지 않는다.
GitHub 조회는 기존 `gh` 로그인으로 수행하며 reviewer 자식 프로세스에는 GitHub/API 키 환경변수를
전달하지 않는다. reviewer는 독립 임시 clone의 base만 checkout한다. 각 scope는 새 세션이며
read-only sandbox, 승인 never, 사용자 config/rules 비적용, agent/app/web 도구 비활성으로 실행한다.
출력은 무시되는 `artifacts/subscription-review/<고유-실행>/`에 보존한다.
전체 finding은 `result.json`, SHA·CI·파일 불변성 증거는 `evidence.json`이다.
CLI 진단 로그는 로컬에서만 확인하고 자동 업로드하지 않는다.

현재 API에서 PR이 열림/non-draft/동일 저장소/write 이상 작성자/최신 head/main base인지 다시 확인한다.
같은 head의 최신 CI run/attempt와 필수 software-checks job 및 Run software checks step이
모두 완료·성공했는지 검사한다. run 전체가 success여도 실제 검사 step 증거가 없으면 거부한다.
낮은 run ID를 나중에 재실행할 수 있으므로 각 run 최신 attempt의 API 시작 시각으로
순서를 판별한다. 이력이 한 페이지를 초과하거나 시각이 없거나 동률이면 승인하지 않는다.
판정 전에도 현재 SHA를 다시 검사한다. reviewer는 base checkout과 git 객체를 읽고
PR의 스크립트·설정·AGENTS를 실행하지 않는다. 독립 테스트 실행은 미검증으로 기록한다.
tracked 파일/작업 트리/Git refs의 fingerprint 변화도 검사한다.

`codex-review.yml`의 API 호출은 제거했다. 현재는 수동 실행 시 미구축 상태를 실패로 알리는
대기 workflow다. CI 성공이나 이 workflow의 미실행을 리뷰 PASS로 사용할 수 없다.
구조화 결과를 Actions에서 검증·게시하는 연결은 다음 단계이며 현재 로컬 JSON은 merge 승인이 아니다.

GitHub 기본 `@codex review`는 별도 공식 구독 연동이다. 해당 연동의 자유 형식 결과만으로
이 저장소의 전체 finding·JSON Schema·최신 SHA 검증을 대체하지 않는다.
OpenAI 공식 문서는 ChatGPT auth.json을 CI로 옮기는 인증 절차를 공개 저장소에 사용하지 말라고
명시한다. 여기서는 GitHub self-hosted runner도 설치하지 않는다.

## 단계 6 검증 계획 (아직 실행하지 않음)

기반 PR에서 로컬 구독 리뷰를 먼저 확인하고 bootstrap 통합 후 작은 모의 테스트 PR을 만든다.
구독 리뷰의 JSON·상태·파일 불변성을 확인하고 Actions 결과 전달을 별도로 검증한다.
의도된 작은 결함 또는 schema fixture로 CHANGES_REQUESTED 판정도 확인한다.
새 head로 갱신해 이전 리뷰가 무효가 되고 새 CI/독립 리뷰가 필요한지 확인한다.
실행당 head/base/run ID와 인증 방식·미검증 항목을 남긴다. 이를 통과한 뒤 Phase 7로 진행한다.

## 공식 근거

- [Codex 인증](https://learn.chatgpt.com/docs/auth): ChatGPT 구독 인증과 API 과금 인증의 구분.
- [GitHub Actions 과금](https://docs.github.com/en/billing/concepts/product-billing/github-actions): 공개 저장소 표준 runner 실행 시간 무료, 별도 저장 용량·대형 runner 과금 조건.
- [Codex Skill 탐색](https://learn.chatgpt.com/docs/build-skills): repository `.agents/skills`, SKILL.md frontmatter.
- [Codex 비대화형 실행](https://learn.chatgpt.com/docs/non-interactive-mode): 별도 exec 세션과 structured output.
- [GitHub workflow_run](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run): default branch 및 비신뢰 코드 주의.

CI Action은 GitHub에서 확인한 commit SHA에 고정했다. 구독 리뷰의 실제 실행 증거는
[구축 상태](setup-status.md)와 PR 설명에 기록한다.
