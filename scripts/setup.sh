#!/usr/bin/env bash
# One-time install for the mic pipeline (M-series Mac). Everything free + open source.
set -euo pipefail
cd "$(dirname "$0")/.."
brew list whisper-cpp >/dev/null 2>&1 || brew install whisper-cpp
brew list ffmpeg >/dev/null 2>&1 || brew install ffmpeg
python3 -m pip install -r requirements.txt
mkdir -p models
M=models/ggml-large-v3-turbo.bin
URL=https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin
if [ ! -f "$M" ]; then
  # resumable: retries + continue from .part, rename only when complete
  for i in 1 2 3 4 5; do
    curl -L --fail --http1.1 -C - --retry 3 -o "$M.part" "$URL" && break
    echo "download interrupted, resuming ($i)..."; sleep 2
  done
  mv "$M.part" "$M"
fi
echo "done. start with: scripts/run.sh"
