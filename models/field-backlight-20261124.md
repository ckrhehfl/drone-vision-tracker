# 현장 추가 학습 드론 검출 모델

스프링클러 오검출과 상하 방향 변화가 나타난 영상을 위해 2026-11-24에 추가 학습한
단일 `drone` 클래스 YOLOv8n 가중치다.

## 사용

팀에서 이미 준비한 파일을 저장소의
`models/local/field-backlight-20261124/field-backlight-20261124.pt` 위치에 둔다.
가중치 SHA-256은 `642b84816bc0394156ae0c5b638d30584146f698ff76e6662f7f480b132317dc`다.
파일이 일치하면 GUI를 다시 열 때 이 모델과 **YOLO 자동 검출**, 임계값 **0.25**가 선택된다.
파일이 없거나 해시가 다르면 [기존 공개 모델](seraphim-yolov8n.md) 설치를 확인한다.
가중치는 로컬에 보관하며 GUI는 다운로드 없이 시작한다.

## 학습 출처

- 초기 모델은 FRAID Labs / TomSmail의 [drone-yolo-v1](https://huggingface.co/TomSmail/drone-yolo-v1/tree/a76fa3c8b60cb7861f8b3a90017340d1069afb9c)
  단일 클래스 변환본을 추가 학습한 모델에서 이어받았다. 이번 시작 가중치 SHA-256은
  `a71738e6c28430d2c441dd997e50e360c30d0ab681276b53d924b179d9cb6b3f`다.
- 공개 자료는 Łukasz Grzybowski / Seraphim Defence Systems의 [Seraphim](https://huggingface.co/datasets/lgrzybowski/seraphim-drone-detection-dataset/tree/5b1c8348bf67e6836e3dbc42b053a5a4bfdec925)이다.
  원본 모델과 데이터 카드에는 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)이 기재되어 있다.
- 학습 입력 670장은 기존 검수 프레임 400장, 기존 공개 이미지 112장, 밝은 배경 56장,
  모의 역광 56장, 공개 배경 크롭 22장, 새 스프링클러 배경에서 파생한 24장이다.
  입력 크기 640, 3 epoch, 상하 반전 확률 0.25로 추가 학습했다.
  학습 데이터 기록 SHA-256은 `6957395555830fde41f1ad6f42f5dfcf8039070bdfdd75e810a4e37d050372d0`다.
  [Ultralytics 라이선스](https://www.ultralytics.com/license)는 공개 배포 전에 확인한다.

## 저장 프레임 비교

| 비교 항목 | 이전 로컬 모델 | 현장 추가 학습 |
| --- | ---: | ---: |
| 스프링클러 주변 크롭 오검출, 화면 방향 보정, 5장 | 3장 | 0장 |
| 손에 든 드론 검출, 정상 방향, 23장 | 17장 | 18장 |
| 손에 든 드론 검출, 상하 반전, 23장 | 11장 | 15장 |
| 공개 검증 이미지 144장, AP50 | 90.06% | 90.01% |
| 밝은 야외 공개 이미지 21장, 재현율 | 95.4% | 90.9% |

스프링클러 비교 사진은 학습에 쓴 사진과 다른 촬영에서 가져왔다. 손에 든 드론 결과는
한 영상의 표본 프레임에서 신뢰도 0.25와 근사 박스 IoU ≥ 0.5를 적용한 값이다.
실제 야외 역광·비행 중 검출 성능과 모터 추종은 미검증이다.
