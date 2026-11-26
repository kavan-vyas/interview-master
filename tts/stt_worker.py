"""Speech-to-text worker on Apple MLX (~1 s per sentence vs 3-4 s for whisper.cpp here). Run with tts/.venv python.
POST /stt?prompt=...  body = 16 kHz mono WAV bytes  ->  {"text": ..., "segments": [{"avg_logprob": ...}]}"""
import io
import json
import os
import sys
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from urllib.parse import parse_qs, urlparse

warnings.filterwarnings("ignore")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
import mlx_whisper  # noqa: E402
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

MODEL = os.environ.get("STT_MODEL", "mlx-community/whisper-large-v3-turbo")
LOCK = Lock()
mlx_whisper.transcribe(np.zeros(16000, dtype=np.float32), path_or_hf_repo=MODEL)   # load + warm


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")

    def do_POST(self):
        q = parse_qs(urlparse(self.path).query)
        x, sr = sf.read(io.BytesIO(self.rfile.read(int(self.headers["Content-Length"]))), dtype="float32")
        with LOCK:
            r = mlx_whisper.transcribe(x, path_or_hf_repo=MODEL, language="en", temperature=0.0,
                                       condition_on_previous_text=False, initial_prompt=(q.get("prompt") or [None])[0] or None)
        out = json.dumps({"text": r.get("text", ""), "segments": [{"avg_logprob": s.get("avg_logprob")} for s in r.get("segments", [])]})
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(out.encode())


print("stt ready", flush=True)
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8081), H).serve_forever()
