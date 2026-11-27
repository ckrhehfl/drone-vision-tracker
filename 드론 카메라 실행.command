#!/bin/zsh
cd "${0:A:h}" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  print 'Python 환경이 없습니다. docs/camera-gui.md의 설치 안내를 확인하세요.'
  [[ -t 0 ]] && read '?Enter를 누르면 닫힙니다.'
  exit 1
fi
if .venv/bin/python -m tools.camera_gui; then
  exit 0
else
  print '실행 오류: docs/camera-gui.md의 환경 및 카메라 권한 안내를 확인하세요.'
  [[ -t 0 ]] && read '?Enter를 누르면 닫힙니다.'
  exit 1
fi
