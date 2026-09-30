# 데이터 보관 영역

권장 로컬 구조: raw/ (촬영 세션 원본), extracted/ (프레임 후보), dataset/ (train·val·test), manifests/ (버전·split 기록). 영상·사진·개인정보·대용량 가중치는 기본적으로 Git에 넣지 않는다. 공개 범위와 라이선스를 먼저 정한다.

세션과 split을 먼저 정하고 파생 이미지가 이를 계승하도록 한다. data/manifests/에 실제 경로를 기록할 때 개인 PC 절대 경로와 개인정보 노출을 피한다.
