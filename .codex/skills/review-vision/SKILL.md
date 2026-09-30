---
name: review-vision
description: 드론 영상 입력·검출·추적 변경에서 좌표·시각·대상 선택·소실 오류를 읽기 전용으로 검토한다.
---

[리뷰 계약](../review-orchestrator/SKILL.md)을 따른다. 구현하거나 수정하지 않는다.
base/head의 실제 코드와 테스트에서 다음 실패 조건을 확인한다.

- 입력 실패·EOF·재연결의 자원 해제와 frame lifecycle, 재연결 후 상태 초기화.
- 원본/resize/letterbox 좌표 복원, bbox 범위와 중심, 중복 변환.
- frame_id·단조 시각·영상 시각·dt의 일관성, 최신 프레임 슬롯과 지연 누적.
- confidence 경계값·NaN·빈 검출, 일반 가중치를 지정 드론 모델로 오인하는 경로.
- 실제 observation과 prediction 구분, stale/누락 시 새 이동 중단.
- 단일 대상 유지, tracking loss·reacquisition·잘못된 target 전환.
- 촬영 세션별 분할, 파생 데이터 누수, test를 이용한 튜닝.

재현 조건과 관련 위치를 가진 JSON finding을 빠짐없이 반환한다.
실제 카메라 FPS·지정 드론 detection 품질은 미측정으로 남기고 필요한 관찰 방법을 적는다.
