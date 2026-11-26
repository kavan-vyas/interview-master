"""Per-session scores -> bank/_work/progress.json; weakest topics drive the picker's 'Weak spots' sort."""
import json
import time

import carts
import config as C
import metrics

F = C.ROOT / "bank" / "_work" / "progress.json"


def load():
    if not F.exists():
        return []
    try:
        return json.loads(F.read_text())
    except Exception:
        bak = F.with_suffix(f".corrupt-{int(time.time())}.json")   # never silently overwrite history
        F.rename(bak)
        print("progress.json unreadable, moved to", bak)
        return []


def _write(rows):
    F.parent.mkdir(parents=True, exist_ok=True)
    tmp = F.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, indent=1))
    tmp.replace(F)                     # atomic


def record(o):
    if not o.s.get("main_id"):
        return None
    c = carts.load(o.s["main_id"]) or {}
    logs = [g for k, g in o.s["stage_log"].items() if k.startswith("main:")]
    n = len(c.get("stages", [])) or 1
    done = sum(1 for g in logs if g["end"] is not None)
    hints = sum(g["hints"] for g in logs)
    errs = [e for g in logs for e in g["errors"]]
    flagged = sum(1 for e in errs if not e["self_corrected"])
    score = max(0.0, min(10.0, 10 * done / n - 0.8 * hints - 1.2 * flagged + 0.4 * (len(errs) - flagged)))
    row = {"t": time.time(), "cart": o.s["main_id"], "title": c.get("title"), "topics": c.get("topics") or [],
           "stages": n, "done": done, "hints": hints, "errors": len(errs), "flagged": flagged,
           "score": round(score, 1), "solo": bool(o.s.get("solo")), "minutes": round(o.now() / 60, 1), "drill": o.s.get("drill"), "m": metrics.compute(o)}
    if not logs:
        return None                    # never reached the main problem
    _write(load() + [row])
    return row


def topics():
    """topic -> {avg, n, last}; lower avg = weaker."""
    agg = {}
    for r in load():
        for t in r["topics"] or ["general"]:
            a = agg.setdefault(t, {"sum": 0, "n": 0, "last": 0})
            a["sum"] += r["score"]; a["n"] += 1; a["last"] = max(a["last"], r["t"])
    return {t: {"avg": round(a["sum"] / a["n"], 1), "n": a["n"], "last": a["last"]} for t, a in agg.items()}
