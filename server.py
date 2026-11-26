"""Interview Master: FastAPI + WebSocket. Run:  python3 server.py [--sim sim/demo.txt] [--no-audio]"""
import argparse
import asyncio
import base64
import io
import os
import re
import signal
import sys
import time
from pathlib import Path
import webbrowser

import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import ai
import board
import professor
import progress
import shutil
import json
import audio
import carts
import config as C
import report
import reports
from orchestrator import Orchestrator

app = FastAPI()

# ---- remote access (iPad's own Safari over home Wi-Fi / Tailscale) ----
# Localhost is always allowed. Anyone else needs the secret key once (?k=...), which sets a cookie.
import secrets
import subprocess
from fastapi import Request as _Rq
from fastapi.responses import PlainTextResponse
KEY_FILE = C.SESSIONS / "_remote_key.txt"
if not KEY_FILE.exists():
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_text(secrets.token_urlsafe(12))
REMOTE_KEY = KEY_FILE.read_text().strip()


_HOSTS = {"ips": set(), "t": 0.0}


def _local_ips():
    """LAN + Tailscale IPs, refreshed in the background every 30 s (tailscale CLI is slow: never on the request path)."""
    import threading
    if time.time() - _HOSTS["t"] > 30:
        _HOSTS["t"] = time.time()
        threading.Thread(target=lambda: _HOSTS.update(ips={ip for ip in (_lan_ip(), _ts_ip()) if ip}), daemon=True).start()
    return _HOSTS["ips"]


def _host_ok(h):
    """Host header we answer to: localhost, LAN / Tailscale IP, *.ts.net (blocks DNS rebinding)."""
    h = (h or "").strip().lower()
    h = h[1:].split("]")[0] if h.startswith("[") else (h.rsplit(":", 1)[0] if h.count(":") == 1 else h)
    return bool(h) and (h in ("localhost", "127.0.0.1", "::1") or h.endswith(".ts.net") or h in _local_ips() or h == _lan_ip())


def _origin_ok(o):
    """No Origin (same-origin GET, img, curl) is fine; otherwise it must be one of our own pages."""
    if not o:
        return True
    from urllib.parse import urlsplit
    try:
        u = urlsplit(o)
        hn, port = u.hostname or "", u.port
    except ValueError:
        return False
    return u.scheme in ("http", "https") and _host_ok(hn) and (hn.endswith(".ts.net") or port == C.PORT)


@app.middleware("http")
async def _remote_guard(request: _Rq, call_next):
    host = request.client.host if request.client else ""
    if host in ("127.0.0.1", "::1", "localhost"):
        if not _host_ok(request.headers.get("host")) or not _origin_ok(request.headers.get("origin")):
            return PlainTextResponse("forbidden", status_code=403)     # another site driving the local API
        return await call_next(request)
    k = request.query_params.get("k")
    if k == REMOTE_KEY or request.cookies.get("imk") == REMOTE_KEY:
        resp = await call_next(request)
        if k == REMOTE_KEY:
            resp.set_cookie("imk", REMOTE_KEY, max_age=60 * 60 * 24 * 180, httponly=True, samesite="strict")
        return resp
    return PlainTextResponse("forbidden", status_code=403)
O = Orchestrator()
board.SINK = lambda: report.session_dir(O) if O.s["phase"] not in ("setup", "done") else None


def _lan_ip():
    import socket
    try:
        k = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); k.connect(("10.255.255.255", 1)); ip = k.getsockname()[0]; k.close()
        return ip
    except Exception:
        return None


def _ts_ip():
    try:
        return subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=3).stdout.split()[0]
    except Exception:
        return None


@app.get("/api/remote")
async def remote(request: _Rq):
    """iPad board links (LAN + Tailscale) with the access key. Mac only."""
    if (request.client.host if request.client else "") not in ("127.0.0.1", "::1"):
        raise HTTPException(403)
    port = request.url.port or 8000
    u = lambda ip: f"http://{ip}:{port}/board?k={REMOTE_KEY}" if ip else None
    return {"lan": u(_lan_ip()), "ts": u(_ts_ip())}


@app.get("/api/remote/qr")
async def remote_qr(request: _Rq, which: str = "lan"):
    import io, qrcode
    from fastapi.responses import Response
    j = await remote(request)
    if not j.get(which):
        raise HTTPException(404, "no address")
    b = io.BytesIO(); qrcode.make(j[which], box_size=6, border=2).save(b, "PNG")
    return Response(b.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/board/manifest.json")
async def board_manifest():
    """Home-screen app: fullscreen, and start URL carries the key (home-screen apps get their own cookie jar)."""
    return {"name": "Interview board", "short_name": "Board", "display": "fullscreen", "orientation": "any",
            "start_url": f"/board?k={REMOTE_KEY}", "background_color": "#ffffff", "theme_color": "#111613",
            "icons": [{"src": "/static/favicon.svg", "sizes": "any", "type": "image/svg+xml"}]}
ARGS = None
LOOP = None
_level = {"v": 0.0}
WPROC = None


# ------------------------------------------------------------------ lifecycle
# One owner: this process. Launcher (scripts/ci.sh) starts it; it stops itself when the last Mac tab closes
# (8 s grace so a reload survives) or when no tab ever connects (orphan guard). Every exit runs _cleanup().
# Mid-interview the server never stops: a closed tab pauses the interview (saved, resumable) and the server waits
# LIVE_GRACE_S for a tab to come back; only then it writes the report, keeps the session resumable, and stops.
_PIDDIR = Path.home() / "Library" / "Application Support" / "CollegeInterview"
if sys.platform == "win32":    # same folder launch.py reads
    _PIDDIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "CollegeInterview"
PIDFILE = _PIDDIR / ("server.pid" if C.PORT == 8000 else f"server-{C.PORT}.pid")   # a test copy never clobbers the app's
GRACE_S, ORPHAN_S = 8, int(os.environ.get("IM_ORPHAN_S", 120))
LIVE_GRACE_S = int(os.environ.get("IM_LIVE_GRACE_S", 1800))
LIVE_PHASES = ("personal", "warmup", "main", "wrapup")
_life = {"seen": False, "empty_at": None, "boot": time.time(), "done": False, "engines": False}


def _cleanup():
    """Idempotent: save, close the mic, kill every speech engine this app runs, drop the pidfile."""
    if _life["done"]:
        return
    _life["done"] = True
    try:
        if O.s.get("phase") not in ("setup", "done"):
            O.s["interrupted"] = True             # resumable from the start screen
            if not O.report_md:                   # no time for the AI narrative: checklist + stats now
                O.report_md = report.quick_md(O)
            report.save(O)
            report.autosave(O)
    except Exception as e:
        print("exit autosave failed:", e, flush=True)
    try:
        if O.audio:
            O.audio.stop()
    except Exception:
        pass
    for pr in (WPROC, audio._partial.get("proc"), audio._mlx.get("proc")):
        try:
            pr and pr.terminate()
        except Exception:
            pass
    if _life["engines"] and sys.platform != "win32":     # (Windows: launch.py stop kills the tree) engines reused from an earlier run aren't our Popen: match them by this app's model paths
        import subprocess    # (only if THIS instance ran the speech engines: a no-audio / failed second copy never does)
        subprocess.run(["pkill", "-f", str(C.MODELS / "ggml-")], check=False)
        subprocess.run(["pkill", "-f", str(C.ROOT / "tts" / "stt_worker.py")], check=False)
    try:
        if PIDFILE.read_text().strip() == str(os.getpid()):
            PIDFILE.unlink()
    except Exception:
        pass
    print("stopped cleanly", flush=True)


def _mac_tabs():
    return sum(1 for w in O.clients if (w.client.host if w.client else "") in ("127.0.0.1", "::1"))


def _busy_report():
    t = _END.get("task")
    return bool(t and not t.done()) or bool(O.s.get("report_busy"))


async def _interrupted_report():
    """No tab for LIVE_GRACE_S mid-interview: write the full report but keep the session resumable."""
    try:
        O.s["interrupted"] = True
        await report.build(O)
        d = report.save(O)
        report.autosave(O)
        _keep_board(d)
        print("interrupted session saved with report:", d, flush=True)
    except Exception as e:
        print("interrupted report failed:", e, flush=True)


async def life_watch():
    while True:
        await asyncio.sleep(1)
        n, now = _mac_tabs(), time.time()
        if n != _life.get("n"):
            _life["n"] = n
            print("mac tabs:", n, [w.client.host if w.client else None for w in O.clients], flush=True)
        if n:
            _life["seen"], _life["empty_at"] = True, None
            continue
        if not _life["seen"]:
            if O.s.get("audio") == "loading":     # engines still loading: never kill them mid-start
                _life["boot"] = now
            elif now - _life["boot"] > ORPHAN_S and O.s.get("phase") in ("setup", "done") and not _busy_report():
                print("no tab opened in 2 min: stopping", flush=True)
                break
            continue
        _life["empty_at"] = _life["empty_at"] or now
        gone = now - _life["empty_at"]
        if _busy_report():
            continue                              # report being written: always let it finish
        if O.s.get("phase") not in LIVE_PHASES:
            if gone >= GRACE_S:
                print("last tab closed: stopping", flush=True)
                break
            continue
        if gone >= GRACE_S and O.s["phase"] != "wrapup" and not O.s.get("paused"):
            print("last tab closed mid-interview: paused and saved, waiting for a tab", flush=True)
            O.note("tab_closed")
            await O.pause(True, auto=True)
        if gone >= LIVE_GRACE_S:
            print(f"no tab for {LIVE_GRACE_S // 60} min mid-interview: writing the report, then stopping", flush=True)
            await _interrupted_report()
            break
    await O.send({"type": "bye"})              # open tabs show "closed" and stop reconnecting
    await asyncio.to_thread(_cleanup)
    os.kill(os.getpid(), signal.SIGTERM)       # uvicorn shuts down; bye() is a no-op after _cleanup


@app.on_event("shutdown")
async def bye():
    try:
        await O.send({"type": "bye"})          # ci.sh stop / Ctrl-C: tabs go to "closed" too
    except Exception:
        pass
    await asyncio.gather(ai.GATE.reset(), ai.WRITER.reset())
    await asyncio.to_thread(_cleanup)


# ------------------------------------------------------------------ audio bridge
def _threadsafe(coro_fn):
    def f(*a):
        if LOOP:
            asyncio.run_coroutine_threadsafe(coro_fn(*a), LOOP)
    return f


async def _seg(s):
    if "error" in s:
        await O.toast(s["error"], "warn")
    else:
        if O.s.get("solo"):
            if not O.barge_ok() and professor.is_echo(s) and not professor.outlasted(s):
                return            # our own voice picked up by the mic
            clean = professor.strip_echo(s)       # his words at the start/inside a student segment: cut, keep theirs
            if clean != s.get("text"):
                print(f"[echo] cut professor words: {s.get('text', '')[:60]!r} -> {clean[:60]!r}", flush=True)
                if not clean:
                    return
                s["text"] = clean
            s["speaker"] = "S"
        s.pop("t_end", None)
        await O.add_segment(s)


async def _enrolled(role):
    if role not in O.s["enrolled"]:
        O.s["enrolled"].append(role)
    await O.send({"type": "enrolled", "role": role})
    await O.push_state()


async def level_pump():
    last = None
    while True:
        await asyncio.sleep(0.1)
        if _level["v"] != last:
            last = _level["v"]
            await O.send({"type": "level", "v": round(last, 4)})


async def _partial(seq, text):
    if O.s.get("phase") in ("personal", "warmup", "main") and not O.s.get("mute"):
        await O.send({"type": "partial", "seq": seq, "text": text})


async def start_audio():
    ok, why = audio.deps_ok()
    if not ok or ARGS.no_audio:
        O.s["audio"] = "off"
        print(f"audio off ({why or '--no-audio'}). Use typed input or --sim.")
        return
    p = audio.Pipeline(_threadsafe(_seg), lambda: LOOP.call_soon_threadsafe(O.activity),
                       lambda v: _level.__setitem__("v", v), _threadsafe(_enrolled))
    O.s["audio"] = "loading"
    try:
        global WPROC
        _life["engines"] = True
        WPROC = await asyncio.to_thread(audio.ensure_whisper)
        await asyncio.to_thread(p.start)
        O.audio, O.s["audio"] = p, "live"
        p.on_partial = _threadsafe(_partial)
        print("audio live")
    except Exception as e:
        O.s["audio"] = "off"
        print("audio failed:", e)
    await O.push_state()


async def sim_play(path):
    """Lines like `+6 S: text`  (wait 6 s, then speaker S says text)."""
    for line in open(path):
        m = re.match(r"\+(\d+(?:\.\d+)?)\s+([IS?]):\s*(.+)", line.strip())
        if not m:
            continue
        await asyncio.sleep(float(m.group(1)))
        while O.s["phase"] not in ("personal", "warmup", "main"):
            await asyncio.sleep(0.5)
        await O.add_segment({"speaker": m.group(2), "text": m.group(3), "conf": 1.0, "spk_conf": 1.0})


@app.on_event("startup")
async def boot():
    global LOOP
    LOOP = asyncio.get_running_loop()
    asyncio.create_task(O.tick_loop())
    _life["boot"] = time.time()                # orphan clock starts when the server can answer
    if not (ARGS and ARGS.keep_alive):
        asyncio.create_task(life_watch())
    PIDFILE.parent.mkdir(parents=True, exist_ok=True)
    PIDFILE.write_text(str(os.getpid()))
    asyncio.create_task(level_pump())
    asyncio.create_task(start_audio())
    asyncio.create_task(asyncio.to_thread(professor.warm))
    if ARGS and ARGS.sim:
        asyncio.create_task(sim_play(ARGS.sim))


# ------------------------------------------------------------------ routes
@app.websocket("/ws")
async def ws(sock: WebSocket):
    host = sock.client.host if sock.client else ""
    if host not in ("127.0.0.1", "::1") and sock.cookies.get("imk") != REMOTE_KEY:
        await sock.close(code=1008)
        return
    if host in ("127.0.0.1", "::1") and not (_host_ok(sock.headers.get("host")) and _origin_ok(sock.headers.get("origin"))):
        await sock.close(code=1008)           # cross-site page opening our socket
        return
    await sock.accept()
    O.clients.add(sock)
    if O.s["phase"] == "done" and getattr(O, "report_md", None):
        d = getattr(O, "report_dir", None)
        asyncio.get_running_loop().call_later(0.3, lambda: asyncio.ensure_future(_report_msg(O.report_md, d)))
    cs = await asyncio.to_thread(carts.load_all)     # YAML re-parse (seconds) never blocks the interview loop
    await sock.send_json({"type": "hello", "state": O.snapshot(), "cards": O.cards,
                          "transcript": O.transcript[-200:], "carts": [carts.summary(c) for c in cs.values()]})
    try:
        while True:
            await sock.receive_text()
    except WebSocketDisconnect:
        O.clients.discard(sock)


@app.get("/api/cartridges")
async def list_carts():
    return [carts.summary(c) for c in (await asyncio.to_thread(carts.load_all)).values()]


@app.get("/api/cartridge/{cid}")
async def get_cart(cid: str):
    c = carts.load(cid)
    if not c:
        raise HTTPException(404)
    return {"cart": c, "yaml": yaml.safe_dump(c, sort_keys=False, allow_unicode=True)}


TEST_PIN = C.SESSIONS / "_test_cart.txt"   # while testing: every session uses this problem (delete file to unpin)


@app.post("/api/setup")
async def setup(body: dict):
    if TEST_PIN.exists() and TEST_PIN.read_text().strip():
        body["main_id"], body["warmup_id"] = TEST_PIN.read_text().strip(), None
    await O.configure(body)
    rs = O.s.get("resume")
    if rs and rs.get("main_id") and rs["main_id"] != O.s.get("main_id"):
        O.s.pop("resume", None)                  # problem changed since: drop the stale resume
    mid = body.get("main_id") or O.s.get("main_id")   # barge-only posts carry no main_id
    if TEST_PIN.exists() and "resume" not in O.s and mid and O.s["phase"] == "setup":
        rs = _last_progress(mid)
        if rs:
            O.s["resume"] = rs
    return {"ok": True}


@app.post("/api/start")
async def start():
    if O.s["phase"] != "setup":
        raise HTTPException(409, "session already running: end or reset first")
    resuming = "resume" in O.s
    board.MODE["v"] = "restore" if resuming else "fresh"
    try:
        await O.start()
    except ValueError as e:
        raise HTTPException(400, str(e))
    if resuming:
        _restore_board()
    return {"ok": True}


def _last_progress(cid):
    """Newest earlier session on this problem that got going: where it stopped (pinned test problem continues)."""
    for d in sorted((p for p in C.SESSIONS.iterdir() if p.is_dir() and (p / "live.json").exists()), reverse=True):
        try:
            s = json.loads((d / "live.json").read_text()).get("s", {})
        except Exception:
            continue
        if s.get("main_id") != cid:
            continue
        if s.get("phase") != "main" or s.get("elapsed", 0) < 60:
            continue                     # ended properly / never started: keep looking
        est = list(s.get("established") or [])
        if s.get("last_note"):
            est.append("Last time: " + str(s["last_note"])[:300])
        for e in (s.get("pending_errors") or [])[:2]:
            est.append("Unresolved slip from last time: " + str(e.get("note", ""))[:200])
        return {"stage": int(s.get("stage", 0)), "established": est, "main_id": cid}
    return None


def _restore_board():
    """Resume: copy the newest earlier board doc for this problem into the new session folder."""
    cur = report.session_dir(O)
    for d in sorted((p for p in C.SESSIONS.iterdir() if p.is_dir() and p != cur and (p / "board" / "doc.json").exists()), reverse=True):
        try:
            if json.loads((d / "live.json").read_text()).get("main_id") == O.s.get("main_id"):
                (cur / "board").mkdir(parents=True, exist_ok=True)
                shutil.copy(d / "board" / "doc.json", cur / "board" / "doc.json")
                return
        except Exception:
            continue


@app.post("/api/action/{name}")
async def action(name: str):
    await O.action(name)
    return {"ok": True}


@app.post("/api/slider")
async def slider(body: dict):
    await O.set_slider(body["value"])
    return {"ok": True}


@app.post("/api/personal")
async def personal(body: dict):
    O.s["personal_on"] = bool(body.get("on"))
    await O.push_state()
    return {"ok": True}


@app.post("/api/seg/{sid}/speaker")
async def seg_speaker(sid: int, body: dict):
    await O.fix_speaker(sid, body["speaker"])
    return {"ok": True}


@app.post("/api/inject")
async def inject(body: dict):
    if O.s.get("paused"):
        return {"ok": False, "paused": True}      # paused: nothing is recorded (the type bar is hidden too)
    await O.add_segment({"speaker": body.get("speaker", "S"), "text": str(body["text"])[:800], "conf": 1.0, "spk_conf": 1.0,
                         "typed": bool(body.get("typed"))})
    return {"ok": True}


@app.post("/api/enrol")
async def enrol(body: dict):
    if not O.audio:
        raise HTTPException(409, "audio is off")
    O.audio.enrol(body["role"])
    return {"ok": True, "seconds": C.ENROL_S}


@app.post("/api/statement")
async def statement(body: dict):
    from pypdf import PdfReader
    raw = base64.b64decode(body["b64"].split(",")[-1])
    try:
        text = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(raw)).pages) \
            if body.get("name", "").lower().endswith(".pdf") else raw.decode("utf-8", "ignore")
    except Exception as e:
        raise HTTPException(400, f"could not read: {e}")
    O.personal.update(statement=text.strip(), name=body.get("name", ""))
    return {"ok": True, "chars": len(text)}


PREBRIEF = """You are pre-briefing a strong-at-maths interviewer before a Cambridge maths mock interview.
Cartridge (YAML):
{yaml}
Talk through the solution path and the stages. Be concise. Flag anything in the cartridge that looks wrong,
especially if answer_source is generated. Suggest stage splits, hints (never revealing the answer), and extensions."""

COMMIT = """Apply every change agreed in this conversation to the cartridge below.
Reply ONLY with the full updated cartridge as JSON (same schema; include "check" sympy expressions on stages
where the answer is computable).
Cartridge:
{yaml}"""


@app.post("/api/prebrief/chat")
async def prebrief_chat(body: dict):
    c = carts.load(body["cartridge_id"])
    if not c:
        raise HTTPException(404)
    try:
        reply = await ai.chat(PREBRIEF.format(yaml=yaml.safe_dump(c, sort_keys=False)), body["messages"])
    except Exception as e:
        raise HTTPException(502, str(e))
    return {"reply": reply}


@app.post("/api/prebrief/commit")
async def prebrief_commit(body: dict):
    c = carts.load(body["cartridge_id"])
    if not c:
        raise HTTPException(404)
    try:
        raw = await ai.chat(COMMIT.format(yaml=yaml.safe_dump(c, sort_keys=False)), body.get("messages", []))
        new = ai.extract_json(raw)
    except Exception as e:
        raise HTTPException(502, str(e))
    if not isinstance(new, dict):
        raise HTTPException(502, "model returned no cartridge")
    new["id"], new["verified"] = c["id"], True
    checks = []
    for i, st in enumerate(new.get("stages", [])):
        if st.get("check"):
            st["check_ok"] = await asyncio.to_thread(carts.verify, st["check"])
            checks.append({"stage": i + 1, "ok": st["check_ok"]})
    if any(x["ok"] is False for x in checks):
        new["verified"] = False
    carts.save(new)
    return {"ok": True, "verified": new["verified"], "checks": checks}


@app.post("/api/verify/{cid}")
async def mark_verified(cid: str):
    c = carts.load(cid)
    if not c:
        raise HTTPException(404)
    c["verified"] = True
    carts.save(c)
    return {"ok": True}


async def _report_msg(md, d):
    await O.send({"type": "report", "md": md, "dir": str(d.relative_to(C.ROOT)) if d else None})


_END = {"task": None, "gen": None}


@app.post("/api/end")
async def end(body: dict = None):
    """End -> one report. A second End (or time-up auto-end) while it is being written waits for the same one."""
    if O.s["phase"] == "setup":
        raise HTTPException(409, "no session running")
    if O.s["phase"] == "done":
        return {"ok": True, "saved": bool(O.report_dir)}
    t = _END.get("task")
    if not (t and not t.done() and _END.get("gen") == O.gen):
        _END["task"], _END["gen"] = asyncio.ensure_future(_end()), O.gen
        t = _END["task"]
    return await asyncio.shield(t)


async def _end():
    gen = O.gen                                  # a reset / new session during the slow report must not be touched
    await professor.shh()
    O.s["report_busy"] = True                    # a reload while it is written lands on the report loader
    if O.s["phase"] != "wrapup":
        await O.enter("wrapup")
    await O.send({"type": "report_busy"})
    try:
        md = await report.build(O)
    except BaseException:
        if O.gen == gen:
            O.s["report_busy"] = False
        raise
    if O.gen != gen:
        return {"ok": False, "stale": True}
    d = report.save(O)
    O.s["phase"] = "done"
    O.s["report_busy"] = False
    O.s.pop("interrupted", None)
    try:
        report.autosave(O)                       # live.json says done: never offered as a resume
    except Exception as e:
        print("autosave failed:", e, flush=True)
    try:
        if O.progress_row is None and O.now() >= 120:     # once per session; skip aborted runs
            O.progress_row = progress.record(O)
    except Exception as e:
        print("progress", e)
    _keep_board(d)
    await O.push_state()
    await _report_msg(md, d)
    return {"ok": True, "saved": bool(d)}


@app.post("/api/report/retry")
async def report_retry():
    gen = O.gen
    await O.send({"type": "report_busy"})
    md = await report.build(O)
    if O.gen != gen:
        return {"ok": False, "stale": True}
    d = report.save(O)
    await _report_msg(md, d)
    return {"ok": True}


@app.post("/api/save")
async def save_now():
    """Consent given after the fact: write the report already on screen."""
    O.s["consent"] = True
    d = report.save(O)
    _keep_board(d)
    await _report_msg(getattr(O, "report_md", ""), d)
    return {"ok": bool(d)}


def _keep_board(d):
    """Saved session: copy board snapshots + replay data next to the report."""
    sid = board.STATE["sid"]
    if not d or not sid:
        return
    try:
        if not any((d / "board").glob(f"{sid}-*.png")):      # live sink already saved this session's shots
            t_start = (O.t0 or 0) - O.s.get("paused_s", 0) - 5    # board page outlives sessions: this one's shots only
            idx = [e for e in board._index(sid) if e.get("t", 0) >= t_start]
            if idx:
                (d / "board").mkdir(parents=True, exist_ok=True)
                for e in idx:
                    if (board.DIR / sid / e["file"]).exists():
                        shutil.copy2(board.DIR / sid / e["file"], d / "board" / e["file"])
                (d / "board" / "index.json").write_text(json.dumps(idx))
        (d / "replay.json").write_text(json.dumps({"t0": O.t0, "transcript": O.transcript, "board_sid": sid}, default=str))
        if getattr(O, "walk_md", None):
            (d / "model_walk.md").write_text(O.walk_md)
    except Exception as e:
        print("keep board", e)


@app.get("/api/progress")
async def progress_api():
    return {"rows": progress.load()[-50:], "topics": progress.topics()}


@app.get("/api/mic")
async def mic_list():
    if not O.audio:
        return {"devices": [], "current": None, "audio": O.s["audio"]}
    return {"devices": audio.Pipeline.inputs(), "current": getattr(O.audio, "device_name", None), "audio": O.s["audio"]}


def _spk(*a):
    try:
        return subprocess.run([shutil.which("SwitchAudioSource") or "/opt/homebrew/bin/SwitchAudioSource", *a], capture_output=True, text=True, timeout=4).stdout.strip()
    except Exception:
        return ""


@app.get("/api/speaker")
async def spk_list():
    return {"devices": [d for d in _spk("-a", "-t", "output").splitlines() if d], "current": _spk("-c", "-t", "output")}


@app.post("/api/speaker")
async def spk_set(body: dict):
    name = str(body.get("name", ""))
    if name not in (await spk_list())["devices"]:
        raise HTTPException(400, "unknown output device")
    _spk("-s", name, "-t", "output")
    return {"ok": True, "current": name}


@app.post("/api/mic/refresh")
async def mic_refresh():
    """Re-scan devices (new webcam/headphones); switch to the preferred mic (webcam first)."""
    if O.audio:
        await asyncio.to_thread(O.audio.open)
    return {**(await mic_list()), "speakers": await spk_list()}


@app.post("/api/mic")
async def mic_set(body: dict):
    if not O.audio:
        raise HTTPException(409, "audio is off")
    name = str(body.get("name", ""))
    if name not in audio.Pipeline.inputs():
        raise HTTPException(400, "unknown input device")
    await asyncio.to_thread(O.audio.choose, name)
    return {"ok": True, "current": name}


@app.post("/api/resume_at")
async def resume_at(body: dict):
    """Next Begin continues the main problem at part `stage` (0-based) with known results carried over."""
    if O.s["phase"] != "setup":
        raise HTTPException(409, "end or reset the current session first")
    O.s["resume"] = {"stage": int(body.get("stage", 0)), "established": body.get("established", []),
                     "main_id": body.get("main_id") or O.s.get("main_id")}
    return {"ok": True}


@app.post("/api/pause")
async def pause(body: dict):
    await O.pause(bool(body.get("on")))
    return {"ok": True, "paused": O.s["paused"]}


@app.post("/api/repeat")
async def repeat():
    await professor.repeat_last(O)
    return {"ok": True}


@app.post("/api/shh")
async def shh():
    await professor.shh()
    return {"ok": True}


@app.post("/api/say")
async def say_text(body: dict):
    if body.get("voice"):
        professor.state["voice"] = body["voice"]
    asyncio.create_task(professor.say(str(body.get("text", ""))[:600]))
    return {"ok": True}


@app.post("/api/mute")
async def mute_toggle(body: dict):
    """Switch a running solo interview between spoken and mute (write + type) mid-session."""
    O.s["mute"] = bool(body.get("on"))
    O.last_speech, O.fired = time.time(), set()        # silence steps restart from now, no burst
    O._mute_hint = time.time()                         # nor an instant "board unchanged" hint
    O.note("mute", on=O.s["mute"])
    await O.push_state()
    return {"ok": True, "mute": O.s["mute"]}


@app.post("/api/spk")
async def spk_toggle(body: dict):
    """Speaker on/off during any interview (off = captions only)."""
    professor.state["speaker_off"] = not bool(body.get("on", True))
    if professor.state["speaker_off"]:
        professor.stop_voice()                   # sound off only: queued lines still come up as captions
    await O.push_state()
    return {"ok": True, "on": not professor.state["speaker_off"]}


@app.post("/api/voice")
async def set_voice(body: dict):
    professor.state["voice"] = body.get("voice") or professor.VOICE
    if body.get("speed"):
        professor.state["speed"] = max(0.8, min(1.4, float(body["speed"])))
    return {"ok": True}


@app.post("/api/solution_walk")
async def solution_walk():
    try:
        md = await professor.model_walk(O)
    except Exception as e:
        raise HTTPException(500, str(e)[:200])
    O.walk_md = md
    return {"md": md}


@app.get("/api/replay")
async def replay():
    sid = board.STATE["sid"]
    return {"t0": O.t0, "transcript": O.transcript, "board_sid": sid,
            "board": board._index(sid) if sid else []}


def _interrupted_list():
    """Sessions that stopped mid-interview (tab closed / server stopped): newest first."""
    out, cur = [], O.report_dir if O.s["phase"] != "setup" else None
    for d in sorted((p for p in C.SESSIONS.iterdir() if p.is_dir() and reports.ID_RE.match(p.name) and (p / "live.json").exists()), reverse=True)[:40]:
        if cur and d == cur:
            continue
        try:
            s = json.loads((d / "live.json").read_text()).get("s", {})
        except Exception:
            continue
        if s.get("phase") not in LIVE_PHASES or s.get("dismissed") or not s.get("main_id"):
            continue
        if not s.get("interrupted") and (d / "report.md").exists():
            continue
        if float(s.get("elapsed") or 0) < 20:
            continue
        c = carts.load(s["main_id"]) or {}
        out.append({"id": d.name, "title": c.get("title") or s["main_id"], "elapsed": int(s.get("elapsed") or 0),
                    "limit_s": s.get("limit_s") or 0, "stage": int(s.get("stage") or 0), "stages": len(c.get("stages") or []),
                    "mute": bool(s.get("mute")), "solo": bool(s.get("solo")), "report": (d / "report.md").exists()})
        if len(out) >= 5:
            break
    return out


def _interrupted_dir(rid):
    if not reports.ID_RE.match(rid) or not (C.SESSIONS / rid / "live.json").exists():
        raise HTTPException(404, "no such session")
    if O.s["phase"] != "setup":
        raise HTTPException(409, "end or reset the current session first")
    return C.SESSIONS / rid


@app.get("/api/interrupted")
async def interrupted():
    return await asyncio.to_thread(_interrupted_list)


@app.post("/api/interrupted/{rid}/resume")
async def interrupted_resume(rid: str):
    """Carry on where it stopped: same folder, same part, same clock. Starts paused (press Resume)."""
    d = _interrupted_dir(rid)
    await professor.shh()
    O.restore(d)
    board.MODE["v"] = "restore"
    await O.send({"type": "phase", "phase": O.s["phase"], "title": (O.cart or {}).get("title", "")})
    await O.push_state()
    if O.s.get("solo"):
        asyncio.ensure_future(professor.warm_brains())
    return {"ok": True}


@app.post("/api/interrupted/{rid}/report")
async def interrupted_report(rid: str):
    """Write the full report for an interrupted session (it is then finished, no longer resumable)."""
    d = _interrupted_dir(rid)
    O.restore(d)
    return await end({})


@app.post("/api/interrupted/{rid}/dismiss")
async def interrupted_dismiss(rid: str):
    d = _interrupted_dir(rid)
    f = d / "live.json"
    j = json.loads(f.read_text())
    j.setdefault("s", {})["dismissed"] = True
    f.write_text(json.dumps(j, default=str))
    return {"ok": True}


@app.post("/api/reset")
async def reset():
    await professor.shh()
    O.reset()
    if O.audio:
        O.s["audio"] = "live"
    await O.push_state()
    return {"ok": True}


app.include_router(reports.router)
app.include_router(board.router)


@app.get("/")
async def index():
    return FileResponse(C.ROOT / "static" / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(C.ROOT / "static" / "favicon.ico")


app.mount("/static", StaticFiles(directory=C.ROOT / "static"), name="static")


O.auto_end = lambda: end({})                   # time's up -> report without pressing End


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", help="script file to replay as speech")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--keep-alive", action="store_true", help="don't stop when the last tab closes")
    ARGS = ap.parse_args()
    import socket as _so                       # port taken (app already running): leave without side effects -
    _probe = _so.socket(_so.AF_INET, _so.SOCK_STREAM)   # no pidfile write, no engine cleanup, no browser tab
    _probe.setsockopt(_so.SOL_SOCKET, _so.SO_REUSEADDR, 1)   # like uvicorn: TIME_WAIT is fine, a listener is not
    try:
        _probe.bind((os.environ.get("IM_HOST", "0.0.0.0"), C.PORT))
    except OSError as e:
        print(f"port {C.PORT} is in use ({e}): College Interview is probably already running. Not starting a second copy.", flush=True)
        raise SystemExit(1)
    finally:
        _probe.close()
    if not ARGS.no_open:
        LOOP_OPEN = lambda: webbrowser.open(f"http://localhost:{C.PORT}")  # noqa: E731
        import threading
        threading.Timer(1.2, LOOP_OPEN).start()
    import atexit
    atexit.register(_cleanup)                  # Ctrl-C / kill: engines and mic still go
    if sys.platform != "win32":                # Windows has no SIGHUP
        signal.signal(signal.SIGHUP, lambda *a: os.kill(os.getpid(), signal.SIGTERM))
    uvicorn.run(app, host=os.environ.get("IM_HOST", "0.0.0.0"), port=C.PORT, log_level="warning", timeout_graceful_shutdown=2)

