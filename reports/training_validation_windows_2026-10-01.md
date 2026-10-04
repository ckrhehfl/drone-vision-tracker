# Windows CUDA 학습 도구 검증 — 2026-10-01

범위: PR #5의 일반 소프트웨어 검사, 실제 CUDA 연산, 합성 데이터 학습·validation·결과 저장.
시작 PR head: `3524e50ecb49946d4bc0ab34985970df96e11b4b`.
이 기록과 당시 CP949 테스트 수정이 제출된 head: `14ed93f9db30e2fa487967bf14cb1fd66f43dabd`.
아래 CUDA/MPS 결과는 2026-10-01 당시 증거다. 2026-10-04 manifest 출처/해시 검사 보완의
현재 HEAD에서 GPU smoke를 재실행한 증거로 해석하지 않는다. 해당 변경은 GPU 없는 소프트웨어
검사로 별도 검증하며 기존 학습 결과나 정확도를 새로 측정했다고 주장하지 않는다.
기존 [Mac 검증 기록](training_validation_2026-10-01.md)은 당시 MPS 검증 자료로 유지한다.

## 환경과 설치

- Windows x86_64, OS build 26200, 기본 파일 인코딩 CP949, Python UTF-8 모드 비활성.
- NVIDIA GeForce RTX 5070, VRAM 12227 MiB, 드라이버 617.14, compute capability 12.0.
- 별도 CPython 3.11.15 / torch 2.9.1+cu128 / torchvision 0.24.1+cu128.
- ultralytics 8.3.203 / numpy 2.2.6 / pytest 8.3.5 / Ruff 0.11.13.
- 사용자가 지정한 캡스톤 폴더 안에 저장소, `.python`, `work/uv-cache`, `work/temp`를 두었다.
  저장소의 `.venv`는 학습 환경, 상위 `work/ci-env`는 개발 의존성만 설치한 검사 환경이다.
- 기존 uv 0.11.6으로 Python 3.11과 가상환경을 준비하고, CUDA wheel을 먼저 설치한 뒤
  `requirements-dev.txt`와 `requirements-training.txt`를 설치했다.
  전역 Python·패키지·PATH·NVIDIA 드라이버는 변경하지 않았다.
- CUDA wheel은 [공식 2.9.1 설치 조합](https://pytorch.org/get-started/previous-versions/#v291)의 cu128이다.
  [Blackwell 지원 안내](https://pytorch.org/blog/pytorch-2-7/)를 확인했다.
  `nvidia-smi`의 드라이버 CUDA 표시는 PyTorch wheel의 CUDA runtime 버전과 별개다.

## 일반 검사와 수정

저장소 루트에서 개발 의존성만 설치한 환경으로 실행했다.
TEMP/TMP는 지정 폴더 안의 `work/temp`, 출력 인코딩만 `PYTHONIOENCODING=utf-8`로 설정했다.
파일의 기본 CP949와 Python UTF-8 모드 비활성 상태는 유지했다.

```powershell
..\work\ci-env\Scripts\python.exe -m tools.ci
..\work\ci-env\Scripts\python.exe -m pip check
```

- 수정 전: **154 passed / 3 failed / 1 skipped**.
  성공·학습 실패·데이터 수량 불일치의 세 기록 검사가 UTF-8 JSON을 CP949로 읽어 실패했다.
- 수정: `tests/test_training.py`의 학습 기록 읽기 두 곳에 UTF-8을 명시했다.
  기존 세 상태 검사의 출력 폴더를 한글 이름으로 바꾸고 이름의 보존을 확인한다.
  학습 도구는 이미 UTF-8로 기록하므로 런타임 코드는 변경하지 않았다.
- 수정 후: **157 passed / 1 skipped**. Ruff lint/format, 문서·설정·compile 검사 통과.
  건너뛴 한 검사는 별도로 실행하는 합성 학습 검사다.
- 개발 전용 환경과 CUDA 학습 환경 모두 `pip check`: 의존성 충돌 없음.

## CUDA 실행

torch가 RTX 5070과 `sm_120` 지원을 보고했다.
CUDA 행렬곱·역전파·동기화, torchvision CUDA NMS,
실제 torch를 사용하는 `auto`/`cuda` 장치 선택을 assert로 확인했다.

저장소 루트에서 학습 가상환경으로 실행했다.

```powershell
$env:RUN_TRAINING_SMOKE="1"
$env:TRAINING_SMOKE_DEVICE="cuda"
.venv\Scripts\python.exe -m pytest tests/test_training.py -k synthetic_training_smoke --basetemp=artifacts/windows-cuda-smoke-01 -q -s
```

- **1 passed / 23 deselected**, 23.23초. 합성 JPG 3장, train/val/test 각 1장.
- 패키지의 YOLO11n 구조와 임의 초기 가중치로 imgsz=64 / batch=1 / epochs=1 실행.
  모델 다운로드와 외부 학습 서비스는 사용하지 않았다.
- 실제 CUDA:0 학습·validation, train/val 이미지·객체 수 일치, validation 객체 1개 확인.
- `best.pt`, `last.pt`, `results.csv`, `args.yaml`, `training_record.json` 생성 확인.
  기록의 완료 상태·CUDA 장치·best 가중치 해시와 실제 파일을 대조했다.
- 학습 YAML의 test 제외, `test_evaluated=false`, `accuracy_verified=false`,
  `mps_blocking_copies=false` 확인.
- PyTorch pin_memory의 device 인자 폐기 예정 경고 24개가 발생했으며 학습은 완료됐다.
  고정한 vendor 패키지 내부 경고이므로 패키지나 런타임을 임의 변경하지 않았다.
- 전체 설치 버전, 원시 검사 로그, 합성 이미지와 가중치는 로컬 ignored `artifacts/`에 보존했다.

## 한계와 다음 작업

이 검증은 위 Windows·RTX 5070 조합의 실행 흐름 검증이다.
합성 데이터와 임의 초기 가중치의 정확도 수치는 지정 드론 인식 성능을 나타내지 않는다.
실제 지정 드론 데이터 학습·최종 test 평가, 장시간 학습·메모리 한계,
다른 GPU·드라이버, 카메라 FPS·추적·Serial·서보·펌웨어는 미검증이다.
이번 Windows 작업에서 Mac MPS 실기기를 재검증하지 않았다.
다음 작업은 검수된 실제 드론 데이터와 신뢰할 수 있는 로컬 가중치로 학습·평가하는 것이다.
