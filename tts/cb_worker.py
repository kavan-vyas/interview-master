"""Chatterbox-Turbo (MLX) worker, run with tts/.venv python. Persistent: model loads once.

stdin  <- {"id": 3, "text": "One sentence."}  (one JSON per line)
stdout -> {"id": 3, "wav": "/tmp/..."} | {"id": 3, "err": "..."} | {"ready": true}
"""
import json
import os
import sys
import tempfile
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
out, sys.stdout = sys.stdout, sys.stderr          # libraries print; keep protocol stdout clean

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from mlx_audio.tts.utils import load_model  # noqa: E402

HERE = Path(__file__).parent
REF = str(HERE / "voices" / "prof_ref_000.wav")
REFS = {"prof": REF, "second": str(HERE / "voices" / "second_ref_000.wav")}
MODEL = os.environ.get("CB_MODEL", "mlx-community/chatterbox-turbo-8bit")
TMP = Path(tempfile.gettempdir()) / "im-voice"
TMP.mkdir(exist_ok=True)


def say(o):
    out.write(json.dumps(o) + "\n")
    out.flush()


m = load_model(MODEL)
for _ in m.generate(text="Right.", ref_audio=REF):   # warm + cache speaker conditioning
    pass
say({"ready": True})
n = 0
for line in sys.stdin:
    job = {}
    try:
        job = json.loads(line)
        a = np.concatenate([np.asarray(r.audio) for r in m.generate(text=job["text"], ref_audio=REFS.get(job.get("ref"), REF))])
        n += 1
        f = TMP / f"cb-{job['id']}-{n}.wav"
        sf.write(f, a, m.sample_rate)
        say({"id": job["id"], "wav": str(f)})
    except Exception as e:
        say({"id": job.get("id"), "err": str(e)[:200]})
