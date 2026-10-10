# 수동 지정용 범용 ViT 추적 모델

수동 GUI에서 지정한 영역을 추적하는 OpenCV Zoo의 사전학습 모델이다.
드론 클래스 검출·지정 드론 전이학습·사람의 신원 인식 모델이 아니다.
실제 드론/인물 정확도와 원거리 성능은 별도 실물 시험이 필요하다.

- 원본: [OpenCV Zoo VitTrack](https://github.com/opencv/opencv_zoo/tree/47534e27c9851bb1128ccc0102f1145e27f23f98/models/object_tracking_vittrack)
- 기여자: Pengyu Liu, GSoC 2023
- 파일: `object_tracking_vittrack_2023sep.onnx`, 714,726 bytes
- SHA-256: `2990f0b7cd44d92afa48cd97db6de7be113fc1d9594fddb74e2725c10478e91d`
- 파일의 원본 라이선스: [Apache-2.0](https://github.com/opencv/opencv_zoo/blob/47534e27c9851bb1128ccc0102f1145e27f23f98/models/object_tracking_vittrack/LICENSE)
- 로컬 위치: `models/local/vittrack/` (가중치·LICENSE·source.json은 Git 제외)

현재 맥에는 위 해시로 확인한 원본 파일과 LICENSE를 로컬에 준비했다. GUI는 외부 다운로드 없이
기존 파일만 읽으며 해시가 다르면 실행을 중단한다. 파일이 없으면 특징점/템플릿 방식으로
전환하고 입력 정보에 `ViT 모델 없음`을 표시한다.

GUI 장치 `mps`에서는 이 ONNX의 같은 가중치를 `onnx2torch==1.5.15`로 메모리에서 변환하고,
고정 템플릿 128×128·검색 256×256 입력으로 최적화해 `torch==2.9.1` MPS에서 추론한다.
최적화한 모델 하나를 같은 프로세스에서 재사용한다. 반복 시작 때 다시 추적/동결하면 MPS의
잔여 할당이 누적되는 것을 확인했으며, 대상 템플릿·위치·점수는 추적기마다 분리한다.
재사용 전에도 GUI의 원본 파일 해시 검사는 유지한다.
`onnx==1.17.0`·`protobuf==5.29.6`을 macOS 잠금 파일에 고정했다. 가중치나 영상은 새로 저장하지 않는다.
전처리의 BGR 채널·정규화·패딩과 Hann 창·좌표 복원은
[고정 OpenCV 소스](https://github.com/opencv/opencv/blob/5.0.0/modules/video/src/tracking/tracker_vit.cpp)를 따른다.
정규화 배율도 해당 소스의 `1 / Scalar` 연산을 그대로 재현한다. 이는 채널별 역수와 다르며
[Scalar 정의](https://github.com/opencv/opencv/blob/5.0.0/modules/core/include/opencv2/core/types.hpp)의
켤레/제곱합을 사용한다. 장치 변경으로 입력·품질 점수가 달라지지 않도록 기존 동작을 보존한다.
`cpu`를 선택하면 기존 OpenCV ViT를 사용한다. 특징점/재검색은 CPU에 남는다.
MPS 사용 불가·추론 오류는 표시하고 정지한다. 전체 추적의 속도·정확도는 실물 시험 전이다.

다른 PC에서 같은 모델을 준비하려면 원본 LICENSE와 파일을 위 로컬 위치에 보관한다.
해시를 확인하지 않은 파일을 다른 이름으로 바꾸어 검사를 우회하지 않는다.
프로젝트 자체의 라이선스나 가중치 공개 배포 승인은 이 문서에서 변경하지 않는다.
