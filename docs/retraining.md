# 측정 기록에서 드론 YOLO 재학습까지

GUI 원본 사진 수집 → 사람의 정답 검수 → 촬영 그룹별 데이터 준비 → 기존 가중치 추가 학습 →
동일 validation 비교를 로컬에서 수행한다. 기존 [학습 도구](training.md)를 재사용하며,
수동 지정 ViT 추적기 학습은 이 파이프라인의 대상이 아니다.
예전 수치 JSON에는 원본 픽셀이 없으므로 그것만으로 재학습할 수 없다.

## 1. 원본 사진과 측정 수집

기존 GUI를 종료하고 실행 파일을 다시 연다. 카메라를 시작한 뒤 **학습 프레임 수집**을 누르고
로컬 폴더를 선택한다. 기본 수집은 꺼져 있다. 선택한 폴더에 새 `learning-<session_id>`가 생긴다.
정지·종료 후 카메라와 추적 작업이 끝나면 다음 자료가 남는다.

| 파일 | 내용 |
| --- | --- |
| `frames/frame-NNNNNN.png` | 화면 크롭·박스 오버레이 이전 원본 해상도 PNG |
| `samples.json` | 저장된 frame_id, 수신 시각, 원본 크기, 이미지 SHA-256, 저장 오류·큐 누락 |
| `measurement.json` | 같은 세션의 전체 수신 프레임 수치·예측 기록. 수집을 선택하면 자동 저장 |
| `annotations.example.json` | 저장된 사진의 라벨 초안. `needs_label`은 정답이 아님 |

0.5초마다 최대 한 장을 별도 쓰기 스레드로 저장하며 디스크 대기열은 두 장으로 제한한다.
저장이 느리면 수집 사진을 건너뛰고 누락 수를 표시한다. 카메라 드라이버 누락 수는 아니다.
빠른 이동·재진입 순간을 모두 보존하는 연속 녹화가 아니므로 실패 사진이 실제 저장됐는지 확인한다.
원본 PNG는 용량이 클 수 있다. 짧은 시험을 수집하고 선택한 로컬 폴더의 여유 공간을 확인한다.
사진·수치·가중치는 자동 업로드하지 않는다.

측정의 첫 6,000개 수신 프레임에 속하는 사진만 저장한다. 수신 30FPS이면 약 200초 이내에
정지해야 한다. 한도 초과, 입력/추적 오류, 사진 저장 실패 기록은 데이터 준비에서 거부한다.
강제 프로세스 종료·전원 차단은 정상 종료와 다르며 수집 완료를 보장하지 않는다.

## 2. 사람이 라벨과 촬영 그룹 검수

각 수집 폴더에서 `annotations.example.json`을 `annotations.json`으로 복사한다.
**저장된 모든 사진**을 확인해 원본 픽셀 좌표 `[x1,y1,x2,y2]`로 박스를 작성한다.
드론이 실제로 없는 사진은 `null`, 미라벨링은 계속 `needs_label`로 둔다. 미라벨링 사진은 거부된다.
현재 단일 대상 시험과 같이 사진당 드론 박스 한 개를 지원한다.
인물로 수동 추적한 시험을 드론 정답으로 바꾸면 안 된다. 예측 박스·품질 점수도 정답이 아니다.
검수 완료 후에만 `status`를 `reviewed`, `target_class`를 `drone`으로 설정한다.

```json
{
  "schema_version": 1,
  "session_id": "samples.json과 같은 값",
  "status": "reviewed",
  "target_class": "drone",
  "frames": [
    {"frame_id": 1, "box": [100, 80, 140, 110]},
    {"frame_id": 16, "box": null}
  ]
}
```

[계획 템플릿](../config/retrain-plan.example.json)을 복사해 `record`, `samples`, `annotations` 경로를
실제 수집 파일로 바꾼다. 경로는 **계획 JSON 위치 기준**이며 절대 경로도 가능하다.
서로 독립적인 train/val/test 촬영을 먼저 정한다. 같은 촬영을 잠깐 정지·재시작한 기록들은
같은 `capture_id`로 묶어 같은 split에만 배정한다. 각 split에 드론 사진이 최소 한 장 있어야 한다.
동일 내용의 split 간 중복은 자동 거부하고, 비슷한 연속 사진·촬영 조건·드론 정체성은 사람이 검수한다.
분할을 확인한 뒤 계획의 `status`를 `reviewed`로 바꾼다. 실패 사례를 본 후 holdout 사진을
학습용으로 옮기면 검증·시험의 독립성을 잃는다.

## 3. 새 데이터 버전 준비

기존 macOS 가상환경에서 실행한다. `data/learning/plan.json`은 위에서 작성한 계획이다.

```bash
.venv/bin/python -m tools.retrain prepare --plan data/learning/plan.json --output data/dataset/drone-retrain-v1 --version drone-retrain-v1
```

세션·frame_id·시각·실제 사진 크기·이미지 해시·라벨 범위를 검사한 뒤 원본을 복사하고
정답을 정규화 YOLO 라벨로 변환한다. **검수한 저장 사진 전체**를 포함한다.
`training_cases.json`은 train 사진에만 미처리·놓침·위치 오류·배경 오인식·일치 등을 표시한다.
검증·시험 사진에서 학습 사례를 고르지 않고 예측을 정답으로 쓰지 않는다.

새 `manifest.json`은 `needs_review`다. 복사된 이미지와 라벨, 촬영 분할을 확인한 뒤에만
`ready`로 바꾼다. 측정/수집/정답/계획의 SHA-256과 원본 출처를 manifest에 보존한다.
카메라에서 직접 저장한 사진은 `source_type=photos`이고 `capture_elapsed_s`는 호스트 수신 시각이다.
영상에서 추출한 프레임의 원본 영상 SHA·PTS를 대신하지 않는다. 그런 자료는 기존 video manifest를 사용한다.
기존 출력 폴더를 덮어쓰지 않는다. 준비 중 실패한 폴더도 보존하므로 원인을 확인하고 새 버전을 쓴다.

## 4. 기존 YOLO 가중치에서 MPS 추가 학습

첫 실제 드론 사진 추가 학습에는 [Seraphim 1차 학습](training.md)의
`runs/train/seraphim-v1/weights/best.pt`를 `models/local/baseline.pt`에 복사해 사용한다.
복사본의 SHA-256이 원본과 같은지 확인하고 검수 상태와 데이터부터 검사한다.

```bash
.venv/bin/python -m tools.train --data data/dataset/drone-retrain-v1/dataset.yaml --manifest data/dataset/drone-retrain-v1/manifest.json --check-only
.venv/bin/python -m tools.train --data data/dataset/drone-retrain-v1/dataset.yaml --manifest data/dataset/drone-retrain-v1/manifest.json --model models/local/baseline.pt --device mps --epochs 100 --imgsz 640 --batch 4 --output runs/train/drone-retrain-v1-mps
```

기존 가중치로 새 학습을 시작한다. 결과는 별도 run의 `weights/best.pt`와 `training_record.json`에 남는다.
MPS를 명시했을 때 사용할 수 없으면 실패한다. CPU 시험은 명시적으로 `--device cpu`를 사용한다.
기존 모델·자료를 자동 다운로드·교체하지 않는다. test는 무결성 검사에만 쓰고 학습에 넘기지 않는다.
반복 학습 시 기존 조건의 사진도 train에 포함해 검출 성능 유지 여부를 고정 validation에서 확인한다.

## 5. 같은 validation에서 이전/새 모델 비교

```bash
.venv/bin/python -m tools.retrain compare --data data/dataset/drone-retrain-v1/dataset.yaml --manifest data/dataset/drone-retrain-v1/manifest.json --baseline models/local/baseline.pt --candidate runs/train/drone-retrain-v1-mps/weights/best.pt --output runs/compare/drone-retrain-v1 --imgsz 640 --batch 4
```

비교 장치 기본값은 MPS다. 두 모델 모두 클래스가 정확히 `0: drone`인 검출 가중치여야 한다.
같은 validation·해상도·batch·장치로 precision, recall, mAP50, mAP50–95와 변화량을
`comparison.json`에 남긴다. 데이터/가중치 해시, 요청·선택 장치와 완료·실패 상태도 기록한다.
런타임 준비·장치 선택 실패도 실패 원인과 함께 기록하며 기존 출력 폴더를 덮어쓰지 않는다.
MPS validation에는 기존 학습의 동기 배치 전송 보완을 적용하고 실제 읽은 이미지·객체 수를 확인한다.
독립 test는 비교에 넘기지 않는다. 후보를 자동 승인·배포하지 않는다.

검출 mAP 향상이 빠른 이동·재진입 추적 개선을 보장하지 않는다. 후보를 GUI에서 직접 선택해
같은 시험 조건으로 속도·오인식·추적을 확인한다. 전체 수신 프레임 기준
[추적 박스 평가](tracking-metrics.md)는 **모든 프레임**의 정답이 필요하다. 샘플 사진 라벨만으로
그 평가의 정답을 대체할 수 없다. 최종 test와 실제 드론 품질 검증은 별도로 수행한다.
