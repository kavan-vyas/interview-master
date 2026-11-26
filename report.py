"""sessions/<date-time>/transcript.md + report.md. Every session is recorded. Audio never saved."""
import json
import time

import ai
import config as C
import metrics

NAMES = {"I": "Interviewer", "S": "Student", "?": "?"}


def mmss(t):
    t = int(t or 0)
    return f"{t // 60:02d}:{t % 60:02d}"


def transcript_md(o):
    lines = ["# Transcript", ""]
    phase = None
    for ev in sorted(o.log + [{"t": s["t"], "kind": "_seg", "seg": s} for s in o.transcript], key=lambda e: e["t"]):
        if ev["kind"] == "phase" and ev["phase"] != phase:
            phase = ev["phase"]
            lines += ["", f"## {phase.title()}" + (f" ({ev.get('cart')})" if ev.get("cart") else ""), ""]
        elif ev["kind"] == "_seg":
            s = ev["seg"]
            lines.append(f"**[{mmss(s['t'])}] {NAMES.get(s['speaker'], s['speaker'])}:** {s['text']}")
    return "\n".join(lines) + "\n"


def stats_md(o):
    out = ["| Stage | Time | Hints | Errors (self-corrected) | Extensions | Level |", "|---|---|---|---|---|---|"]
    for k, g in o.s["stage_log"].items():
        end = g["end"] if g["end"] is not None else o.now()
        errs = g["errors"]
        sc = sum(e["self_corrected"] for e in errs)
        out.append(f"| {k} {g['goal']} | {mmss(end - g['start'])} | {g['hints']} | {len(errs)} ({sc}) | "
                   f"{g['extensions']} | {g['level_start']}→{g.get('level_end', o.level)} |")
    notes = [f"- {e['note']} ({'self-corrected' if e['self_corrected'] else 'flagged'})"
             for g in o.s["stage_log"].values() for e in g["errors"]]
    return "\n".join(out) + ("\n\n**Errors**\n" + "\n".join(notes) if notes else "")


async def build(o):
    """Always generate the full report (in memory). Consent only decides whether it hits disk."""
    gen = getattr(o, "gen", None)
    tmd, smd = transcript_md(o), stats_md(o)
    if o.s.get("mute"):
        boards = [f"[{mmss(e['t'])}] board: {e.get('text', '')}" for e in o.log if e.get("kind") == "board_seen"]
        smd = ("MUTE INTERVIEW: the student was somewhere they could not talk; they worked on the board and typed. "
               "Judge the written working (board notes below). Never criticise lack of talking, thinking aloud, "
               "silence or fillers.\n\n" + smd)
        tmd = tmd + "\n\nBoard notes over time:\n" + "\n".join(boards[-40:])
    head = (f"# Mock interview report\n\n{time.strftime('%d %B %Y, %H:%M')} · {mmss(o.now())} total · "   # before the slow call:
            f"main: `{o.s['main_id']}` · warm-up: `{o.s['warmup_id'] or '-'}` · final level {o.level}/5\n\n"   # a reset may come meanwhile
            f"{metrics.md(dict(metrics.compute(o), mute=bool(o.s.get('mute'))))}\n## Per stage\n\n{smd}\n\n")
    try:
        narrative = await ai.report_narrative(smd, tmd, o.personal["statement"] if o.s["personal_on"] else "")
    except Exception as e:
        narrative = f"_Narrative failed: {e}. Press Retry._"
    if getattr(o, "gen", None) != gen:
        return head + narrative.strip() + "\n"     # session reset meanwhile: caller drops it, o untouched
    o.report_md = head + narrative.strip() + "\n" + board_md(o)
    return o.report_md


def board_md(o):
    """Final board state per page, linked from the session's board/ folder."""
    d = getattr(o, "report_dir", None)
    shots = sorted((d / "board").glob("*.png")) if d and (d / "board").exists() else []
    last = {}
    for f in shots:
        last[f.stem.rsplit("-p", 1)[-1]] = f.name
    if not last:
        return ""
    return "\n## Board\n\n" + "\n".join(f"![page {int(k) + 1}](board/{v})" for k, v in sorted(last.items())) + \
        f"\n\n_{len(shots)} snapshots in `board/`._\n"


def save(o):
    """Write transcript + report (always). Audio never saved."""
    d = session_dir(o)
    d.mkdir(parents=True, exist_ok=True)
    (d / "transcript.md").write_text(transcript_md(o))
    (d / "transcript.jsonl").write_text("\n".join(json.dumps(s) for s in o.transcript) + "\n")
    if getattr(o, "report_md", None):
        (d / "report.md").write_text(o.report_md)
    o.report_dir = d
    return d


def session_dir(o):
    d = getattr(o, "report_dir", None) or C.SESSIONS / time.strftime("%Y-%m-%d_%H-%M-%S")
    d.mkdir(parents=True, exist_ok=True)
    o.report_dir = d
    return d


def autosave(o):
    """Live recording: transcript + state written as the session runs, so nothing is lost on a restart."""
    d = session_dir(o)
    (d / "transcript.md").write_text(transcript_md(o))
    (d / "transcript.jsonl").write_text("\n".join(json.dumps(s, default=str) for s in o.transcript) + "\n")
    (d / "live.json").write_text(json.dumps({"t0": o.t0, "s": o.s, "log": o.log, "main_id": o.s.get("main_id"),
                                             "stage": o.s.get("stage")}, default=str))
    return d


async def write(o):
    md = await build(o)
    return save(o), md
