# 지정 드론 학습 실행

이번 범위는 P0 환경 확인, P2a 데이터 사전 검사, P2b 학습 실행 도구다.
실제 지정 드론 데이터·가중치와 카메라 추적·팬틸트 제어는 아직 포함하지 않는다.

## 설치

Python 3.11, torch 2.9.1, torchvision 0.24.1, ultralytics 8.3.203을 사용한다.
Mac 신규 환경:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-macos.lock.txt
python -c "import torch; print(torch.backends.mps.is_available())"
```

`requirements-macos.lock.txt`는 Mac arm64의 개발·학습 환경 전체 버전 기록이다.
Windows/Linux 설치에는 사용하지 않는다.
NVIDIA PC에서도 Python 3.11 가상환경을 만든 뒤 활성화한다.
Windows PowerShell: `py -3.11 -m venv .venv`, `.venv\Scripts\Activate.ps1`.
Linux: `python3.11 -m venv .venv`, `source .venv/bin/activate`.
GPU·드라이버에 맞는 CUDA wheel을 먼저 확인한다. 아래 cu126은 공식 설치 조합의 예시다.

```bash
python -m pip install torch==2.9.1 torchvision==0.24.1 --index-url https://download.pytorch.org/whl/cu126
python -m pip install -r requirements-dev.txt -r requirements-training.txt
python -c "import torch; print(torch.cuda.is_available(), torch.version.cuda)"
```

CUDA 장비의 실제 호환성과 학습은 아직 검증하지 않았다.
Mac/CUDA의 수치·속도·최적 batch가 같다고 보장하지 않는다.
`--device auto`는 CUDA → MPS → CPU 순으로 선택한다.
cuda/mps를 명시하면 해당 장치가 없을 때 실패한다.

## 데이터와 세션

촬영 세션을 먼저 train/val/test로 나눈다. 같은 촬영을 자른 영상도 같은 세션이다.
JPG/JPEG/PNG와 같은 이름의 YOLO 라벨을 다음 구조로 준비한다.

```text
data/dataset/
  images/train/session01/frame001.jpg
  images/val/session02/frame001.jpg
  images/test/session03/frame001.jpg
  labels/train/session01/frame001.txt
  labels/val/session02/frame001.txt
  labels/test/session03/frame001.txt
```

라벨은 `0 x_center y_center width height` 형식의 정규화 박스다.
검수된 빈 배경에는 빈 라벨 파일을 둔다. 각 split에는 적어도 하나의 드론 라벨이 필요하다.
`config/dataset.example.yaml`을 사용하거나 복사해 path를 수정한다.
path는 YAML 파일 위치를 기준으로 해석한다.

`data/manifests/drone-v1.json`을 작성한다. images는 데이터셋 루트 기준 경로다.
모든 이미지가 manifest에 정확히 한 번 나타나야 한다.

```json
{
  "schema_version": 1,
  "status": "ready",
  "dataset_version": "drone-v1",
  "sessions": [
    {"session_id": "session01", "split": "train", "images": ["images/train/session01/frame001.jpg"]},
    {"session_id": "session02", "split": "val", "images": ["images/val/session02/frame001.jpg"]},
    {"session_id": "session03", "split": "test", "images": ["images/test/session03/frame001.jpg"]}
  ]
}
```

템플릿의 example_only는 실행 대상으로 받지 않는다. 라벨 검수가 끝난 manifest에만 ready를 사용한다.
중복 세션 ID, split 밖 경로, 누락 파일, 다른 클래스, 잘못된 좌표,
split 사이 동일 파일 해시를 거부한다.
이미지 경로의 하위 디렉터리 이름으로 images를 다시 사용하지 않는다.
숨김 이미지·숨김 하위 디렉터리와 glob 특수문자가 있는 데이터셋 루트는 거부한다.
학습 직전 YOLO가 실제 읽은 train/val 이미지·객체 수를 다시 검사한다.
비슷한 연속 프레임·잘못 묶은 세션·기체 외형의 정답성은 사람이 검수한다.
사전 검사는 이미지 디코딩을 수행하지 않는다.

```bash
python -m tools.train --data config/dataset.example.yaml --manifest data/manifests/drone-v1.json --check-only
```

이 검사는 PyYAML만 필요하고 PyTorch/Ultralytics를 가져오지 않는다.

## 학습

신뢰할 수 있는 로컬 detection `.pt` 파일을 지정한다.
일반 사전학습 가중치는 지정 드론 완성 모델이 아니다.

```bash
python -m tools.train --data config/dataset.example.yaml --manifest data/manifests/drone-v1.json --model models/local/base.pt --device mps --epochs 100 --imgsz 640 --batch 4 --output runs/train/drone-v1-mps
```

NVIDIA PC에서는 device를 cuda로, output을 runs/train/drone-v1-cuda로 바꾼다.
100 epochs·640·batch 4는 시작값이다. 메모리가 부족하면 batch를 줄인다.
기본은 workers=0, AMP 꺼짐이다. 기존 output을 재사용하지 않는다.
MPS에서는 배치 텐서를 동기 전송한다. 검증 중 CPU→MPS 비동기 전송에서 라벨 값이
깨지는 현상을 재현했고, 학습과 validation 양쪽의 전송을 보완했다.
validation 라벨 수가 사전 검사와 다르면 해당 실행을 실패로 기록한다.
train/val의 자동 생성 라벨 캐시는 학습 시작 전에 다시 생성하도록 제거한다.
원본 이미지·라벨·test 캐시는 보존한다. 같은 길이로 수정된 라벨도 새로 읽게 하기 위함이다.
[관련 PyTorch 이슈](https://github.com/pytorch/pytorch/issues/189690)도 같은 전송 수명 문제를 보고한다.
가중치 자동 다운로드·패키지 자동 설치·외부 학습 서비스 연동은 비활성이다.
학습은 로컬에서 수행하며 촬영 자료를 서버에 보내지 않는다.

결과: weights/best.pt, weights/last.pt, results.csv, args.yaml, training_record.json.
실행 기록에는 데이터 버전·해시·split 수량, 초기/결과 가중치 해시,
코드 commit·변경 여부·도구 해시, 환경·패키지 버전, 옵션, 완료/실패/중단 상태가 들어간다.
로컬 절대 경로가 포함될 수 있으므로 runs를 공개 업로드하지 않는다.
학습에 넘기는 YAML에는 train/val만 넣는다. test는 무결성 검사에만 사용한다.
최종 test 평가와 실제 드론 인식 품질은 아직 미검증이다.

## 검사

```bash
python -m tools.ci
```

일반 CI는 AI 패키지·GPU 없이 데이터·장치 선택·실패 기록을 검사한다.
합성 이미지와 임의 초기 가중치로 1 epoch를 실행하는 별도 확인은 아래 명령이다.
실제 드론 성능 검증이 아니다.

```bash
RUN_TRAINING_SMOKE=1 TRAINING_SMOKE_DEVICE=mps python -m pytest tests/test_training.py -k synthetic_training_smoke -q
```

CUDA PC PowerShell:

```powershell
$env:RUN_TRAINING_SMOKE="1"
$env:TRAINING_SMOKE_DEVICE="cuda"
python -m pytest tests/test_training.py -k synthetic_training_smoke -q
```

사용 도구·가중치의 라이선스는 공개·배포 전에 확인한다.
저장소 자체의 라이선스 결정은 변경하지 않았다.
참고: [YOLO 학습](https://docs.ultralytics.com/modes/train/),
[PyTorch 설치 조합](https://pytorch.org/get-started/previous-versions/#v291),
[Apple MPS](https://developer.apple.com/metal/pytorch/),
[Ultralytics 라이선스](https://www.ultralytics.com/license).
