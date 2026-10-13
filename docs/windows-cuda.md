# Windows CUDA 설치와 소프트웨어 검증

같은 Python 프로그램에서 Windows NVIDIA CUDA, Apple MPS, CPU를 선택한다.
Windows x86_64 / Python 3.11용 안내다. Mac 전용 잠금 파일과 Finder 실행기는 Windows에 사용하지 않는다.
카메라·Serial·서보를 자동으로 시작하거나 드라이버·전역 Python 설정을 바꾸지 않는다.

## 설치

저장소 루트의 PowerShell에서 실행한다. 가상환경 실행 파일을 직접 사용하므로 활성화나 실행 정책 변경이 필요 없다.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install torch==2.9.1 torchvision==0.24.1 --index-url https://download.pytorch.org/whl/cu128
.\.venv\Scripts\python.exe -m pip install -r requirements-windows.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"
```

torch 2.9.1 / torchvision 0.24.1 / CUDA runtime 12.8은
[공식 설치 조합](https://pytorch.org/get-started/previous-versions/#v291)이다.
기존 프로젝트 버전을 유지하고 ONNX·OpenCV·Pillow도 기존 Mac 환경과 같은 직접 의존성 버전으로 고정한다.
`requirements-windows.txt`는 전체 전이 의존성 잠금 파일이 아니며 설치된 환경은 `pip check`로 확인한다.
CUDA wheel 설치 성공이나 `torch.version.cuda` 값만으로 GPU 사용 가능을 판단하지 않는다.
실제 NVIDIA GPU·호환 드라이버가 있어야 `torch.cuda.is_available()`가 참이 된다.
기존 [2026-10-01 RTX 5070 검증](../reports/training_validation_windows_2026-10-01.md)은 당시 학습 경로의 증거다.
현재 GUI·추가 학습 변경의 GPU 재검증 증거로 재사용하지 않는다.

## GUI

```powershell
.\.venv\Scripts\python.exe -m tools.camera_gui
```

Windows 기본 추론 장치는 `cpu`다. NVIDIA GPU를 사용하려면 `cuda`를 명시적으로 선택한다.
Windows 카메라는 0~9번 목록에서 선택하며 **카메라 시작**을 누를 때만 실제 입력을 연다.
카메라 권한과 선택 번호, 실제 입력 해상도·FPS는 해당 PC에서 확인한다.
수동 ViT는 [해시가 고정된 로컬 모델](../models/vittrack.md)이 있을 때 CUDA 추론을 사용한다.
모델이 없으면 GUI는 `ViT 모델 없음`을 표시하고 기존 특징점·템플릿 방식을 사용한다.
YOLO는 신뢰하는 로컬 드론 `best.pt`를 사용한다. GPU 모델 추론에서 명시한 장치를 사용할 수 없으면
오류를 표시하고 정지하며, CPU로 조용히 바꾸지 않는다.
전처리·특징점·재검색은 CPU에 남는다. 실제 추적 성능·정확도는 별도 시험이 필요하다.

## Seraphim 1차 학습 이후 실제 사진 추가 학습

[데이터 준비·라벨 검수·촬영 그룹 분할](retraining.md)을 먼저 마친다.
첫 baseline은 `runs/train/seraphim-v1/weights/best.pt`에서 `models/local/baseline.pt`로 복사한 동일 파일이며,
PowerShell `Get-FileHash -Algorithm SHA256`으로 두 해시를 대조한다.
test는 학습·반복 튜닝·아래 validation 비교에 사용하지 않는다.

```powershell
.\.venv\Scripts\python.exe -m tools.train --data data/dataset/drone-retrain-v1/dataset.yaml --manifest data/dataset/drone-retrain-v1/manifest.json --check-only
.\.venv\Scripts\python.exe -m tools.train --data data/dataset/drone-retrain-v1/dataset.yaml --manifest data/dataset/drone-retrain-v1/manifest.json --model models/local/baseline.pt --device cuda --epochs 100 --imgsz 640 --batch 4 --output runs/train/drone-retrain-v1-cuda
.\.venv\Scripts\python.exe -m tools.retrain compare --data data/dataset/drone-retrain-v1/dataset.yaml --manifest data/dataset/drone-retrain-v1/manifest.json --baseline models/local/baseline.pt --candidate runs/train/drone-retrain-v1-cuda/weights/best.pt --device cuda --output runs/compare/drone-retrain-v1-cuda --imgsz 640 --batch 4
```

`--device cuda`는 GPU가 없으면 실패한다. 학습과 모델 비교의 `auto`는 CUDA → MPS → CPU 순으로 선택한다.
GPU 검증에는 `cuda`를 명시해 CPU 실행을 CUDA 결과로 혼동하지 않는다.
기존 출력은 덮어쓰지 않으며 반복 시험에는 새 출력 폴더를 사용한다.

## 검증 범위

`ci.yml`의 `windows-software-checks`는 GPU 없는 GitHub Windows Server 2022 러너에서 실행한다.
CUDA wheel·Tk·OpenCV·ONNX 변환 라이브러리의 import, 한글 경로 원본 PNG 저장·복원,
의존성 충돌, 명시적 CUDA 불가 오류,
전체 `python -m tools.ci`와 임의 가중치·합성 사진의 CPU 1 epoch 학습 경로를 확인한다.
기존 Linux `software-checks`와 읽기 전용 토큰·고정 action SHA를 유지한다.
실제 실행 성공·실패와 테스트 수는 PR의 정확한 head에 연결된 Actions 및 독립 리뷰 기록에서 확인한다.
Windows 검사가 PASS여도 GPU 추론·CUDA 학습·실제 드론 정확도를 검증한 것은 아니다.

향후 Windows NVIDIA PC를 확보하면 먼저 CUDA 가용성을 확인하고 다음 기존 합성 검사를 실행한다.

```powershell
$env:RUN_TRAINING_SMOKE="1"
$env:TRAINING_SMOKE_DEVICE="cuda"
.\.venv\Scripts\python.exe -m pytest tests/test_training.py -k synthetic_training_smoke --basetemp=artifacts/windows-cuda-smoke -q
```

실제 GPU의 ViT·YOLO 추론, 같은 validation 비교, 지정 드론 test, 카메라 입력 FPS·지연과
Serial·서보·펌웨어는 현재 미검증이다. GUI 수동 ViT와 학습 YOLO는 서로 다른 모델·용도다.
