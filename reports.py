"""Past sessions archive: list, read, per-report notes + categories. All local files under sessions/."""
import json
import re
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

import config as C

router = APIRouter(prefix="/api")
CATS = C.SESSIONS / "categories.json"
ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}(-\d+)?$")
DEFAULT_CATS = [
    {"name": "Strong", "color": "#5cffaa"}, {"name": "Needs work", "color": "#ff5470"},
    {"name": "Proof", "color": "#b58cff"}, {"name": "Algebra", "color": "#4de2ff"},
    {"name": "Geometry", "color": "#ffb547"}, {"name": "Follow up", "color": "#b6ff3b"},
]


def _listed(d):
    """In the archive: a report, a saved board, or a recorded transcript (interrupted session, no report yet)."""
    return ((d / "report.md").exists() or (d / "board" / "doc.json").exists()
            or ((d / "live.json").exists() and (d / "transcript.jsonl").exists() and (d / "transcript.jsonl").stat().st_size > 2))


def _dir(rid):
    if not ID_RE.match(rid):
        raise HTTPException(400, "bad report id")
    d = C.SESSIONS / rid
    if not _listed(d):
        raise HTTPException(404, "no such report")
    return d


def _meta(d):
    f = d / "meta.json"
    try:
        return json.loads(f.read_text()) if f.exists() else {}
    except json.JSONDecodeError:
        return {}


def _summary(d):
    md = _md(d)
    m = _meta(d)
    head = re.search(r"\n\n(.+?)\n", md)
    line = head.group(1) if head and (d / "report.md").exists() else ""
    main = re.search(r"main: `([^`]*)`", line)
    if not main and (d / "live.json").exists():          # board-only session (ended without a report)
        try:
            mid = json.loads((d / "live.json").read_text()).get("main_id") or ""
            main = re.search(r"(.*)", mid) if mid else None
        except json.JSONDecodeError:
            pass
        line = line or time.strftime("%d %B %Y, %H:%M", time.strptime(d.name[:16], "%Y-%m-%d_%H-%M"))
    dur = re.search(r"· (\d+:\d+) total", line)
    lvl = re.search(r"final level (\d)/5", line)
    return {"id": d.name, "title": m.get("title") or (main.group(1).replace("-", " ") if main else d.name),
            "when": line.split(" · ")[0] if line else d.name, "duration": dur.group(1) if dur else "",
            "level": int(lvl.group(1)) if lvl else None, "main": main.group(1) if main else "",
            "categories": m.get("categories", []), "notes": (m.get("notes") or "")[:140],
            "updated": m.get("updated"), "board": (d / "board" / "doc.json").exists()}


def _md(d):
    f = d / "report.md"
    if f.exists():
        return f.read_text()
    if (d / "board" / "doc.json").exists():
        return "# No report\n\n_This session ended without a report. Its whiteboard is saved: press **Whiteboard**._\n"
    return ("# No report yet\n\n_This interview was interrupted before its report. Its transcript is below. "
            "On the start screen you can resume it or write its report._\n")


@router.get("/reports")
async def list_reports():
    ds = [d for d in C.SESSIONS.iterdir() if d.is_dir() and ID_RE.match(d.name) and _listed(d)]
    return [_summary(d) for d in sorted(ds, key=lambda d: d.name, reverse=True)]


@router.get("/reports/{rid}")
async def get_report(rid: str):
    d = _dir(rid)
    t = d / "transcript.md"
    return {"id": rid, "report": _md(d), "transcript": t.read_text() if t.exists() else "",
            "meta": _meta(d), "summary": _summary(d)}


@router.get("/reports/{rid}/board/{name}")
async def report_board_img(rid: str, name: str):
    """Board snapshot linked from a report (![page 1](board/<file>.png))."""
    d = _dir(rid)
    if not re.fullmatch(r"[0-9A-Za-z_-]{1,60}\.png", name):
        raise HTTPException(400)
    f = d / "board" / name
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f, media_type="image/png")


@router.post("/reports/{rid}/meta")
async def set_meta(rid: str, body: dict):
    d = _dir(rid)
    m = _meta(d)
    if "title" in body:
        m["title"] = str(body["title"])[:120]
    if "notes" in body:
        m["notes"] = str(body["notes"])[:20000]
    if "categories" in body:
        m["categories"] = [str(c)[:40] for c in body["categories"]][:20]
    m["updated"] = time.strftime("%Y-%m-%d %H:%M")
    (d / "meta.json").write_text(json.dumps(m, indent=1))
    return {"ok": True, "updated": m["updated"]}


@router.get("/categories")
async def get_cats():
    try:
        return json.loads(CATS.read_text()) if CATS.exists() else DEFAULT_CATS
    except json.JSONDecodeError:
        return DEFAULT_CATS


@router.post("/categories")
async def set_cats(body: dict):
    cats, seen = [], set()
    for c in body.get("categories", [])[:40]:
        n = str(c.get("name", "")).strip()[:40]
        col = c.get("color", "#b6ff3b")
        if n and n.lower() not in seen and re.match(r"^#[0-9a-fA-F]{6}$", col):
            seen.add(n.lower())
            cats.append({"name": n, "color": col})
    CATS.write_text(json.dumps(cats, indent=1))
    # renamed categories follow; deleted ones are dropped from every report
    names = {c["name"] for c in cats}
    for d in C.SESSIONS.iterdir():
        f = d / "meta.json"
        if f.exists():
            m = _meta(d)
            ren = body.get("rename", {}) or {}
            keep = list(dict.fromkeys(ren.get(x, x) for x in m.get("categories", []) if ren.get(x, x) in names))
            if keep != m.get("categories", []):
                m["categories"] = keep
                f.write_text(json.dumps(m, indent=1))
    return cats
