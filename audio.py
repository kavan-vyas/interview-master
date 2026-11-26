"""mic -> Silero VAD -> whisper-server -> ECAPA speaker ID. Audio stays in RAM, never on disk.

Callbacks run on worker threads; the server bridges them into asyncio.
Run standalone to test in terminal:  python3 audio.py
"""
import io
import json
import queue
import threading
import time
import uuid
import urllib.error
import urllib.request
import wave
from concurrent.futures import ThreadPoolExecutor

import re
import sys
import tempfile
import warnings

import numpy as np

warnings.filterwarnings("ignore", message="An output with one or more elements was resized")

# whisper hallucinations on noise/short audio
HALLU = {"thank you", "thanks", "bye", "amen", "you", "yes sir", "um", "uh", "hey", "mm-hmm", "right",
         "oh okay", "i see", "thank you very much", "thanks for watching", "that", "is", "and then", "eyes"}

import config as C

_LOGDIR = "/tmp" if sys.platform != "win32" else tempfile.gettempdir()   # Windows has no /tmp


def _dehallu(t):
    """Whisper on noise: subtitle-style '- X - Y' lines and loops ('I said I said I said...'). Drop or collapse."""
    t = (t or "").strip()
    if t.startswith("- ") and t.count(" - ") >= 1:
        return ""
    w = t.split()
    if len(w) >= 12:
        grams = [" ".join(w[i:i + 4]).lower() for i in range(len(w) - 3)]
        if len(set(grams)) / len(grams) < 0.5:
            return ""                    # mostly a loop: nothing real in it
    sents = re.split(r"(?<=[.?!])\s+", t)
    out = [x for i, x in enumerate(sents) if i == 0 or x.lower() != sents[i - 1].lower()]
    return " ".join(out)


def deps_ok():
    try:
        import sounddevice, silero_vad, speechbrain, torch  # noqa: F401
        return True, ""
    except Exception as e:  # ImportError or PortAudio missing
        return False, str(e)


def wav_bytes(x):
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(C.SR)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


STT_URL = "http://127.0.0.1:8081/stt"
PARTIAL_URL = "http://127.0.0.1:8082/inference"   # small.en whisper-server: ~0.3 s/call -> live words while speaking
_partial = {"proc": None}


def ensure_partial(timeout=60):
    """Second, small whisper-server only for live captions; the big model still does the final text. Non-fatal."""
    import shutil, subprocess
    try:
        urllib.request.urlopen(PARTIAL_URL.rsplit("/", 1)[0] + "/", timeout=1)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        pass
    exe, model = shutil.which("whisper-server"), C.MODELS / "ggml-small.en.bin"
    if not exe or not model.exists():
        return False
    log = open(f"{_LOGDIR}/im-partial.log", "w")
    _partial["proc"] = subprocess.Popen([exe, "-m", str(model), "--host", "127.0.0.1", "--port", "8082", "-l", "en"],
                                        stdout=log, stderr=log)
    t = time.time()
    while time.time() - t < timeout:
        try:
            urllib.request.urlopen(PARTIAL_URL.rsplit("/", 1)[0] + "/", timeout=1)
            return True
        except urllib.error.HTTPError:
            return True
        except Exception:
            time.sleep(0.5)
    return False


def whisper_partial(x):
    b = "imp" + str(int(time.time() * 1000))
    body = (f'--{b}\r\nContent-Disposition: form-data; name="temperature"\r\n\r\n0\r\n'
            f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="p.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode() \
        + wav_bytes(x) + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(PARTIAL_URL, data=body, headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    with urllib.request.urlopen(req, timeout=5) as r:
        t = " ".join((json.loads(r.read()).get("text") or "").split())
    return "" if "[" in t or "(" in t else t
STT_PY = C.ROOT / "tts" / ".venv" / "bin" / "python"
_mlx = {"on": False, "proc": None, "check_at": 0.0, "restart_at": 0.0}


def _stt_up():
    try:
        urllib.request.urlopen("http://127.0.0.1:8081/", timeout=1)
        return True
    except Exception:
        return False


def ensure_stt(timeout=180):
    """Prefer the MLX worker (~1 s/sentence). Returns Popen or None; falls back to whisper.cpp if unavailable."""
    import subprocess
    if _stt_up():
        _mlx["on"] = True
        return None
    if not STT_PY.exists():
        return None
    log = open(f"{_LOGDIR}/im-stt.log", "w")
    proc = subprocess.Popen([str(STT_PY), str(C.ROOT / "tts" / "stt_worker.py"), "8081"], stdout=log, stderr=log)
    t = time.time()
    while time.time() - t < timeout:
        if proc.poll() is not None:
            return None
        if _stt_up():
            _mlx["on"], _mlx["proc"] = True, proc
            return proc
        time.sleep(0.5)
    proc.kill()
    return None


def _whisper_mlx(x, prompt=""):
    import urllib.parse
    # whisper copies the prompt's style: a bare keyword list garbles numbers ("7x1284"), a sentence doesn't
    # disfluent prompt -> whisper keeps "um", "uh", "like" (they're measured in the report) instead of cleaning them out
    p = (f"Um, so, uh, a Cambridge maths interview, like, hmm. Words that may come up: {prompt}." if prompt
         else "Um, so, uh, like, hmm, I mean, you know.")
    req = urllib.request.Request(STT_URL + "?prompt=" + urllib.parse.quote(p), data=wav_bytes(x),
                                 headers={"Content-Type": "audio/wav"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _mlx_restart():
    """MLX worker died / hung: kill ours, start a fresh one (at most once a minute)."""
    if _stt_up() or time.time() < _mlx["restart_at"]:
        return
    _mlx["restart_at"] = time.time() + 60
    p = _mlx.get("proc")
    if p and p.poll() is None:
        p.kill()
    print("mlx stt down: restarting worker", flush=True)
    ensure_stt(timeout=90)


def whisper(x, prompt=""):
    """MLX first. A failure retries MLX (restarting a dead worker); whisper.cpp only if it is actually up.
    While on whisper.cpp, MLX is re-checked every 30 s and taken back as soon as it answers."""
    if not _mlx["on"] and time.time() >= _mlx["check_at"]:
        _mlx["check_at"] = time.time() + 30
        _mlx["on"] = _stt_up()
    if _mlx["on"]:
        for i in range(2):
            try:
                d = _whisper_mlx(x, prompt)
                text = " ".join((d.get("text") or "").split())
                lp = [s.get("avg_logprob") for s in d.get("segments") or [] if s.get("avg_logprob") is not None]
                return text, (float(np.exp(np.mean(lp))) if lp else 1.0)
            except Exception as e:
                print(f"mlx stt failed ({i + 1}/2):", e, flush=True)
                if i == 0:
                    _mlx_restart()
        if not whisper_up():
            raise RuntimeError("speech-to-text down (MLX failed, whisper.cpp not running): retrying on the next line")
        print("mlx stt: whisper.cpp fallback, re-checking MLX every 30 s", flush=True)
        _mlx["on"], _mlx["check_at"] = False, time.time() + 30
    return _whisper_cpp(x, prompt)


def _whisper_cpp(x, prompt=""):
    """POST to whisper.cpp whisper-server /inference (multipart, in-memory)."""
    b = uuid.uuid4().hex
    parts = []
    for k, v in (("temperature", "0"), ("response_format", "json"), ("prompt", prompt)):   # verbose_json (logprobs) cost +0.9 s/line
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="s.wav"\r\n'
                 f"Content-Type: audio/wav\r\n\r\n".encode() + wav_bytes(x) + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    req = urllib.request.Request(C.WHISPER_URL, data=b"".join(parts),
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read())
    text = " ".join((d.get("text") or "").split())
    segs = d.get("segments") or []
    # avg_logprob -> rough 0..1 confidence
    lp = [s.get("avg_logprob") for s in segs if s.get("avg_logprob") is not None]
    conf = float(np.exp(np.mean(lp))) if lp else 1.0
    return text, conf


def whisper_up():
    import urllib.parse
    u = urllib.parse.urlparse(C.WHISPER_URL)
    try:
        urllib.request.urlopen(f"{u.scheme}://{u.netloc}/", timeout=1)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def ensure_whisper(timeout=90):
    """MLX worker first; whisper-server (Metal) only as fallback. Returns the Popen or None."""
    import shutil, subprocess, urllib.parse
    p = ensure_stt()
    if _mlx["on"]:
        return p
    if whisper_up():
        return None
    exe, model = shutil.which("whisper-server"), C.MODELS / "ggml-large-v3-turbo.bin"
    if not exe or not model.exists():
        raise RuntimeError("whisper-server or model missing: run scripts/setup.sh")
    u = urllib.parse.urlparse(C.WHISPER_URL)
    log = open(f"{_LOGDIR}/im-whisper.log", "w")
    proc = subprocess.Popen([exe, "-m", str(model), "--host", u.hostname, "--port", str(u.port), "-l", "en"],
                            stdout=log, stderr=log)
    t = time.time()
    while time.time() - t < timeout:
        if proc.poll() is not None:
            raise RuntimeError(f"whisper-server exited, see {_LOGDIR}/im-whisper.log")
        if whisper_up():
            return proc
        time.sleep(0.5)
    raise RuntimeError("whisper-server did not come up in time")


class Pipeline:
    def __init__(self, on_segment, on_activity, on_level, on_enrolled):
        self.on_segment, self.on_activity = on_segment, on_activity
        self.on_level, self.on_enrolled = on_level, on_enrolled
        self.q = queue.Queue(maxsize=400)
        self.vocab = ""
        self.voices = {}          # role -> unit embedding
        self.enrol_role = None
        self.enrol_buf = []
        self.paused = False
        self._stop = threading.Event()
        self.pool = ThreadPoolExecutor(1)
        self._olock = threading.RLock()     # stream open/close: watchdog thread vs /api/mic never overlap
        self._plock = threading.Lock()      # pending counter (+= on the VAD thread, -= on the STT thread)
        self.pending = 0
        self.on_partial = None              # live words while speaking (set by the server)
        self.ppool = ThreadPoolExecutor(1)
        self._pseq, self._pbusy, self._plast, self.partial_ok = 0, False, 0.0, False

    # ---------- setup ----------
    def load(self):
        import torch
        from silero_vad import load_silero_vad
        try:
            from speechbrain.inference.speaker import EncoderClassifier
        except ImportError:
            from speechbrain.pretrained import EncoderClassifier
        torch.set_num_threads(2)
        self.torch = torch
        self.vad = load_silero_vad()
        local, opts = C.MODELS / "ecapa", {"device": "mps" if torch.backends.mps.is_available() else "cpu"}
        try:       # local copy first: starting offline / with flaky Wi-Fi must still work
            self.enc = EncoderClassifier.from_hparams(source=str(local), savedir=str(local), run_opts=opts)
        except Exception:
            self.enc = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb", savedir=str(local), run_opts=opts)

    # ---------- mic selection: MacBook mic preferred, saved choice wins, auto-recover on device changes ----------
    MIC_FILE = C.SESSIONS / "_mic.txt"

    @staticmethod
    def inputs():
        import sounddevice as sd
        return [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]

    def preferred(self):
        names = self.inputs()
        want = []
        try:
            want.append(self.MIC_FILE.read_text().strip())
        except Exception:
            pass
        cam = ("cam", "webcam", "brio", "c920", "c922", "c930", "logi", "kiyo", "insta360", "obsbot", "elgato")
        want = [n for n in names if any(c in n.lower() for c in cam) and "macbook" not in n.lower() and "teams" not in n.lower()] + want   # webcam mic first
        bt = ("shokz", "openrun", "airpods", "buds", "headset", "hands-free", "bose", "sony wh", "jabra")   # Bluetooth mics = phone-call quality
        want = [w for w in want if not any(b in w.lower() for b in bt)]
        want += [n for n in names if "macbook" in n.lower() and "microphone" in n.lower()]
        want += [n for n in names if "macbook" in n.lower()]
        for w in want:
            if w in names:
                return w
        return names[0] if names else None

    def open(self, name=None):
        import sounddevice as sd
        try:
            from tts import voice
            playing = voice.playing
        except Exception:
            playing = lambda: False   # noqa: E731
        with self._olock:
            t = time.time()
            while playing() and time.time() - t < 6:   # PortAudio restart would kill the professor's voice
                time.sleep(0.1)
            try:
                self.stream.stop(); self.stream.close()
            except Exception:
                pass
            if not playing():                          # still talking: just reopen the input stream
                sd._terminate(); sd._initialize()      # refresh PortAudio's device list (iPad/headphones plugged in)
            name = name or self.preferred()
            self.stream = sd.InputStream(samplerate=C.SR, channels=1, dtype="float32", device=name,
                                         blocksize=C.VAD_CHUNK, callback=self._cb)
            self.stream.start()
            self.device_name, self.last_cb = name, time.time()
            self.last_sig = self.last_cb
        print("mic:", name, flush=True)

    def choose(self, name):
        self.MIC_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.MIC_FILE.write_text(name)
        self.open(name)

    def _watch(self):
        """Stream silently dies when the device disappears or the default changes -> reopen."""
        tries, next_at = 0, 0.0
        while not self._stop.is_set():
            time.sleep(2)
            now = time.time()
            dead = now - getattr(self, "last_cb", now) > 4
            flat = now - getattr(self, "last_sig", now) > 6   # callbacks still firing but pure zeros (device switch, Bluetooth hijack)
            if not (dead or flat):
                tries = 0
                continue
            if now < next_at:          # back off: reopening PortAudio every 2 s leaks until macOS kills us
                continue
            tries += 1
            next_at = now + min(60, 5 * 2 ** min(tries, 4))
            if True:
                try:
                    print("mic stalled" if dead else "mic silent (zeros)", "- reopening", flush=True)
                    self.open()
                except Exception as e:
                    print("mic reopen failed:", e, flush=True)

    def start(self):
        self.load()
        self.open()
        threading.Thread(target=lambda: setattr(self, "partial_ok", ensure_partial()), daemon=True).start()
        threading.Thread(target=self._run, daemon=True).start()
        threading.Thread(target=self._watch, daemon=True).start()

    def stop(self):
        self._stop.set()
        with self._olock:
            try:
                self.stream.stop(); self.stream.close()
            except Exception:
                pass

    def _cb(self, indata, frames, t, status):
        self.last_cb = time.time()
        if indata.any():
            self.last_sig = self.last_cb
        try:
            self.q.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass

    # ---------- speaker ----------
    def embed(self, x):
        with self.torch.no_grad():
            e = self.enc.encode_batch(self.torch.from_numpy(x).unsqueeze(0)).squeeze().cpu().numpy()
        return e / (np.linalg.norm(e) + 1e-9)

    def enrol(self, role):
        self.enrol_buf, self.enrol_role = [], role

    def who(self, x):
        if not self.voices or len(x) < C.SR * 0.6:
            return "?", 0.0
        e = self.embed(x)
        sc = sorted(((float(e @ v), r) for r, v in self.voices.items()), reverse=True)
        best, role = sc[0]
        margin = best - (sc[1][0] if len(sc) > 1 else -1)
        if best < C.SPK_THRESH or margin < C.SPK_MARGIN:
            return "?", round(best, 2)
        if best >= C.SPK_ADAPT and len(x) >= C.SR * 2:   # refine voice print with confident speech
            v = 0.9 * self.voices[role] + 0.1 * e
            self.voices[role] = v / (np.linalg.norm(v) + 1e-9)
        return role, round(best, 2)

    # ---------- main loop ----------
    def _run(self):
        pre = []                         # 200 ms pre-roll
        buf, speaking, quiet, last_lvl = [], False, 0, 0
        end_chunks = int(C.END_SILENCE_S * C.SR / C.VAD_CHUNK)
        while not self._stop.is_set():
            try:
                ch = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            now = time.time()
            if now - last_lvl > 0.08 and not self.enrol_role:
                self.on_level(float(np.sqrt(np.mean(ch ** 2))))
                last_lvl = now
            if self.paused:
                continue
            p = self.vad(self.torch.from_numpy(ch), C.SR).item()
            voiced = p >= C.VAD_THRESH
            if self.enrol_role:
                if voiced:
                    self.enrol_buf.append(ch)
                    self.on_level(-len(self.enrol_buf) * C.VAD_CHUNK / C.SR / C.ENROL_S)  # progress <0
                if len(self.enrol_buf) * C.VAD_CHUNK >= C.ENROL_S * C.SR:
                    role, x = self.enrol_role, np.concatenate(self.enrol_buf)
                    self.voices[role] = self.embed(x)
                    self.enrol_role, self.enrol_buf = None, []
                    self.on_enrolled(role)
                continue
            if voiced:
                if not speaking:
                    speaking, buf = True, list(pre)
                buf.append(ch); quiet = 0
                self.on_activity()
            elif speaking:
                buf.append(ch); quiet += 1
            pre = (pre + [ch])[-6:]
            dur = len(buf) * C.VAD_CHUNK / C.SR
            self.in_speech = speaking
            if (speaking and self.on_partial and self.partial_ok and not self._pbusy and dur >= 0.6
                    and now - self._plast >= 0.3):
                self._pbusy, self._plast = True, now
                self.ppool.submit(self._partial, np.concatenate(buf[-int(15 * C.SR / C.VAD_CHUNK):]), self._pseq)
            if speaking and (quiet >= end_chunks or dur >= C.MAX_SEG_S):
                speaking = False
                self._pseq += 1                 # late partials for this segment are dropped
                if dur - quiet * C.VAD_CHUNK / C.SR >= C.MIN_SPEECH_S:
                    try:
                        with self._plock:
                            self.pending += 1
                        self.pool.submit(self._finish, np.concatenate(buf), now)
                    except RuntimeError:  # interpreter shutting down
                        return
                buf = []

    def _partial(self, x, seq):
        try:
            t = _dehallu(whisper_partial(x))
            if t and seq == self._pseq and self.on_partial:
                self.on_partial(seq, t)
        except Exception:
            pass
        finally:
            self._pbusy = False

    def _finish(self, x, t_end):
        try:
            self._finish2(x, t_end)
        finally:
            with self._plock:
                self.pending = max(0, self.pending - 1)

    def _finish2(self, x, t_end):
        try:
            text, conf = whisper(x, self.vocab)
        except Exception as e:
            self.on_segment({"error": f"whisper: {e}"})
            return
        text = _dehallu(text)
        norm = re.sub(r"[^a-z' -]", "", text.lower()).strip(" -")
        if not norm or "[" in text or "*" in text or (norm in HALLU and len(x) < C.SR * 8):
            return
        role, sconf = ("S", 1.0) if getattr(self, "solo", False) else self.who(x)
        self.on_segment({"speaker": role, "text": text, "conf": round(min(conf, 1.0), 2),
                         "spk_conf": sconf, "dur": round(len(x) / C.SR, 1), "t_end": t_end})


if __name__ == "__main__":  # terminal test: build-order step 1
    ok, why = deps_ok()
    if not ok:
        raise SystemExit(f"audio deps missing: {why}\nrun scripts/setup.sh")
    p = Pipeline(lambda s: print(json.dumps(s)), lambda: None, lambda l: None,
                 lambda r: print(f"[enrolled {r}]"))
    print("starting whisper-server...")
    wproc = ensure_whisper()
    p.start()
    p.paused = True
    for role in ("I", "S"):
        input(f"Enrol {role}: press Enter, then read a sentence for ~{C.ENROL_S:.0f}s of speech...")
        p.enrol(role); p.paused = False
        while p.enrol_role:
            time.sleep(0.2)
        p.paused = True
    p.paused = False
    print("Listening. Ctrl-C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        p.stop()
        if wproc:
            wproc.terminate()
