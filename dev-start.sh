#!/usr/bin/env bash
# Start the live loop + FastAPI dashboard with one command.
# Loop runs in the background under caffeinate (Mac doesn't sleep).
# Dashboard runs in the foreground — Ctrl+C stops everything.
#
# Usage: ./dev-start.sh
# Loop logs:   tail -f /tmp/traderbot-loop.log
# Dashboard:   http://localhost:8000

set -euo pipefail

cd "$(dirname "$0")"

LOG_FILE="${TRADERBOT_LOOP_LOG:-/tmp/traderbot-loop.log}"
PORT="${TRADERBOT_PORT:-8000}"

echo "================================================================"
echo "   traderbot — dev start (paper mode)"
echo "================================================================"
echo "  Project:    $(pwd)"
echo "  Loop log:   $LOG_FILE"
echo "  Dashboard:  http://localhost:$PORT"
echo "================================================================"

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | grep -q LISTEN; then
  echo
  echo "  ⚠  Port $PORT already in use — is the dashboard already running?"
  echo "     Stop it (Ctrl+C in that terminal) or set TRADERBOT_PORT to a free port."
  exit 1
fi

LOOP_PID=""
cleanup() {
  if [[ -n "$LOOP_PID" ]] && kill -0 "$LOOP_PID" 2>/dev/null; then
    echo
    echo "→ Stopping live loop (PID $LOOP_PID)..."
    kill "$LOOP_PID" 2>/dev/null || true
    wait "$LOOP_PID" 2>/dev/null || true
  fi
  echo "→ Bye."
}
trap cleanup EXIT INT TERM

echo
echo "→ Starting live_loop in background (caffeinate keeps Mac awake)..."
caffeinate -di uv run python -m workers.live_loop > "$LOG_FILE" 2>&1 &
LOOP_PID=$!
echo "  PID: $LOOP_PID"

sleep 2

if ! kill -0 "$LOOP_PID" 2>/dev/null; then
  echo
  echo "  ⚠  live_loop died on startup. Last 20 lines of $LOG_FILE:"
  tail -n 20 "$LOG_FILE" || true
  exit 1
fi

echo
echo "→ Starting FastAPI dashboard on port $PORT..."
echo "  Open http://localhost:$PORT in your browser."
echo "  Press Ctrl+C to stop both processes."
echo

uv run uvicorn web.main:app --host 127.0.0.1 --port "$PORT"
