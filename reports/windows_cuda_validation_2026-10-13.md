# PR #19 Windows CUDA 실기기 검증 — 2026-10-13

시작 head: `ceca38a0af53e68c4e0fcc3abb86518cbcc53f9e`.
base: `219b797ce0a4a33df5388bd291af7b2746259f7b`.
검증 대상은 이 head에 아래 ONNX 변환 수정·회귀 검증을 적용한 작업본이다.
최종 commit·CI·독립 리뷰 식별은 PR #19에 기록한다.
기존 2026-10-01 학습 기록과 GPU 없는 GitHub Windows CI를 이번 실기기 증거로 재사용하지 않았다.

## 환경

- Windows x86_64 build 26300, CPython 3.11.15, 파일 기본 인코딩 CP949, UTF-8 모드 0.
- RTX 5070 / VRAM 12227 MiB / 드라이버 617.14 / compute capability 12.0.
- torch 2.9.1+cu128 / torchvision 0.24.1+cu128 / ultralytics 8.3.203 / numpy 2.2.6.
- ONNX 1.17.0 / onnx2torch 1.5.15 / protobuf 7.36.2 / opencv-python 5.0.0.93.
- 기존 캡스톤 폴더의 `.venv`에 `requirements-windows.txt`의 누락 패키지만 설치했다.
  설치 캐시·임시 자료·검증 산출물도 같은 지정 폴더 안에 두었다. 전역 환경·드라이버 변경 없음.
- `pip check` 통과. 전체 설치 버전과 도구별 SHA-256은 로컬 ignored `artifacts/`에 보존했다.
- 저장소 지정 ViT 원본과 LICENSE를 로컬에 준비했다. 크기 714726 bytes,
  SHA-256 `2990f0b7cd44d92afa48cd97db6de7be113fc1d9594fddb74e2725c10478e91d` 확인.
  가중치·사진·원시 로그는 커밋하지 않았다.

## 재현한 문제와 수정

`create_manual_tracker(0.65, "cuda")`가 추론 전에 `PermissionError`로 실패했다.
onnx2torch 1.5.15에 경로를 전달하면 `NamedTemporaryFile`을 열린 상태로 유지하고
shape inference 결과를 같은 파일에서 다시 읽는다. Windows 파일 잠금과 충돌한다.
일반 CI와 기존 mock은 실제 모델의 변환을 수행하지 않아 이를 검출하지 못했다.

공통 `_torch_model`에서 기존 ONNX 로더로 모델을 먼저 읽고 `convert(ModelProto)`를 호출한다.
고정 모델은 작은 메모리 shape inference 경로를 사용하며 vendor 파일·의존성 버전은 변경하지 않았다.
해시 검증·GPU별 캐시·trace/freeze·warmup과 대상별 상태는 유지했다.
기존 CUDA/MPS 재시작 테스트는 로더 반환 모델이 변환기에 전달되는지와 재사용을 확인하도록 보강했다.

## 검사 결과

- `python -m tools.ci`: **342 passed / 1 skipped**. Ruff·format·문서·설정·syntax 통과.
- `python -m pytest tests/test_manual_vit.py -q`: **12 passed**.
- 기본 임시 폴더의 기존 pytest 자료는 로컬 sandbox에서 `WinError 5`로 읽을 수 없었다.
  기존 자료를 삭제하거나 권한을 바꾸지 않고 이번 실행 전용 basetemp·cache_dir를 사용했다.
- Tk GUI 생성·Windows 기본 CPU·CUDA 선택지 확인. 실제 카메라를 열지 않았다.
- 원본 PNG의 한글 경로 저장·복원 후 픽셀 일치 확인.

저장소 루트에서 기본 검사 재현 명령:

```powershell
$env:PYTHONIOENCODING="utf-8"
$env:PYTHONUTF8="0"
$env:PYTEST_ADDOPTS="--basetemp=artifacts/cuda-ci-new -o cache_dir=artifacts/cuda-ci-cache-new"
.\.venv\Scripts\python.exe -m tools.ci
```

basetemp는 매번 새 경로를 선택한다.

## 실제 CUDA 실행

- 수정 후 해시가 고정된 ViT의 초기화·GPU warmup·합성 프레임 update 3회 통과.
  GPU 템플릿·출력 텐서, 출력 크기 `(1,1,16,16)/(1,2,16,16)/(1,2,16,16)`와 유한 값 확인.
  동일 CUDA 네트워크의 재사용과 두 추적기의 템플릿·위치 분리 확인.
- 기존 합성 학습 검사: **1 passed / 24 deselected**, CUDA:0 / 1 epoch / imgsz=64 / batch=1.
  train/val/test 각 1장의 합성 JPG와 임의 초기 YOLO11n 가중치를 사용했다.
  validation 객체 1개와 best/last 가중치·완료 기록 확인.
- 위 best 가중치에서 CUDA 1 epoch 추가 학습 통과. 입력 가중치 해시 일치,
  baseline 파일 보존, CUDA 장치·validation 객체 1개 확인.
- `tools.retrain compare --device cuda`로 baseline/candidate의 동일 validation 비교 통과.
  완료 상태·장치 0·가중치 해시·유효한 지표·학습/비교 YAML의 test 제외 확인.
- `CameraSession.process`의 실제 YOLO 경로에 합성 프레임 2개를 넣어 CUDA:0 추론 확인.
  캡처 세션을 생성하지 않았고 `VideoCapture`나 Serial·서보를 열지 않았다.
- pin_memory device 인자의 vendor 폐기 예정 경고가 발생했으나 학습은 완료됐다.

합성 학습 명령:

```powershell
$env:RUN_TRAINING_SMOKE="1"
$env:TRAINING_SMOKE_DEVICE="cuda"
.\.venv\Scripts\python.exe -m pytest tests/test_training.py -k synthetic_training_smoke --basetemp=artifacts/cuda-training-new -q -s
```

ViT 모델을 [원본 안내](../models/vittrack.md)에 따라 준비한 뒤 초기화·update 재현:

```powershell
@'
import numpy as np, torch
from tools.camera_gui import create_manual_tracker
tracker = create_manual_tracker(0.65, "cuda")
assert tracker is not None
frame = np.random.default_rng(0).integers(0, 256, (480, 640, 3), dtype=np.uint8)
tracker.init(frame, (160, 120, 80, 60))
assert tracker.template.is_cuda
tracker.update(frame)
torch.cuda.synchronize()
'@ | .\.venv\Scripts\python.exe -
```

## 한계

ViT 점수와 임의 YOLO 가중치의 validation 지표는 실제 드론 정확도를 나타내지 않는다.
합성 입력에서 검출이 없거나 0인 지표도 실행 흐름 확인과 구분한다.
실제 Seraphim 1차 가중치와 지정 드론 사진의 추가 학습·독립 test,
카메라 FPS·지연·추종 품질, Serial·서보·펌웨어, 장시간 학습·다른 GPU와
이번 변경의 Mac MPS 실기기 실행은 미검증이다.
다음 단계는 검수된 실제 데이터와 신뢰하는 baseline으로 같은 경로를 학습·평가하는 것이다.
