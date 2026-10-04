# 테스트 — 자동화 기반 / 드론 기능은 구현 전

대상: 원본/리사이즈 좌표, bbox 중심, dt와 프레임 최신성, 단일 대상 선정, 소실·재획득·PAUSED·FAULT, 명령 범위, 세션·순서, partial line·overflow, watchdog, 재연결, 데이터 세션 분할 누수.

문서 검사 스크립트는 tools/check_design_package.py에 있다. 이것을 프로젝트의 추종 기능 테스트로 집계하지 않는다. 앞으로 만든 테스트는 카메라·GPU·실물 모터 없이 실행 가능한 것과 현장 감독이 필요한 것을 구분한다.

현재 pytest는 설정의 안전 기본값·잘못된 입력, 리뷰 JSON·SHA·scope 누락,
CI/PR 상태·권한 경계, Arduino compile 누락 감지를 검사한다.
학습 도구의 데이터·세션 누수·라벨·장치 선택·실패 기록 검사도 포함한다.
합성 데이터 GPU 학습 검사는 명시적으로 선택할 때만 실행한다.
실행 방법은 [학습 안내](../docs/training.md)를 참조한다.
`python -m tools.ci`로 전체 검사, `python -m pytest`로 테스트만 실행한다.
