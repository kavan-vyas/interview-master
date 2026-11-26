#!/usr/bin/env bash
# College Interview: one-command install for macOS (Apple Silicon or Intel). Safe to run again:
# anything already installed or downloaded is kept, so a re-run only fills in what is missing.
#
#   scripts/setup.sh            install everything and build the Dock app
#   scripts/setup.sh --no-app   same, without (re)building ~/Applications/College Interview.app
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
NO_APP=0; [ "${1:-}" = "--no-app" ] && NO_APP=1

step() { printf '\n==> %s\n' "$*"; }
fail() { printf '\nSetup stopped: %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = Darwin ] || fail "this script is for macOS. On Windows run scripts\\setup.ps1 in PowerShell."
echo "College Interview setup ($(uname -m) Mac) in $ROOT"

# ---------------------------------------------------------------- Homebrew
step "Homebrew"
BREW=""
for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && BREW="$b" && break; done
if [ -z "$BREW" ]; then
  cat <<'EOF'
Homebrew (the Mac package manager) is not installed. Install it by pasting this into Terminal:

  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

Then close Terminal, open it again, and run scripts/setup.sh once more.
EOF
  exit 1
fi
eval "$("$BREW" shellenv)"
echo "found $BREW"

# ---------------------------------------------------------------- tools from Homebrew
step "Speech-to-text engine (whisper.cpp), ffmpeg, speaker switcher"
for f in whisper-cpp ffmpeg switchaudio-osx; do
  if brew list --formula "$f" >/dev/null 2>&1; then echo "ok      $f"; else brew install "$f"; fi
done
command -v whisper-server >/dev/null || fail "whisper-server was not found after installing whisper-cpp. Try: brew reinstall whisper-cpp"

# ---------------------------------------------------------------- Python 3.11 + .venv
step "Python 3.11"
PY311=""
for p in "$(brew --prefix)/opt/python@3.11/bin/python3.11" \
         /Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11 \
         "$(command -v python3.11 || true)"; do
  [ -n "$p" ] && [ -x "$p" ] && PY311="$p" && break
done
if [ -z "$PY311" ]; then
  brew install python@3.11
  PY311="$(brew --prefix)/opt/python@3.11/bin/python3.11"
fi
"$PY311" -c 'import sys; assert sys.version_info[:2] == (3, 11)' || fail "$PY311 is not Python 3.11"
echo "using $PY311"

step "Python packages (into .venv; first time takes a few minutes, about 1.5 GB)"
VPY="$ROOT/.venv/bin/python"
if [ ! -x "$VPY" ] || ! "$VPY" -c 'import sys; assert sys.version_info[:2] == (3, 11)' 2>/dev/null; then
  rm -rf "$ROOT/.venv"
  "$PY311" -m venv "$ROOT/.venv"
fi
"$VPY" -m pip install --upgrade --quiet pip
"$VPY" -m pip install -r requirements.txt

# ---------------------------------------------------------------- models + folders
step "Models (about 2.3 GB the first time; an interrupted download resumes)"
mkdir -p models sessions .claude_sandbox
"$VPY" scripts/fetch_models.py

# ---------------------------------------------------------------- Claude Code
step "Claude Code (the AI professor uses your own Claude sign-in)"
export PATH="$HOME/.local/bin:$PATH"
if command -v claude >/dev/null 2>&1; then
  echo "found $(command -v claude)"
  if claude auth status 2>/dev/null | grep -q '"loggedIn": *true'; then
    echo "signed in"
  else
    echo "Not signed in yet. Run this, and sign in with your Claude account in the browser that opens:"
    echo "  claude auth login"
  fi
else
  cat <<'EOF'
Claude Code is not installed. Install it with:

  curl -fsSL https://claude.ai/install.sh | bash

then sign in (needs a Claude Pro or Max plan):

  claude auth login
EOF
fi

# ---------------------------------------------------------------- Dock app
if [ "$NO_APP" = 0 ]; then
  step "Dock app"
  APP="$(scripts/make_app.sh)"
  echo "built $APP (drag it to your Dock if you like)"
fi

step "Checking the install"
"$VPY" launch.py doctor || true

cat <<EOF

Setup finished.
Start College Interview: open "College Interview" from ~/Applications (or Launchpad),
or run:  .venv/bin/python launch.py open
The first start asks for microphone permission: click Allow.
EOF
