"""Professor voice: Kokoro-82M (open source, local) + maths-aware LaTeX -> speech (from phi-plus).

Sentence streaming: first sentence plays while the rest is still being synthesised. Interruptible.
"""
import json
import os
import queue
import re
import subprocess
import tempfile
import threading
from pathlib import Path

import numpy as np
import soundfile as sf

from .latex_to_speech import convert_latex
from .lexicon import apply_lexicon

os.environ.setdefault("HF_HUB_OFFLINE", "1")   # model is cached; no network (eduroam-safe, fast load)
SR = 24000
VOICES = ["bm_george", "bm_fable", "bm_lewis", "bm_daniel", "bf_emma", "bf_isabella"]
LEX = Path(__file__).parent / "maths_lexicon.json"
KDIR = Path(__file__).resolve().parent.parent / "models" / "kokoro"
_pipe, _lock = None, threading.Lock()
_gen = 0                     # bumps on stop(): stale producers/players exit
_player = {"p": None}
_out = {"n": 0}               # output streams open right now (mic reopen must not restart PortAudio under them)


def playing():
    return _out["n"] > 0
TMP = Path(tempfile.gettempdir()) / "im-voice"


def load():
    global _pipe
    with _lock:
        if _pipe is None:
            from kokoro import KModel, KPipeline
            if (KDIR / "kokoro-v1_0.pth").exists():     # local copy: works offline, no Hub lookups
                m = KModel(repo_id="hexgrad/Kokoro-82M", config=str(KDIR / "config.json"),
                           model=str(KDIR / "kokoro-v1_0.pth")).eval()
                _pipe = KPipeline(lang_code="b", repo_id="hexgrad/Kokoro-82M", model=m)
            else:
                _pipe = KPipeline(lang_code="b", repo_id="hexgrad/Kokoro-82M")
            apply_lexicon(_pipe, LEX)
            for _ in _pipe("Right.", voice=_vp(VOICES[0])):   # warm the graph
                pass
    return _pipe


def _vp(v):
    f = KDIR / "voices" / f"{v}.pt"
    return str(f) if f.exists() else v


def spoken(text):
    return convert_latex(text)


_plock = threading.Lock()      # guards generation check + afplay spawn
_slock = threading.Lock()      # Kokoro KPipeline is not thread-safe
HERE = Path(__file__).parent


def stop():
    global _gen
    with _plock:
        _gen += 1
        p = _player["p"]
        if p and p.poll() is None:
            p.kill()


# ---------- Chatterbox-Turbo worker (human-sounding, preferred) ----------
VENV_PY = HERE / ".venv" / "bin" / "python"
_cb = {"p": None, "ready": False, "lock": threading.Lock()}


def cb_ok(ref="prof"):
    return (HERE / "voices" / f"{ref}_ref_000.wav").exists() and VENV_PY.exists()


def cb_start():
    """Spawn (or respawn) the persistent Chatterbox worker; blocks until warm. False if unavailable."""
    if _cb["ready"] and _cb["p"] and _cb["p"].poll() is None:
        return True
    _cb["ready"] = False
    if not cb_ok():
        return False
    with _cb["lock"]:
        if _cb["p"] is None or _cb["p"].poll() is not None:
            _cb["p"] = subprocess.Popen([str(VENV_PY), str(HERE / "cb_worker.py")],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
            _cb["ready"] = '"ready"' in (_cb["p"].stdout.readline() or "")
        else:
            _cb["ready"] = True
    return _cb["ready"]


def _cb_synth(sentence, my, ref="prof"):
    """One sentence via the worker. Raises if the worker is dead/garbled (caller falls back to Kokoro)."""
    with _cb["lock"]:
        p = _cb["p"]
        if not p or p.poll() is not None:
            _cb["ready"] = False
            raise RuntimeError("chatterbox worker not running")
        try:
            p.stdin.write(json.dumps({"id": my, "text": sentence, "ref": ref}) + "\n")
            p.stdin.flush()
            r = json.loads(p.stdout.readline() or "{}")
        except Exception:
            _cb["ready"] = False
            raise
    if not r.get("wav"):
        raise RuntimeError(r.get("err") or "chatterbox: no audio")
    return Path(r["wav"])


def _kokoro(sentence, voice, speed, path):
    kv = {"prof": "bm_george", "second": "bf_emma"}.get(voice, voice)
    if kv not in VOICES:
        kv = "bm_george"
    pipe = load()
    with _slock:
        audio = [np.asarray(r.audio) for r in pipe(sentence, voice=kv, speed=speed)]
    if not audio:
        return None
    sf.write(path, np.concatenate(audio), SR)
    return path


def _chunks(text):
    """Sentences; a long first sentence is cut at its first comma so audio starts sooner."""
    parts = [s for s in re.split(r"(?<=[.?!])\s+", spoken(text)) if s.strip()]
    if parts and len(parts[0].split()) > 6:
        m = re.search(r",\s", parts[0])
        if m and parts[0][m.end():].strip():
            parts[:1] = [parts[0][:m.start() + 1], parts[0][m.end():]]
    return parts


def _kokoro_arr(sentence, voice, speed):
    kv = {"prof": "bm_george", "second": "bf_emma"}.get(voice, voice)
    if kv not in VOICES:
        kv = "bm_george"
    pipe = load()
    with _slock:
        for r in pipe(sentence, voice=_vp(kv), speed=speed):
            yield np.asarray(r.audio, dtype=np.float32)


def speak(text, voice="bm_george", speed=1.0, on_start=None):
    """Blocking, streamed: each Kokoro chunk plays the moment it exists, gapless (one output stream).
    "prof"/"second" = Chatterbox-Turbo if installed (falls back to Kokoro). Raises if nothing synthesised."""
    import sounddevice as sd
    stop()
    my = _gen
    use_cb = voice in ("prof", "second") and cb_ok(voice) and cb_start()
    q, err = queue.Queue(), []

    def produce():
        nonlocal use_cb
        try:
            parts = _chunks(text)
            total = sum(len(x) for x in parts) or 1
            for k, sent in enumerate(parts):
                if my != _gen:
                    break
                done = False
                if use_cb:
                    try:
                        f = _cb_synth(sent, my, voice)
                        a, _ = sf.read(f, dtype="float32")
                        f.unlink(missing_ok=True)
                        q.put((a, len(sent), total))
                        done = True
                    except Exception as e:
                        print("chatterbox failed, Kokoro fallback:", e, flush=True)
                        use_cb = False
                if not done:
                    for a in _kokoro_arr(sent, voice, speed):
                        if my != _gen:
                            break
                        q.put((a, len(sent), total))
        except Exception as e:
            err.append(e)
        finally:
            q.put(None)

    th = threading.Thread(target=produce, daemon=True)
    th.start()
    played, blk = 0, SR // 20
    with _plock:
        _out["n"] += 1
    try:
        with sd.OutputStream(samplerate=SR, channels=1, dtype="float32", latency="low") as out:
            while True:
                try:
                    item = q.get(timeout=0.25)   # never block forever: stop() or a dead producer ends playback
                except queue.Empty:
                    if my != _gen or (not th.is_alive() and q.empty()):
                        break
                    continue
                if item is None:
                    break
                a, n, total = item
                if not played and on_start:      # caption pacing: est. seconds for the whole line from chunk 1
                    try:
                        on_start(len(a) / SR / max(1, n) * total)
                    except Exception:
                        pass
                if a.ndim > 1:
                    a = a.mean(axis=1)
                for i in range(0, len(a), blk):
                    if my != _gen:
                        break
                    out.write(a[i:i + blk].reshape(-1, 1))
                if my == _gen:
                    played += 1
            if my == _gen and played:
                out.write(np.zeros((SR // 10, 1), np.float32))   # let the tail drain
    finally:
        with _plock:
            _out["n"] -= 1
    if err and not played:
        raise err[0]
