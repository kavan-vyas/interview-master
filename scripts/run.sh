#!/usr/bin/env bash
# Starts the dashboard on http://localhost:8000. Speech-to-text: MLX worker (tts/stt_worker.py),
# started by the server itself; whisper-server (Metal) only if MLX isn't available.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 server.py "$@"
