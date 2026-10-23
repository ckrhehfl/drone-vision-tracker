# 측정 기록에서 드론 YOLO 재학습까지

GUI 원본 사진 수집 → 사람의 정답 검수 → 촬영 그룹별 데이터 준비 → 기존 가중치 추가 학습 →
동일 validation 비교를 로컬에서 수행한다. 기존 [학습 도구](training.md)를 재사용하며,
수동 지정 ViT 추적기 학습은 이 파이프라인의 대상이 아니다.
예전 수치 JSON에는 원본 픽셀이 없으므로 그것만으로 재학습할 수 없다.

## 외부 동영상 가져오기

GUI의 **학습용 영상 가져오기**에서 로컬 MP4·MOV·AVI·MKV·WebM 등의 파일과 저장 폴더를 선택한다.
**영상 테스트**는 재생·추적·평가 전용이며 사진 추출이나 학습을 수행하지 않는다.
연결된 카메라가 없어도 사용할 수 있다. 별도 작업에서 기본 0.5초 간격으로 원본 크기 PNG를
추출하고, 새 `video-...` 폴더에 `samples.json`과 `annotations.example.json`을 저장한다.
추출 중 창을 종료하면 취소하며, 실패·취소된 부분 결과는 학습 준비에서 거부한다.
영상은 업로드하지 않고 기존 모델을 자동 학습·교체하지 않는다.

간격이나 출력 이름을 지정하려면 다음 명령을 사용한다. Windows에서는
`.venv/bin/python`을 `.\\venv\\Scripts\\python.exe`로 바꾼다.

```bash
.venv/bin/python -m tools.video_frames --video "/로컬/촬영 영상.mp4" --output data/learning/video-train --interval 0.5
```

기존 출력은 덮어쓰지 않는다. 원본 영상의 SHA-256과 디코더가 반환한 영상 시각을 보존한다.
FPS나 호스트 수신 시각으로 영상 시각을 만들지 않는다. 시각이 없거나 역행하는 파일,
빈 영상, 프레임 수 불일치, 이미지 저장 실패는 거부한다.
코덱 지원과 프레임 수 정보는 설치된 OpenCV 디코더에 달려 있다. 전체 프레임 수가 없거나
양의 정수가 아니면 완전성을 확인할 수 없어 가져오기를 거부한다. 실제 디코딩한 프레임 수가
영상 정보와 일치해야 완료로 기록한다. 추출 내용도 검수한다. 6,000장 한도를 넘으면 실패하므로
긴 영상은 간격을 늘리고 새 폴더로 추출한다. 검수·준비가 끝날 때까지 원본 영상을 보존한다.

추출된 **모든 프레임**을 아래 라벨 형식으로 검수하고 `annotations.json`에 저장한다.
박스는 원본 픽셀 좌표이며, 실제 빈 배경만 `null`로 지정한다.
`needs_label`을 배경으로 취급하거나 모델 예측을 그대로 정답으로 사용하지 않는다.

동영상용 [계획 템플릿](../config/retrain-video-plan.example.json)을 복사하고 추출 폴더 경로를 지정한다.
파일 경로는 계획 JSON 기준이다. 같은 원본 영상·이름만 바꾼 복사본·같은 촬영의 잘라낸 영상은
한 split에만 넣는다. 연결된 촬영은 같은 `capture_id`로 묶는다.
한 영상을 프레임별로 무작위 분할하지 않으며, 독립 train/val/test 촬영을 모두 준비한다.
기존 카메라 사진 source와 영상 source를 같은 계획에 함께 넣을 수도 있다.
라벨은 `reviewed`, 계획은 `reviewed`로 검수한 뒤 아래 3–5단계 명령을 그대로 사용한다.

준비된 manifest는 `source_type=video`, 원본 영상 경로·해시, 프레임 `timestamp_s`를 보존한다.
prepare 단계에서는 추출 기록의 원본 영상·이미지 해시와 라벨 검수 상태를 확인하고,
준비 중 원본·이미지·라벨 파일이 변경되면 거부한다.
준비 후 `tools.train --check-only`는 manifest의 이미지 해시와 라벨 형식을 검사한다.
원본 영상을 다시 해시하거나 승인 라벨의 기대 해시를 대조하지는 않으며,
`ready`가 원본·라벨 수정을 잠그는 기능은 아니다. 변경할 때는 새 버전으로 준비·재검수한다.
확정한 validation/test의 원본·정답은 유지하고 결과를 본 뒤 수정하지 않는다.
영상 입력에는 실시간 측정 기록을 만들지 않으며,
`training_cases.json`에 추적 실패·놓침을 임의로 집계하지 않는다.
Seraphim 1차 `best.pt`에서 CUDA/MPS 추가 학습하고 같은 validation으로 비교하는 흐름은 동일하다.

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

### GUI 라벨 편집기

1. 기존 프로그램의 **학습 · 평가 → 수동 라벨 편집기**를 연다. 기존 GUI와 같은 색상·버튼 스타일의
   편집 창이며, 창을 여는 것만으로 카메라·모델·학습을 실행하지 않는다.
2. **수집/추출 폴더 열기**에서 `samples.json`과 `frames/`가 있는 폴더를 선택한다.
   정상 종료된 카메라 수집 또는 영상 추출 폴더를 지원한다. 단순 사진 폴더의 출처·시각을 임의 생성하지 않는다.
   기존 `annotations.json`이 있으면 이어서 편집하고, 없으면 모든 이미지를 미라벨로 시작한다.
3. 이미지의 빈 영역을 드래그해 드론 박스를 그린다. 박스 안을 드래그하면 이동하고,
   네 모서리를 드래그하면 크기가 바뀐다. 원본 비율·여백을 반영해 원본 픽셀 좌표로 저장한다.
   Esc는 진행 중 드래그 취소, **실행 취소**는 최근 편집 되돌리기(최대 50회)다.
4. 드론이 없는 이미지는 **드론 없음 (배경)**을 눌러 `null`로 표시한다.
   **라벨 지우기 (미라벨)**는 `needs_label`로 되돌린다. 이전/다음·번호 이동·미라벨 찾기로 이동한다.
5. **임시 저장** 또는 Ctrl+S(macOS는 ⌘+S)로 `annotations.json`에 진행 상황을 저장한다.
   `status=needs_review`이며 미라벨이 남아 있어도 저장 가능하지만 재학습 준비에는 사용할 수 없다.
6. 모든 이미지를 직접 확인한 뒤 **검수 완료 저장**을 누른다. 미라벨이 없어야 하며 전체 이미지의
   해시·실제 크기를 다시 확인한 뒤 `status=reviewed`, `target_class=drone`으로 저장한다.
   이미지 수가 많으면 전체 원본 확인에 시간이 걸린다.

원본 이미지·수집 기록·라벨 초안은 수정하지 않는다. 라벨 저장은 임시 파일을 거쳐 교체하며,
열어 둔 라벨 JSON이나 수집 기록이 외부에서 바뀌면 덮어쓰기를 거부한다. 실패한 편집 내용은
메모리에 유지되므로 외부 변경을 확인한 뒤 재개한다. 파일을 다시 열기 전 필요한 변경은 따로 기록한다.
저장하지 않은 채 폴더를 바꾸거나 편집기/메인 프로그램을 닫으면 임시 저장·버리기·취소를 선택한다.
이미지 이동은 메모리의 라벨을 유지하며 자동 저장하지 않는다.

편집기는 이미지당 단일 드론 박스만 다룬다. 자동 예측을 정답으로 채우거나 학습을 실행하지 않으며,
촬영 그룹 분할은 아래 학습 창 또는 기존 계획에서 별도로 검수한다.

### GUI에서 수동 라벨로 추가 학습

1. **학습 · 평가 → 수동 라벨로 모델 학습**을 연다. 이 창을 여는 것만으로 모델·학습을 실행하지 않는다.
2. 라벨 편집기에서 **검수 완료 저장**한 수집/추출 폴더를 고른다. `samples.json`, `annotations.json`이
   필요하며 카메라 수집 폴더에는 같은 세션의 `measurement.json`도 있어야 한다.
   임시 저장·미라벨 상태는 추가할 수 없다. 실제 이미지·원본 영상의 해시와 측정 계약은 데이터 준비 때 다시 검사한다.
3. 각 폴더의 `train`, `val`, `test` 분할과 **촬영 그룹 이름**을 지정하고 **목록에 추가**한다.
   같은 촬영의 정지·재시작 기록이나 원본 영상·잘라낸 영상은 같은 그룹과 분할에 넣는다.
   독립 촬영 세 그룹을 모두 준비한다. 한 폴더의 이미지를 무작위로 나눠 holdout을 만들지 않는다.
   여러 폴더를 같은 그룹·분할로 추가할 수 있다. 잘못 추가했다면 선택 항목을 삭제하고 다시 추가한다.
4. 그룹과 분할 독립성을 직접 확인한 체크를 켜고 저장할 상위 폴더·데이터 버전 이름을 지정한다.
   **새 데이터 준비**는 새 `retrain-...` 작업 폴더에 검수한 `plan.json`과 `dataset/`을 만든다.
   기존 준비 도구를 사용해 검수한 모든 사진을 복사하고 YOLO 라벨로 변환하며, 원본 폴더는 수정하지 않는다.
   분할 누수·원본 변경·잘못된 라벨 등은 기존 검사에서 거부한다. 실패한 부분 결과도 보존한다.
5. **준비한 데이터 폴더 열기**로 복사된 `images/`, `labels/`와 `manifest.json`의 촬영 분할을 직접 확인한다.
   아직 `needs_review`이므로 학습할 수 없다. 확인 체크 후 **데이터 검수 완료**를 눌러야
   이미지 해시·YOLO 라벨·분할 무결성을 다시 검사하고 manifest를 `ready`로 원자 저장한다.
   검사 중 manifest가 바뀌거나 파일 교체에 실패하면 기존 파일을 덮어쓰지 않는다.
   GUI 체크를 해제하거나 목록을 변경하면 학습 잠금을 복원하며, 새 목록은 새 데이터 준비가 필요하다.
6. 신뢰하는 로컬 검출 `.pt`를 선택한다. 기존 Seraphim 1차 학습의 `weights/best.pt` 또는 검수한 baseline을 쓴다.
   장치와 학습 횟수·이미지 크기·배치를 지정하고 **학습 시작**을 누른다. 기본값은 epochs 100, imgsz 640, batch 4다.
   `cuda`/`mps`를 명시했는데 사용할 수 없으면 실패하며, `auto`만 CUDA → MPS → CPU 순으로 선택한다.
   기존 `tools.train`이 데이터를 다시 검사한 뒤 실행하며 test는 학습에 넘기지 않는다.
7. 각 학습은 별도 `train-.../`에 저장된다. **학습 결과 폴더 열기**에서 `weights/best.pt`와
   `training_record.json`을 확인한다. 추적 GUI의 모델 선택에서 결과를 직접 골라 사용한다.
   이전 가중치·준비본·학습 결과는 교체하지 않는다. 같은 validation 모델 비교는 아래 기존 명령을 사용한다.

화면 로그는 최근 약 50,000자이며 전체 출력은 작업 폴더의 번호별 `.log`에 남는다.
번호별 `.json`은 작업 종류·명령·완료/실패/중지와 종료 코드를 기록한다.
**작업 중지**와 창 종료 확인은 해당 프로세스를 종료하고 해제 완료를 기다린다.
중지·실패 결과는 자동 채택하지 않는다. 강제 중지한 학습의 내부 `training_record.json`은 `running`으로
남을 수 있으므로 GUI 작업 기록의 `cancelled`와 함께 확인하고 완료된 가중치로 취급하지 않는다.
추적·영상 가져오기를 정지한 뒤 학습 작업을 시작한다. 별도 프로세스는 기존 가상환경을 사용하며
필요한 학습 패키지가 없으면 로그에 오류를 표시한다. 자동 설치·다운로드·업로드는 하지 않는다.
이 동작은 라벨의 실제 정답 품질·드론 정확도·test 평가·실물 성능 완료를 뜻하지 않는다.
승인 후 데이터 파일을 수정할 때도 아래 기존 버전 준비·재검수 규칙을 지킨다.

### JSON 직접 편집과 라벨 형식

직접 JSON을 편집할 때는 각 수집 폴더에서 `annotations.example.json`을 `annotations.json`으로 복사한다.
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
영상에서 추출한 프레임은 위 동영상 가져오기 절차와 video manifest를 사용한다.
기존 출력 폴더를 덮어쓰지 않는다. 준비 중 실패한 폴더도 보존하므로 원인을 확인하고 새 버전을 쓴다.

## 4. 기존 YOLO 가중치에서 CUDA/MPS 추가 학습

아래 예제는 [공개 쿼드콥터 모델](../models/seraphim-yolov8n.md)의 설치 가중치를 사용한다.
직접 [Seraphim 1차 학습](training.md)을 했다면 `--model`과 비교의 `--baseline`을
그 실행의 `weights/best.pt`로 지정한다. 원본을 보존하고 검수 상태와 데이터부터 검사한다.

```bash
.venv/bin/python -m tools.train --data data/dataset/drone-retrain-v1/dataset.yaml --manifest data/dataset/drone-retrain-v1/manifest.json --check-only
.venv/bin/python -m tools.train --data data/dataset/drone-retrain-v1/dataset.yaml --manifest data/dataset/drone-retrain-v1/manifest.json --model models/local/seraphim-yolov8n/best.pt --device mps --epochs 100 --imgsz 640 --batch 4 --output runs/train/drone-retrain-v1-mps
```

기존 가중치로 새 학습을 시작한다. 결과는 별도 run의 `weights/best.pt`와 `training_record.json`에 남는다.
MPS를 명시했을 때 사용할 수 없으면 실패한다. CPU 시험은 명시적으로 `--device cpu`를 사용한다.
Windows CUDA 설치·PowerShell 명령은 [Windows 안내](windows-cuda.md)를 따른다.
기존 모델·자료를 자동 다운로드·교체하지 않는다. test는 무결성 검사에만 쓰고 학습에 넘기지 않는다.
반복 학습 시 기존 조건의 사진도 train에 포함해 검출 성능 유지 여부를 고정 validation에서 확인한다.

## 5. 같은 validation에서 이전/새 모델 비교

```bash
.venv/bin/python -m tools.retrain compare --data data/dataset/drone-retrain-v1/dataset.yaml --manifest data/dataset/drone-retrain-v1/manifest.json --baseline models/local/seraphim-yolov8n/best.pt --candidate runs/train/drone-retrain-v1-mps/weights/best.pt --output runs/compare/drone-retrain-v1 --imgsz 640 --batch 4
```

비교 장치 기본값은 `auto`이며 CUDA → MPS → CPU 순으로 선택한다.
GPU 검증에는 `--device cuda` 또는 `--device mps`를 명시하며 사용할 수 없으면 실패한다.
두 모델 모두 클래스가 정확히 `0: drone`인 검출 가중치여야 한다.
같은 validation·해상도·batch·장치로 precision, recall, mAP50, mAP50–95와 변화량을
`comparison.json`에 남긴다. 데이터/가중치 해시, 요청·선택 장치와 완료·실패 상태도 기록한다.
런타임 준비·장치 선택 실패도 실패 원인과 함께 기록하며 기존 출력 폴더를 덮어쓰지 않는다.
MPS validation에는 기존 학습의 동기 배치 전송 보완을 적용하고 실제 읽은 이미지·객체 수를 확인한다.
독립 test는 비교에 넘기지 않는다. 후보를 자동 승인·배포하지 않는다.

검출 mAP 향상이 빠른 이동·재진입 추적 개선을 보장하지 않는다. 후보를 GUI에서 직접 선택해
같은 시험 조건으로 속도·오인식·추적을 확인한다. 전체 수신 프레임 기준
[추적 박스 평가](tracking-metrics.md)는 **모든 프레임**의 정답이 필요하다. 샘플 사진 라벨만으로
그 평가의 정답을 대체할 수 없다. 최종 test와 실제 드론 품질 검증은 별도로 수행한다.
