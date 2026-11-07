#!/bin/zsh
cd "${0:A:h}" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  print 'Python 환경이 없습니다. docs/camera-gui.md의 설치 안내를 확인하세요.'
  read '?Enter를 누르면 닫힙니다.'
  exit 1
fi
.venv/bin/python -m tools.servo0_manual_gui
if [[ $? -ne 0 ]]; then
  read '?실행 오류: Enter를 누르면 닫힙니다.'
fi
