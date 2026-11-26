"""College Interview launcher for macOS and Windows: the one way to start, stop or inspect the app.

    python launch.py open     start the server if it isn't healthy, then open the dashboard in your browser
    python launch.py start    same, without opening the browser
    python launch.py stop     stop the server and every speech engine it runs
    python launch.py status   running or not, pid, memory
    python launch.py doctor   check the install (Python packages, whisper-server, models, Claude sign-in)

Anything after the command is passed to server.py, e.g. `python launch.py open --no-audio`.
The server stops itself 8 s after the last dashboard tab closes, or 2 min after start if no tab ever opens.
Uses only the standard library, so any Python 3 can run it; the server itself runs on the .venv Python.
"""
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WIN = sys.platform == "win32"
MAC = sys.platform == "darwin"
PORT = int(os.environ.get("IM_PORT", 8000))
URL = f"http://localhost:{PORT}"
HEALTH = f"http://127.0.0.1:{PORT}/api/mic"

if WIN:
    _base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "CollegeInterview"
    DATA, LOG = _base, _base / "Logs" / "CollegeInterview.log"
elif MAC:
    DATA = Path.home() / "Library" / "Application Support" / "CollegeInterview"
    LOG = Path.home() / "Library" / "Logs" / "CollegeInterview.log"
else:
    DATA = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "CollegeInterview"
    LOG = DATA / "CollegeInterview.log"
PIDF = DATA / ("server.pid" if PORT == 8000 else f"server-{PORT}.pid")   # written by server.py itself (same path)


# ---------------------------------------------------------------- finding things
def venv_python():
    """The Python that has the app's packages: .venv first, then the Mac framework 3.11 the app was built on."""
    cands = [ROOT / ".venv" / "Scripts" / "python.exe"] if WIN else [ROOT / ".venv" / "bin" / "python"]
    if MAC:
        cands.append(Path("/Library/Frameworks/Python.framework/Versions/3.11/bin/python3"))
    for c in cands:
        if c.exists():
            return str(c)
    return sys.executable


def run_env():
    """PATH with whisper-server and the claude CLI on it (Dock / Start Menu launches get a bare PATH)."""
    env = dict(os.environ)
    home = Path.home()
    extra = [ROOT / "tools" / "whisper", home / ".local" / "bin", home / "bin"]
    if WIN:
        extra += [Path(os.environ.get("APPDATA", home)) / "npm"]
    else:
        extra += [Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
        env.setdefault("USER", os.environ.get("LOGNAME") or home.name)   # Claude sign-in (keychain) needs these
        env.setdefault("LOGNAME", env["USER"])
    env["PATH"] = os.pathsep.join([str(p) for p in extra if p.exists()] + [env.get("PATH", "")])
    env["PYTHONUTF8"] = "1"            # Windows: read/write the UTF-8 cartridges and logs correctly
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def whisper_server(env=None):
    return shutil.which("whisper-server", path=(env or run_env())["PATH"])


# ---------------------------------------------------------------- process helpers
def healthy():
    try:
        urllib.request.urlopen(HEALTH, timeout=2).read()
        return True
    except Exception:
        return False


def healthy_slow():
    """A busy but alive server (slow request, report being written) is not restarted: a few longer probes first."""
    if healthy():
        return True
    if not alive(pid()):
        return False
    for _ in range(3):
        try:
            urllib.request.urlopen(HEALTH, timeout=6).read()
            return True
        except Exception:
            time.sleep(1)
    return False


def port_busy():
    import socket
    s = socket.socket()
    s.settimeout(1)
    try:
        return s.connect_ex(("127.0.0.1", PORT)) == 0
    finally:
        s.close()


def pid():
    try:
        return int(PIDF.read_text().strip())
    except Exception:
        return None


def alive(p):
    if not p:
        return False
    if WIN:   # os.kill(p, 0) would send Ctrl-C on Windows: ask the kernel instead
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, p)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259         # STILL_ACTIVE
    try:
        os.kill(p, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _quiet(cmd):
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                       creationflags=0x08000000 if WIN else 0)    # CREATE_NO_WINDOW
    except Exception:
        pass


def _kill_matching(fragments):
    """Kill processes whose command line contains one of `fragments` (this app's own paths only)."""
    if WIN:   # one PowerShell call for all of them (each start costs about a second)
        lst = ",".join("'%s'" % f.replace("'", "''") for f in fragments)
        _quiet(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                "$f = @(%s); Get-CimInstance Win32_Process | Where-Object { $c = $_.CommandLine; $c -and "
                "$_.ProcessId -ne %d -and $_.ProcessId -ne $PID -and ($f | Where-Object { $c.Contains($_) }) } | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
                % (lst, os.getpid())])
    else:
        for f in fragments:
            _quiet(["pkill", "-f", f.replace(".", "\\.")])


def stop():
    p = pid()
    if alive(p):
        if WIN:
            _quiet(["taskkill", "/PID", str(p), "/T", "/F"])      # whole tree: server + whisper-servers
        else:
            import signal
            try:
                os.kill(p, signal.SIGTERM)                       # server saves and stops its engines
            except OSError:
                pass
            for _ in range(50):
                if not alive(p):
                    break
                time.sleep(0.1)
            if alive(p):
                try:
                    os.kill(p, signal.SIGKILL)
                except OSError:
                    pass
    # strays: an older copy of this app's server or engines (never anything outside this folder)
    _kill_matching([str(ROOT / "server.py"), str(ROOT / "models" / "ggml-"), str(ROOT / "tts" / "stt_worker.py")])
    try:
        PIDF.unlink()
    except Exception:
        pass


def alert(title, msg):
    print(f"{title}\n{msg}", file=sys.stderr)
    try:
        if MAC:
            subprocess.run(["osascript", "-e", f'display alert "{title}" message "{msg}"'], check=False)
        elif WIN:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, msg, title, 0x10)
    except Exception:
        pass


def start(server_args):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    (ROOT / ".claude_sandbox").mkdir(exist_ok=True)      # empty working folder for Claude calls
    stop()                                                # half-dead or orphaned copy: clear it first
    if port_busy():
        alert("College Interview did not start",
              f"Port {PORT} is already used by another program. Close it, or set IM_PORT to another port.")
        return False
    env = run_env()
    log = open(LOG, "ab")
    log.write(f"\n---- {time.strftime('%Y-%m-%d %H:%M:%S')} launch.py start ----\n".encode())
    log.flush()
    kw = dict(cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    if WIN:   # own hidden console, so whisper-server windows stay hidden and closing a terminal doesn't kill it
        kw["creationflags"] = 0x08000000 | 0x00000200   # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True                   # like nohup ... &
    subprocess.Popen([venv_python(), "-u", str(ROOT / "server.py"), "--no-open", *server_args], **kw)
    for _ in range(180):                                  # first start can be slow (models load)
        if healthy():
            return True
        time.sleep(0.5)
    alert("College Interview did not start", f"See the log: {LOG}")
    return False


def status():
    p = pid()
    if not alive(p):
        print("not running" + (" (but something answers on port %d)" % PORT if healthy() else ""))
        return
    mem = ""
    try:
        if WIN:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {p}", "/FO", "CSV", "/NH"], capture_output=True,
                                 text=True, creationflags=0x08000000).stdout
            mem = ", " + out.strip().split('","')[-1].strip('"')
        else:
            rss = subprocess.run(["ps", "-o", "rss=", "-p", str(p)], capture_output=True, text=True).stdout
            mem = f", {int(rss.strip()) / 1024:.0f} MB"
    except Exception:
        pass
    print(f"running pid {p}{mem}, {URL}")


def doctor():
    """Plain checklist of everything the app needs."""
    env = run_env()
    py = venv_python()
    rows = []

    def row(ok, what, fix=""):
        rows.append(ok)
        print(f"  [{'ok' if ok else 'MISSING'}] {what}" + ("" if ok else f"\n           fix: {fix}"))

    setup = r"powershell -ExecutionPolicy Bypass -File scripts\setup.ps1" if WIN else "scripts/setup.sh"
    print(f"College Interview check ({ROOT})")
    row(Path(py).exists() and Path(py) != Path(sys.executable) or ".venv" in py, f"app Python: {py}", f"run {setup}")
    code = ("import fastapi, uvicorn, websockets, yaml, sympy, pypdf, numpy, qrcode, sounddevice, soundfile, "
            "silero_vad, speechbrain, torch, kokoro, claude_agent_sdk, spacy; spacy.load('en_core_web_sm')")
    r = subprocess.run([py, "-c", code], capture_output=True, text=True, env=env)
    row(r.returncode == 0, "Python packages", (r.stderr.strip().splitlines() or ["?"])[-1] + f"  ->  run {setup}")
    ws = whisper_server(env)
    row(bool(ws), f"whisper-server: {ws or 'not found'}",
        r"run scripts\setup.ps1 (puts it in tools\whisper)" if WIN else "brew install whisper-cpp")
    for m in ("ggml-large-v3-turbo.bin", "ggml-small.en.bin", "kokoro/kokoro-v1_0.pth", "kokoro/voices/bm_george.pt",
              "ecapa/embedding_model.ckpt", "ecapa/label_encoder.ckpt"):
        row((ROOT / "models" / m).exists(), f"model models/{m}", f"{py} scripts/fetch_models.py")
    cl = shutil.which("claude", path=env["PATH"])
    row(bool(cl), f"Claude Code: {cl or 'not found'}",
        "install Claude Code: " + ("irm https://claude.ai/install.ps1 | iex" if WIN
                                   else "curl -fsSL https://claude.ai/install.sh | bash"))
    if cl:
        try:
            a = subprocess.run([cl, "auth", "status"], capture_output=True, text=True, timeout=20, env=env)
            try:
                import json
                signed = bool(json.loads(a.stdout).get("loggedIn"))
            except ValueError:
                signed = a.returncode == 0 and "not logged in" not in (a.stdout + a.stderr).lower()
        except Exception:
            signed = False
        row(signed, "Claude sign-in", "run: claude auth login")
    print("  [info] server: " + ("running at " + URL if healthy() else "not running"))
    print("All good." if all(rows) else "Fix the MISSING items above, then run this check again.")
    return all(rows)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "open"
    extra = sys.argv[2:]
    if cmd in ("open", "start"):
        if not healthy_slow() and not start(extra):
            sys.exit(1)
        if cmd == "open":
            webbrowser.open(URL)
    elif cmd == "stop":
        stop()
        print("stopped")
    elif cmd == "status":
        status()
    elif cmd == "doctor":
        sys.exit(0 if doctor() else 1)
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
