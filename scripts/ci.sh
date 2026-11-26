#!/bin/bash
# College Interview lifecycle: the one way to start / stop / inspect the app.
#   ci.sh open    start the server if it isn't healthy, then open a dashboard tab   (the Dock app runs this)
#   ci.sh stop    stop the server and every speech engine it runs
#   ci.sh status  running or not, pid, memory
# The server stops itself 8 s after the last Mac tab closes, or 2 min after start if no tab ever opens.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
export USER="${USER:-$(id -un)}" LOGNAME="${LOGNAME:-$(id -un)}"   # Claude sign-in (keychain) needs these   # Dock/Finder launches get a bare PATH: whisper-server lives here
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY=/Library/Frameworks/Python.framework/Versions/3.11/bin/python3
PORT=8000
URL="http://localhost:$PORT"
DIR="$HOME/Library/Application Support/CollegeInterview"
PIDF="$DIR/server.pid"
LOG="$HOME/Library/Logs/CollegeInterview.log"
mkdir -p "$DIR" "$(dirname "$LOG")"

healthy() { curl -s -m 2 "$URL/api/mic" >/dev/null 2>&1; }
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
    if ! healthy; then
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
