"""All tunables in one place. Override any with env vars (IM_<NAME>)."""
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CARTS = ROOT / "cartridges"
SESSIONS = ROOT / "sessions"
MODELS = ROOT / "models"
SANDBOX = ROOT / ".claude_sandbox"  # empty cwd for `claude -p` -> no project context loaded


def _env(name, default, cast=str):
    v = os.environ.get("IM_" + name)
    return cast(v) if v is not None else default


def _claude_bin():
    if os.environ.get("CLAUDE_BIN"):
        return os.environ["CLAUDE_BIN"]
    stable = Path.home() / "bin" / "claude-stable"
    return str(stable) if stable.exists() else (shutil.which("claude") or "claude")


CLAUDE_BIN = _claude_bin()
GATE_MODEL = _env("GATE_MODEL", "haiku")
WRITER_MODEL = _env("WRITER_MODEL", "sonnet")
GATE_TIMEOUT = _env("GATE_TIMEOUT", 25.0, float)
WRITER_TIMEOUT = _env("WRITER_TIMEOUT", 60.0, float)

PORT = _env("PORT", 8000, int)
WHISPER_URL = _env("WHISPER_URL", "http://127.0.0.1:8080/inference")

# audio
SR = 16000
VAD_CHUNK = 512            # silero needs 512 samples @16k
VAD_THRESH = _env("VAD_THRESH", 0.5, float)
END_SILENCE_S = _env("END_SILENCE_S", 0.9, float)   # longer chunks = whisper gets context, fewer garbled fragments
MIN_SPEECH_S = _env("MIN_SPEECH_S", 0.4, float)
MAX_SEG_S = _env("MAX_SEG_S", 20.0, float)
ENROL_S = _env("ENROL_S", 10.0, float)
SPK_THRESH = _env("SPK_THRESH", 0.30, float)
SPK_MARGIN = _env("SPK_MARGIN", 0.05, float)
SPK_ADAPT = _env("SPK_ADAPT", 0.55, float)   # >= this: update voice print

# rules
HOLD_S = _env("HOLD_S", 45, int)           # error hold, 30..60
SILENCE_STEPS = (20, 45, 90)               # think-aloud, hint rung 1, simpler case
FAST_STAGE_S = _env("FAST_STAGE_S", 150, int)
STUCK_S = 90
WRITER_WINDOW_S = 90                       # transcript fed to writer
