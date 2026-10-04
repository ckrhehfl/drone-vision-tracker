# 로컬 학습 도구 검증 — 2026-10-01

범위: CUDA/MPS 장치 선택, 세션·라벨 사전 검사, 로컬 학습 실행과 기록.
시작 commit: b9913e691b5c460336a48200828deb54a0ad18d5.
GitHub 업로드는 사용자 허락 전까지 수행하지 않는다.

## 조사한 환경

- MacBook Air / Apple M5 / 통합 메모리 24GB / arm64.
- macOS 27.0.1 (26A434).
- 기본 python3는 3.9.6. 별도 설치되어 있던 python3.11은 3.11.16.
- 저장소 안의 .venv에 개발·학습 패키지를 설치했다.
- torch 2.9.1 / torchvision 0.24.1 / ultralytics 8.3.203.
- 전체 설치 버전은 requirements-macos.lock.txt에 기록했다.
- MPS는 GPU 접근을 허용한 실행에서 사용 가능. 격리 실행에서는 사용 불가로 표시된다.
- NVIDIA GPU·CUDA 드라이버·CUDA 학습은 이 컴퓨터에서 확인할 수 없다.

## 검증

- python -m tools.ci: lint/format, 문서·설정, 157 passed / 1 skipped.
- python -m pip check: No broken requirements found.
- 합성 JPG 3장(train/val/test 각 1장), 임의 초기 YOLO11n 가중치,
  imgsz=64 / batch=1 / epochs=1 / device=mps로 학습 실행 통과.
- best.pt/last.pt 생성, 완료 기록, validation의 객체 1개 확인 통과.
- test는 학습에 전달한 YAML에서 제외됨을 확인.
- 일반 CI의 합성 GPU 학습 검사는 opt-in으로 skip한다.

## 발견·보완

torch 2.8.0/torchvision 0.23.0에서 첫 MPS 실행은 가중치를 생성했지만,
validation이 실제 라벨 1개를 0개로 보고했다.
CPU와 MPS의 동일 가중치·데이터를 비교하고 CPU→MPS 비동기 전송에서
batch_idx/cls 값이 깨지는 것을 관찰했다. torch 2.9.1에서도 동일 현상을 재현했다.
MPS 학습·validation 배치의 전송을 동기화한 뒤 정상 객체 수와 학습 완료를 확인했다.
라벨 수가 사전 검사와 다르면 성공으로 기록하지 않는다.

별도 읽기 전용 리뷰에서 중첩된 images 디렉터리가 vendor 라벨 변환 경로를 바꾸어
학습 라벨을 누락시키는 문제를 재현했다. 해당 경로를 사전 검사에서 거부하고 회귀 검사를 추가했다.
숨김 이미지가 vendor glob에서 제외되는 두 번째 문제도 같은 방식으로 보완했다.
학습 직전 실제 읽힌 train/val 이미지·객체 수를 검사해 조용한 누락을 중단한다.

## 미검증과 다음 작업

실제 지정 드론 촬영 자료가 없어 전이학습·최종 test 정확도는 미검증이다.
CUDA 실제 학습, 카메라 FPS·추적·Serial·모터·펌웨어는 미실행이다.
데이터 검사는 파일 해시 기준이다. 유사 프레임과 잘못 기록한 세션을 자동 판정하지 않는다.
다음 작업은 지정 드론 영상과 검수된 라벨을 확보해 같은 도구로 학습·평가하는 것이다.
