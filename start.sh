#!/usr/bin/env bash
# 맥·리눅스에서 처음 써보기:  bash start.sh   → 브라우저에서 http://127.0.0.1:8080
set -e
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "python3 가 필요합니다 (https://www.python.org/downloads/)"; exit 1; }
if [ ! -x .venv/bin/python ]; then
  echo "[1/3] 실행 환경 만드는 중..."
  python3 -m venv .venv
fi
PY=.venv/bin/python
if [ ! -f .venv/installed.txt ]; then
  echo "[2/3] 필요한 프로그램 설치 중... (몇 분 걸립니다)"
  "$PY" -m pip install -q --upgrade pip
  "$PY" -m pip install -q -r requirements.txt
  echo "[3/3] 크롬 브라우저 받는 중..."
  "$PY" -m playwright install chromium
  echo ok > .venv/installed.txt
fi
mkdir -p data
echo
echo " 관리 화면: http://127.0.0.1:8080   (Ctrl+C 로 종료)"
command -v tesseract >/dev/null || echo " 화면 글자 인식(OCR)을 쓰려면 tesseract 를 설치하세요 (README 참고)."
echo
( sleep 4; (command -v open >/dev/null && open http://127.0.0.1:8080) || (command -v xdg-open >/dev/null && xdg-open http://127.0.0.1:8080) || true ) >/dev/null 2>&1 &
exec "$PY" -m webmacro panel data/config.yaml
