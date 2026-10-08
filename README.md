# AI 드론 비전 추적기

**실내에서 지정 드론 1대를 검출·추적하고 팬·틸트 카메라가 화면 중앙으로 자동 추종하는 캡스톤 프로젝트.**

> Markdown 설계 기준은 v1.1이며 단일 카메라·팬/틸트 2축의 운용 한계를 보완했습니다. 로컬 학습 실행 도구는 포함되어 있으나 카메라 추적 프로그램·펌웨어·지정 드론 학습 모델은 아직 없습니다. 현재 파일만으로 실제 서보가 움직이지 않습니다.

## 드론 학습 시작

CUDA(NVIDIA)·MPS(Apple Silicon)·CPU 선택을 지원하는 `python -m tools.train`을 사용합니다.
데이터 세션·라벨을 먼저 검사하고 학습 결과와 재현 정보를 로컬에 기록합니다.
설치와 실행 명령은 [학습 안내](docs/training.md)를 참조합니다.
1차 학습은 Seraphim 공개 데이터셋, 이후 추가 학습은 실제 지정 드론 사진을 사용합니다.
데이터 준비 명령과 1차 `best.pt`에서 추가 학습을 시작하는 방법도 학습 안내에 있습니다.
GitHub 업로드는 사용자 허락 후 PR로 진행합니다.

## 처음 읽을 문서
| 문서 | 내용 |
|---|---|
| [설계서](docs/01_system_design.md) | MVP, 하드웨어, 전원, 인식·추적·제어, 시험 |
| [설계서 Word](docs/AI_드론_비전_추적_설계서_v1.0.docx) | 과거 v1.0 스냅샷. 최신 v1.1 기준은 Markdown |
| [통신 규격](docs/02_serial_protocol.md) | 메시지, 상태, watchdog, 오류 처리 |
| [데이터·평가 계획](docs/03_dataset_and_evaluation.md) | 촬영·분할·라벨·평가 기준 |
| [학습 실행](docs/training.md) | CUDA/MPS 환경·데이터 검사·학습 명령 |
| [개발 계획](docs/04_implementation_plan.md) | P0~P6 단계와 완료 기준 |
| [결정 기록](docs/05_decisions.md) | 확정 / 초기값 / 실측 / 후속 논의 |
| [출처](docs/06_sources.md) | 첨부 근거와 공식 참고 자료 |
| [단일 카메라 운용 범위](docs/07_single_camera_operating_envelope.md) | 경계 제한·안쪽 복귀·시험 영역·SC01–SC08 시험 계획 |

## MVP 흐름
카메라 → 노트북 검출·추적 → 화면 중심 오차 → 팬·틸트 목표값 → USB Serial → Arduino → PCA9685 → 서보 → 카메라 방향 변경.

레이저/LED는 후속 범위입니다. 이번 완료 기준은 화면 십자선과 카메라 추종이며, 자동 조사·정밀 조준 구현은 포함하지 않습니다.

거리 측정·두 번째 카메라·3축 이상·추가 전원을 추가하지 않습니다. 검증된 앞쪽 시험 영역과
보수적인 2축 운용 범위를 사용하며 머리 위·후방 모든 경로의 연속 추종을 보장하지 않습니다.
시험 영역은 사전에 고정하고, 영역 내 실패를 사후 제외하지 않습니다.

## Codex 작업 시작
저장소 루트에서 `AGENTS.md`와 최신 결정·구현 계획을 읽고 `prompts/implementation.md`의 작업 범위를 선택한 단계에 맞춥니다. 학습 도구의 기존 환경 검증을 보존하고, 실제 데이터의 P2 평가 또는 미구현 P1 영상 입력부터 이어갑니다. 구현 후에는 `prompts/review.md`로 별도 리뷰 작업을 시작합니다.

팀원은 자신의 Codex에서 `$dev-orchestrator <기능 요청>`으로 공통 스킬을 사용합니다.
별도 세션의 독립 리뷰, PR당 최대 2회 수정, Decision Gate를 거쳐 요청받은 PR을 병합합니다.
GitHub Actions는 일반 CI를 실행합니다. 별도의 AI API Key나 소유자 PC 상시 가동은 필요 없습니다.
[팀원 안내](docs/automation/collaborators.md)와 [실행 방법](docs/automation/development.md)을 확인하세요.
스킬은 협업 절차이며 계정 권한을 추가하거나 무인 병합 서비스를 실행하지 않습니다.

## GitHub 협업

기존 저장소는 feature branch와 PR로 변경합니다. main 직접 push나 보호 우회는 하지 않습니다.
각자의 기존 GitHub 권한을 사용하고, 병합 직전에 최신 CI·독립 리뷰·Gate 결과를 확인합니다.
촬영 자료·개인정보·가중치·키를 커밋하지 않습니다. 라이선스는 임의로 지정하지 않았습니다.

## 포함된 검사
Python이 있는 환경에서 아래 명령은 패키지 구조와 예제 설정만 검사합니다. GPU·카메라·시리얼 장치·인터넷을 사용하지 않습니다.

```bash
python tools/check_design_package.py
```

실제 인식·추종 성능 시험은 P1~P6 구현 이후 수행해야 합니다.

## 개발 검증

Python 3.11.0에서 `python -m venv .venv`로 환경을 만들고 활성화한 뒤 실행합니다.

```bash
python -m pip install -r requirements-dev.txt
python -m tools.ci
```

Ruff·pytest·syntax/import·설정·문서 검사를 같은 명령으로 로컬과 CI에서 실행합니다.
이는 카메라·모델 정확도·Serial·모터 시험을 대체하지 않습니다.
