# AI 드론 비전 추적기

**실내에서 지정 드론 1대를 검출·추적하고 팬·틸트 카메라가 화면 중앙으로 자동 추종하는 캡스톤 프로젝트.**

> v1.0 설계·개발 준비 패키지입니다. 추적 프로그램·펌웨어·학습 모델은 아직 구현·포함되어 있지 않습니다. 현재 파일만으로 실제 서보가 움직이지 않습니다.

## 처음 읽을 문서
| 문서 | 내용 |
|---|---|
| [설계서](docs/01_system_design.md) | MVP, 하드웨어, 전원, 인식·추적·제어, 시험 |
| [설계서 Word](docs/AI_드론_비전_추적_설계서_v1.0.docx) | 읽기·공유용 스냅샷. Markdown이 편집 원본 |
| [통신 규격](docs/02_serial_protocol.md) | 메시지, 상태, watchdog, 오류 처리 |
| [데이터·평가 계획](docs/03_dataset_and_evaluation.md) | 촬영·분할·라벨·평가 기준 |
| [개발 계획](docs/04_implementation_plan.md) | P0~P6 단계와 완료 기준 |
| [결정 기록](docs/05_decisions.md) | 확정 / 초기값 / 실측 / 후속 논의 |
| [출처](docs/06_sources.md) | 첨부 근거와 공식 참고 자료 |

## MVP 흐름
카메라 → 노트북 검출·추적 → 화면 중심 오차 → 팬·틸트 목표값 → USB Serial → Arduino → PCA9685 → 서보 → 카메라 방향 변경.

레이저/LED는 후속 범위입니다. 이번 완료 기준은 화면 십자선과 카메라 추종이며, 자동 조사·정밀 조준 구현은 포함하지 않습니다.

## Codex 작업 시작
저장소 루트에서 `AGENTS.md`를 먼저 읽고 `prompts/implementation.md`의 작업 프롬프트를 사용합니다. 첫 범위는 P0 환경 확인과 P1 영상 입력 준비입니다. 구현 후에는 `prompts/review.md`로 별도 리뷰 작업을 시작합니다.

설치 방식, 플러그인, MCP, GitHub 자동 리뷰 연결과 비용 설정은 아직 결정·설정하지 않았습니다. 여기 있는 지침은 자동 실행 환경이 아닙니다.

## GitHub에 올리기
ZIP을 풀고 **README.md와 AGENTS.md가 있는 폴더를 저장소 루트**로 사용합니다. ZIP 한 개를 저장소에 올리는 것이 아니라 압축을 푼 파일·폴더를 커밋합니다. 아래는 새 로컬 폴더를 새 빈 원격 저장소에 올릴 때의 예시입니다. 기존 저장소에는 `git init`을 반복하지 말고 기존 이력을 유지합니다.

```bash
git init
git add .
git commit -m "docs: initialize drone tracker design v1.0"
git branch -M main
# GitHub에서 만든 빈 저장소 URL을 아래에 넣습니다.
git remote add origin <YOUR_REPOSITORY_URL>
git push -u origin main
```

숨김 파일인 `.gitignore`, `.gitattributes`, `.github/`도 포함합니다. 공개 전에 촬영 자료·개인정보·모델 라이선스·팀 공개 동의를 확인합니다. 라이선스는 임의로 지정하지 않았습니다.

## 포함된 검사
Python이 있는 환경에서 아래 명령은 패키지 구조와 예제 설정만 검사합니다. GPU·카메라·시리얼 장치·인터넷을 사용하지 않습니다.

```bash
python tools/check_design_package.py
```

실제 인식·추종 성능 시험은 P1~P6 구현 이후 수행해야 합니다.
