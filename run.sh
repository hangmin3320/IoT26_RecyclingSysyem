#!/usr/bin/env bash
#
# run.sh — MediaMTX(영상 서버) + Flask 앱을 한 번에 실행하는 개발용 런처.
#
# 사용법:
#   ./run.sh                         # 일반 실행
#   MOCK_MODE=True ./run.sh          # 하드웨어 없이 테스트
#   RESET_TOKEN=secret ./run.sh      # 환경변수는 앞에 붙여서 전달
#
# Ctrl+C 한 번으로 MediaMTX 와 앱이 함께 종료된다.
# (자동 실행/상시 가동은 systemd 권장 — README §6 참고)

set -euo pipefail
cd "$(dirname "$0")"

MEDIAMTX_BIN="./mediamtx/mediamtx"
MEDIAMTX_CFG="./mediamtx/mediamtx.yml"
MM_PID=""

cleanup() {
  echo
  echo "[run.sh] 종료 중..."
  if [ -n "$MM_PID" ] && kill -0 "$MM_PID" 2>/dev/null; then
    kill "$MM_PID" 2>/dev/null || true
    wait "$MM_PID" 2>/dev/null || true
  fi
  echo "[run.sh] 정리 완료."
}
trap cleanup EXIT INT TERM

# --- MediaMTX (백그라운드) ---
if [ -x "$MEDIAMTX_BIN" ]; then
  echo "[run.sh] MediaMTX 시작 (로그: /tmp/mediamtx.log)"
  "$MEDIAMTX_BIN" "$MEDIAMTX_CFG" >/tmp/mediamtx.log 2>&1 &
  MM_PID=$!
  sleep 1
  if ! kill -0 "$MM_PID" 2>/dev/null; then
    echo "[run.sh] 경고: MediaMTX 가 바로 종료됨 — /tmp/mediamtx.log 확인"
    MM_PID=""
  fi
else
  echo "[run.sh] 경고: MediaMTX 바이너리 없음 ($MEDIAMTX_BIN) — 스트림 없이 진행"
fi

# --- Flask 앱 (포그라운드; venv 있으면 우선 사용) ---
PYTHON="${PYTHON:-python3}"
[ -x ".venv/bin/python" ] && PYTHON=".venv/bin/python"

echo "[run.sh] 앱 시작: $PYTHON app.py   (Ctrl+C 로 전체 종료)"
echo "[run.sh] 대시보드: http://$(hostname -I | awk '{print $1}'):${FLASK_PORT:-5000}"
"$PYTHON" app.py
