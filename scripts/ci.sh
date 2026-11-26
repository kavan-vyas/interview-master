#!/bin/bash
# College Interview lifecycle: the one way to start / stop / inspect the app.
#   ci.sh open    start the server if it isn't healthy, then open a dashboard tab   (the Dock app runs this)
#   ci.sh stop    stop the server and every speech engine it runs
#   ci.sh status  running or not, pid, memory
# The server stops itself 8 s after the last Mac tab closes, or 2 min after start if no tab ever opens.
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
export USER="${USER:-$(id -un)}" LOGNAME="${LOGNAME:-$(id -un)}"   # Claude sign-in (keychain) needs these   # Dock/Finder launches get a bare PATH: whisper-server lives here
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY=/Library/Frameworks/Python.framework/Versions/3.11/bin/python3   # setup.sh venv first
PORT="${IM_PORT:-${PORT:-8000}}"           # same variable as config.py (IM_PORT; PORT accepted)
export IM_PORT="$PORT"
URL="http://localhost:$PORT"
DIR="$HOME/Library/Application Support/CollegeInterview"
if [ "$PORT" = 8000 ]; then PIDF="$DIR/server.pid"; else PIDF="$DIR/server-$PORT.pid"; fi   # matches server.py
LOG="$HOME/Library/Logs/CollegeInterview.log"
mkdir -p "$DIR" "$(dirname "$LOG")"

healthy() { curl -s -m 2 "$URL/api/mic" >/dev/null 2>&1; }
# a busy but alive server (slow request, report being written) is NOT restarted: a few longer probes first
healthy_slow() { healthy && return 0; alive || return 1; for _ in 1 2 3; do curl -s -m 6 "$URL/api/mic" >/dev/null 2>&1 && return 0; sleep 1; done; return 1; }
pid() { cat "$PIDF" 2>/dev/null; }
alive() { local p; p=$(pid); [ -n "$p" ] && kill -0 "$p" 2>/dev/null; }

stop() {
  local p; p=$(pid)
  if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then
    kill "$p"; for _ in $(seq 50); do kill -0 "$p" 2>/dev/null || break; sleep 0.1; done
    kill -9 "$p" 2>/dev/null
  fi
  # strays: an older copy of this app's server or engines (never anything outside this folder)
  pkill -f "$ROOT/server.py" 2>/dev/null; pkill -f "^$PY -u server.py" 2>/dev/null
  pkill -f "$ROOT/models/ggml-" 2>/dev/null; pkill -f "$ROOT/tts/stt_worker.py" 2>/dev/null
  rm -f "$PIDF"
}

case "${1:-open}" in
  open|start)
    if ! healthy_slow; then
      stop                                       # half-dead or orphaned copy: clear it first
      cd "$ROOT" && nohup "$PY" -u "$ROOT/server.py" --no-open >>"$LOG" 2>&1 &
      for _ in $(seq 120); do healthy && break; sleep 0.5; done
      healthy || { osascript -e 'display alert "College Interview did not start" message "Log: ~/Library/Logs/CollegeInterview.log"'; exit 1; }
    fi
    [ "$1" = start ] || open "$URL" ;;
  stop) stop; echo stopped ;;
  status)
    if alive; then echo "running pid $(pid), $(ps -o rss= -p "$(pid)" | awk '{printf "%.0f MB", $1/1024}')"; else echo "not running"; fi ;;
  *) echo "usage: ci.sh open|stop|status"; exit 2 ;;
esac
