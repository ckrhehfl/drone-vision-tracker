# 공개 Seraphim 기반 쿼드콥터 검출 모델

2026-10-15 현재 Mac에는 `models/local/seraphim-yolov8n/best.pt`가 설치되어 있다.
가중치와 설치 기록 `source.json`은 Git에 포함하지 않는다. 다른 PC에서는 기존 학습 환경에서
다음 명령을 한 번 실행한다. GUI가 시작될 때 모델을 다운로드하지 않는다.

```bash
.venv/bin/python -m tools.pretrained_drone --download
```

원본이 로컬 캐시에 있으면 `--download` 없이 실행할 수 있다. 다운로드는 약 12.4MB다.
기존 설치가 정상이라면 재사용하고, 원본 해시 불일치·다른 기존 설치·남은 `.part` 파일은
덮어쓰지 않는다. 실패한 자료를 확인·보존한 뒤 다시 준비한다.

## 출처와 변환

- 원본: [TomSmail/drone-yolo-v1](https://huggingface.co/TomSmail/drone-yolo-v1)
- 작성자 표기: FRAID Labs / TomSmail
- 고정 revision: `a76fa3c8b60cb7861f8b3a90017340d1069afb9c`
- 원본 `best.pt` SHA-256: `bf24a20e69b28896a0c7e4855c72146149fa5c25845fa5e54beda2b93cf79824`
- 원본 구조: YOLOv8n, 클래스 `0: quadcopter`, `1: fixed-wing`, 입력 640
- 작성자 설명: Seraphim 쿼드콥터 사진 9,000장과 별도 고정익 사진 9,000장으로 학습한 모델.
  [Seraphim 전체 데이터](https://huggingface.co/datasets/lgrzybowski/seraphim-drone-detection-dataset)를
  이 프로젝트에서 직접 학습한 결과가 아니다. 원본의 학습 조건·분할·성능은 작성자의 설명이며
  독립적으로 재현·검증하지 않았다.

설치 도구는 원본 SHA-256을 확인한 뒤 클래스 0의 **학습된 분류 가중치와 박스 회귀를 보존**하고
다른 클래스 출력 채널을 제거한다. 남은 클래스를 `0: drone`으로 명명한다.
원본의 2클래스 optimizer·성능 기록·loss 캐시는 파생 체크포인트에 이월하지 않는다.
원본 캐시는 보존하며 `source.json`에 원본 revision/해시, 변환 내용, 설치된 가중치 해시를 기록한다.
새 학습·resume·모델 병합은 실행하지 않는다. 다른 클래스의 학습 영향까지 제거한다는 뜻은 아니다.

작성자는 원본 모델과 Seraphim에 CC-BY-4.0을 표기한다. 출처와 변경 내용을 위에 기록했다.
실행·저장에는 기존 Ultralytics를 사용하며 그 도구의 라이선스와 공개 배포 검토는
[학습 안내](../docs/training.md)를 따른다. 저장소 전체의 라이선스를 변경한 것이 아니다.

## GUI에서 사용

GUI를 종료하고 `드론 카메라 실행.command`로 다시 연다. 설치 기록과 가중치 해시가 일치하면
기본값이 `YOLO 자동 검출`, 해당 `best.pt`, 임계값 `0.25`가 된다. 카메라는 시작 버튼으로 켠다.
설치가 없거나 손상됐다면 기존 수동 지정으로 시작한다. 인물 시험은 `수동 지정`을 선택한다.
방식을 바꾸면 수동 임계값은 `0.65`, YOLO는 `0.25`로 설정되며 사용자가 다시 조정할 수 있다.
가중치가 학습한 대상은 쿼드콥터 범주이며 특정 드론 개체의 신원 판별 모델이 아니다.

## 나중에 추가 학습

검수한 촬영 자료가 준비되면 [재학습 안내](../docs/retraining.md)에 따라 새 데이터 버전을 만든다.
초기 `--model`에 설치된 `best.pt`를 지정하고 새로운 run 폴더로 학습한다.
원본 모델을 덮어쓰지 않으며, 같은 validation에서 설치 모델과 새 모델을 비교할 수 있다.
GUI의 `best.pt 선택`으로 새 가중치를 사용한다. 다음 GUI 실행 기본값은 설치 모델이므로
추가 학습본을 쓰려면 해당 파일을 다시 선택한다.

```bash
.venv/bin/python -m tools.train --data data/dataset/drone-retrain-v1/dataset.yaml --manifest data/dataset/drone-retrain-v1/manifest.json --model models/local/seraphim-yolov8n/best.pt --device mps --epochs 100 --imgsz 640 --batch 4 --output runs/train/drone-retrain-v1-mps
```

검수 전 자료와 test를 학습에 사용하는 기존 제한은 유지한다. 모델 설치는 정확도 검증이 아니다.
공개 원본의 mAP를 변환 모델·실제 Mac 카메라·지정 드론의 성능으로 표시하지 않는다.
원거리 작은 기체·빠른 이동·가림·조명·재진입과 실제 카메라 FPS는 실물 검증이 필요하다.
