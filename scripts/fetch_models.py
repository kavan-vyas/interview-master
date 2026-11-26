"""Download every model College Interview needs into models/. Safe to run again: files that exist are kept.

Run with the app's own Python (the one in .venv), after `pip install -r requirements.txt`:
    .venv/bin/python scripts/fetch_models.py           (Mac)
    .venv\\Scripts\\python.exe scripts\\fetch_models.py   (Windows)

What it fetches (about 2.3 GB in total):
    models/ggml-large-v3-turbo.bin   speech-to-text, main transcript   (whisper.cpp, ~1.6 GB)
    models/ggml-small.en.bin         speech-to-text, live captions     (whisper.cpp, ~470 MB)
    models/kokoro/                   the professor's voice              (Kokoro-82M, ~330 MB)
    models/ecapa/                    speaker recognition                (SpeechBrain ECAPA, ~90 MB)
"""
import shutil
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
HF = "https://huggingface.co"

WHISPER = {
    "ggml-large-v3-turbo.bin": f"{HF}/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin",
    "ggml-small.en.bin": f"{HF}/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin",
}
KOKORO_REPO = "hexgrad/Kokoro-82M"
KOKORO_FILES = ["config.json", "kokoro-v1_0.pth"] + [
    f"voices/{v}.pt" for v in ("bm_george", "bm_fable", "bm_lewis", "bm_daniel", "bf_emma", "bf_isabella")]
ECAPA_REPO = "speechbrain/spkrec-ecapa-voxceleb"
ECAPA_FILES = ["hyperparams.yaml", "embedding_model.ckpt", "mean_var_norm_emb.ckpt",
               "classifier.ckpt", "label_encoder.txt"]


def say(msg):
    print(msg, flush=True)


def download(url, dest):
    """Resumable HTTP download: writes <dest>.part, renames only when complete."""
    if dest.exists():
        say(f"  ok      {dest.relative_to(ROOT)}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    for attempt in range(1, 8):
        have = part.stat().st_size if part.exists() else 0
        req = urllib.request.Request(url, headers={"User-Agent": "college-interview-setup"})
        if have:
            req.add_header("Range", f"bytes={have}-")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                if have and r.status != 206:      # server ignored Range: start again
                    have = 0
                total = have + int(r.headers.get("Content-Length") or 0)
                with open(part, "ab" if have else "wb") as f:
                    done, last = have, 0.0
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        if time.time() - last > 1 and total:
                            last = time.time()
                            sys.stdout.write(f"\r  {dest.name}: {done / 1e6:,.0f} / {total / 1e6:,.0f} MB   ")
                            sys.stdout.flush()
            sys.stdout.write("\n")
            if total and part.stat().st_size < total:
                raise IOError("download ended early")
            part.replace(dest)
            say(f"  saved   {dest.relative_to(ROOT)}")
            return
        except Exception as e:
            say(f"\n  download interrupted ({e}); retrying {attempt}/7 ...")
            time.sleep(min(30, 2 * attempt))
    raise SystemExit(f"Could not download {url}. Check your internet connection and run setup again.")


def hf_file(repo, name, dest_dir):
    dest = dest_dir / name
    if dest.exists():
        say(f"  ok      {dest.relative_to(ROOT)}")
        return dest
    download(f"{HF}/{repo}/resolve/main/{name}", dest)
    return dest


def main():
    MODELS.mkdir(exist_ok=True)
    say("Speech-to-text models (whisper.cpp):")
    for name, url in WHISPER.items():
        download(url, MODELS / name)

    say("Professor voice (Kokoro):")
    for name in KOKORO_FILES:
        hf_file(KOKORO_REPO, name, MODELS / "kokoro")

    say("Speaker recognition (SpeechBrain ECAPA):")
    ecapa = MODELS / "ecapa"
    for name in ECAPA_FILES:
        if name == "label_encoder.txt" and (ecapa / "label_encoder.ckpt").exists():
            say("  ok      models/ecapa/label_encoder.ckpt")
            continue
        hf_file(ECAPA_REPO, name, ecapa)
    # SpeechBrain looks for label_encoder.ckpt; a real copy (not a symlink) works on Windows too.
    if not (ecapa / "label_encoder.ckpt").exists():
        shutil.copyfile(ecapa / "label_encoder.txt", ecapa / "label_encoder.ckpt")
    say("All models are in place.")


if __name__ == "__main__":
    main()
