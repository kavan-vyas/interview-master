"""Whiteboard: iPad (Sidecar) canvas page posts PNG snapshots here; stored per board session with timestamps.

Layout: sessions/_boards/<sid>/<seq>-p<page>.png + index.json [{seq, page, t, file}].
STATE["last"] is what the professor brain (phase 3) reads.
"""
import json
import re
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

import config as C

router = APIRouter()
DIR = C.SESSIONS / "_boards"
SINK = None   # server sets: () -> live session dir or None; each snap is also saved there
STATE = {"sid": None, "n": 0, "last": None, "t": 0.0, "page": 0, "pages": 1}
_SID = re.compile(r"^[0-9A-Za-z_-]{1,40}$")


def _index(sid):
    p = DIR / sid / "index.json"
    return json.loads(p.read_text()) if p.exists() else []


def latest_png():
    """Bytes of the newest snapshot, or None."""
    if not STATE["last"]:
        return None
    p = DIR / STATE["sid"] / STATE["last"]
    return p.read_bytes() if p.exists() else None


@router.post("/api/board/snap")
async def snap(request: Request, sid: str, page: int = 0, pages: int = 1):
    if not _SID.match(sid):
        raise HTTPException(400, "bad sid")
    body = await request.body()
    if not body.startswith(b"\x89PNG") or len(body) > 8_000_000:
        raise HTTPException(400, "png only")
    d = DIR / sid
    d.mkdir(parents=True, exist_ok=True)
    idx = _index(sid)
    seq = len(idx) + 1
    name = f"{seq:04d}-p{page}.png"
    (d / name).write_bytes(body)
    now = time.time()
    idx.append({"seq": seq, "page": page, "t": now, "file": name})
    (d / "index.json").write_text(json.dumps(idx))
    STATE.update(sid=sid, n=seq, last=name, t=now, page=page, pages=pages)
    try:
        sd = SINK() if SINK else None
        if sd:
            bd = sd / "board"
            bd.mkdir(parents=True, exist_ok=True)
            (bd / f"{sid}-{name}").write_bytes(body)
    except Exception as e:
        print("board sink", e)
    return {"ok": True, "n": seq}


@router.get("/api/board/state")
async def state():
    s = dict(STATE)
    s["url"] = f"/api/board/img/{s['sid']}/{s['last']}" if s["last"] else None
    s["age"] = round(time.time() - s["t"], 1) if s["t"] else None
    return s


@router.get("/api/board/timeline/{sid}")
async def timeline(sid: str):
    if not _SID.match(sid):
        raise HTTPException(400)
    return _index(sid)


@router.get("/api/board/img/{sid}/{name}")
async def img(sid: str, name: str):
    if not _SID.match(sid) or not re.match(r"^\d{4}-p\d+\.png$", name):
        raise HTTPException(400)
    p = DIR / sid / name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/png")


MODE = {"v": "fresh"}   # server sets "restore" when a session resumes an earlier one


SEEN = {"t": 0.0, "remote": 0.0}       # last board poll (any) / from another device (the iPad)


@router.get("/api/board/session")
async def session(request: Request):
    """Board page polls this: a new session id -> wipe (fresh) or load the saved board (restore)."""
    import time as _t
    SEEN["t"] = _t.time()
    if (request.client.host if request.client else "") not in ("127.0.0.1", "::1"):
        SEEN["remote"] = SEEN["t"]
    d = SINK() if SINK else None
    return {"session": d.name if d else None, "mode": MODE["v"]}


@router.post("/api/board/doc")
async def doc_put(request: Request, session: str):
    d = SINK() if SINK else None
    if not d or d.name != session:
        raise HTTPException(409, "not the live session")
    body = await request.body()
    if len(body) > 20_000_000:
        raise HTTPException(413)
    (d / "board").mkdir(parents=True, exist_ok=True)
    (d / "board" / "doc.json").write_bytes(body)
    return {"ok": True}


@router.get("/api/board/doc")
async def doc_get(session: str):
    d = SINK() if SINK else None
    f = d / "board" / "doc.json" if d and d.name == session else None
    if not f and re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}(-\d{2})?(-\d+)?", session):   # read-only view of a past board
        f = C.SESSIONS / session / "board" / "doc.json"
    if not f or not f.exists():
        raise HTTPException(404)
    return FileResponse(f, media_type="application/json")


DEBUG = []


@router.post("/api/board/debug")
async def debug(request: Request):
    DEBUG.append(json.loads(await request.body()))
    del DEBUG[:-30]
    return {"ok": True}


@router.get("/api/board/debug")
async def debug_get():
    return DEBUG[-10:]


@router.get("/board")
async def page():
    """tldraw scribe pad (bundled offline, stuck-pen watchdog). Plain canvas fallback at /board/simple."""
    return FileResponse(C.ROOT / "static" / "tl" / "index.html", headers={"Cache-Control": "no-cache"})


@router.get("/board/simple")
async def simple():
    return FileResponse(C.ROOT / "static" / "board.html", headers={"Cache-Control": "no-cache"})
