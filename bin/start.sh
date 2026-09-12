#!/bin/sh
# EOS UI Startup Script
# Usage: bin/start.sh [PORT]
#
# Picks a free port, finds the server entrypoint under eos/ui/ (server.py /
# app.py / main.py / gui_server.py) and starts it with nohup. PID/port info
# is written to .eos-ui.pid; the browser opens automatically when possible.
#
# If there is no server entrypoint, it exits with a clear error; no process
# is started.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
UI_DIR="$PROJECT_DIR/ui"
PID_FILE="$PROJECT_DIR/.eos-ui.pid"
LOG_FILE="$PROJECT_DIR/.eos-ui.log"

# --- Pick a free port --------------------------------------------------------
get_free_port() {
  python3 -c "import socket; s = socket.socket(); s.bind(('', 0)); print(s.getsockname()[1]); s.close()"
}

port_is_free() {
  python3 -c "import socket,sys; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(('', int(sys.argv[1]))); s.close(); sys.exit(0)
except OSError:
    sys.exit(1)" "$1"
}

# --- Collision guard: is our own process already running? -------------------
if [ -f "$PID_FILE" ]; then
  OLD_PID=$(sed -n '1p' "$PID_FILE")
  OLD_PORT=$(sed -n '2p' "$PID_FILE")
  if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" >/dev/null 2>&1; then
    # PID is alive; is it really the one listening on the port?
    if [ -n "$OLD_PORT" ] && command -v lsof >/dev/null 2>&1; then
      LISTEN_PID=$(lsof -nP -iTCP:"$OLD_PORT" -sTCP:LISTEN -t 2>/dev/null | head -n1)
      if [ -n "$LISTEN_PID" ] && [ "$LISTEN_PID" = "$OLD_PID" ]; then
        echo "[!] EOS UI is already running (PID $OLD_PID, port $OLD_PORT)"
        echo "    To stop it: bin/stop.sh"
        exit 1
      fi
    else
      echo "[!] EOS UI is already running (PID $OLD_PID)"
      echo "    To stop it: bin/stop.sh"
      exit 1
    fi
  fi
  # PID is not alive -> stale file
  rm -f "$PID_FILE"
fi

# --- Port selection -----------------------------------------------------------
if [ -n "$1" ]; then
  PORT="$1"
  if ! port_is_free "$PORT"; then
    echo "[!] Requested port $PORT is not available."
    exit 1
  fi
else
  PORT=$(get_free_port)
  if [ -z "$PORT" ]; then
    echo "[!] No free port found."
    exit 1
  fi
  # Race guard: re-check
  if ! port_is_free "$PORT"; then
    PORT=$(get_free_port)
  fi
fi

# --- Server entrypoint --------------------------------------------------------
# Starts the FastAPI application with uvicorn. ui.server:app must be
# importable from the repo root (ui/ is a package).
ENTRY="ui.server:app"
if [ ! -f "$UI_DIR/server.py" ]; then
  echo "[!] ui/server.py not found. eos-ui may not be built yet (see docs/phases.md)."
  exit 1
fi

# --- Startup -------------------------------------------------------------
cd "$PROJECT_DIR"

echo "[*] Starting EOS UI..."
echo "    Project : $PROJECT_DIR"
echo "    App     : $ENTRY"
echo "    Port    : $PORT"
echo "    Log     : $LOG_FILE"

nohup python3 -m uvicorn "$ENTRY" --host 127.0.0.1 --port "$PORT" > "$LOG_FILE" 2>&1 &
PID=$!

START_EPOCH=$(date +%s)
{
  echo "$PID"
  echo "$PORT"
  echo "$ENTRY"
  echo "$START_EPOCH"
} > "$PID_FILE"

sleep 1

if kill -0 "$PID" >/dev/null 2>&1; then
  echo "[OK] EOS UI started (PID $PID) -> http://localhost:$PORT"
  echo "    API health: http://localhost:$PORT/api/health"
  python3 -c "import webbrowser; webbrowser.open('http://localhost:$PORT/api/instances')" 2>/dev/null || true
else
  echo "[FAIL] EOS UI failed to start. Log: $LOG_FILE"
  rm -f "$PID_FILE"
  exit 1
fi
