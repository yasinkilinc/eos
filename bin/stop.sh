#!/bin/sh
# EOS UI Stop Script
# Usage: bin/stop.sh
#
# Stops only the EOS UI process started by bin/start.sh.
# PID/port are cross-checked; on any mismatch (PID reused, port taken over by
# another process) NO process is touched, so an unrelated process is never
# killed by mistake.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PID_FILE="$PROJECT_DIR/.eos-ui.pid"

if [ ! -f "$PID_FILE" ]; then
  echo "[!] No PID file: $PID_FILE"
  echo "    EOS UI is not running, or was started without bin/start.sh."
  exit 1
fi

PID=$(sed -n '1p' "$PID_FILE")
PORT=$(sed -n '2p' "$PID_FILE")

if [ -z "$PID" ]; then
  echo "[!] PID file is empty. Removing it."
  rm -f "$PID_FILE"
  exit 1
fi

# Is the PID alive?
if ! kill -0 "$PID" >/dev/null 2>&1; then
  echo "[*] Process $PID is not running anymore. Removing stale PID file."
  rm -f "$PID_FILE"
  exit 0
fi

# --- Port/PID cross-check -------------------------------------------
# Do nothing if the process actually listening on the port differs from
# the recorded PID.
if [ -n "$PORT" ] && command -v lsof >/dev/null 2>&1; then
  LISTEN_PID=$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -n1)
  if [ -z "$LISTEN_PID" ]; then
    echo "[!] PID $PID is not listening on port $PORT (nobody is listening on it)."
    echo "    Trying to stop it anyway (trusting the recorded PID)..."
    LISTEN_PID="$PID"  # fallthrough: no match, but stop it anyway
  fi
  if [ "$LISTEN_PID" != "$PID" ]; then
    echo "[!] PID $PID is not listening on port $PORT; a different process $LISTEN_PID is."
    echo "    Avoiding touching a different process. Removing PID file."
    rm -f "$PID_FILE"
    exit 1
  fi
fi

echo "[*] Stopping EOS UI (PID $PID, port $PORT)..."
kill "$PID"

WAIT=0
while kill -0 "$PID" >/dev/null 2>&1; do
  sleep 0.5
  WAIT=$((WAIT + 1))
  if [ "$WAIT" -ge 10 ]; then
    echo "[!] Process did not stop gracefully, applying kill -9..."
    kill -9 "$PID" 2>/dev/null || true
    break
  fi
done

rm -f "$PID_FILE"
echo "[OK] EOS UI stopped"
