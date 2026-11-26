"""What Cambridge interviewers actually assess, measured from the session (research-backed, see SOLO_PLAN.md)."""
import re

WPS = 2.5                      # spoken words per second (estimate; segments carry start time only)
SMALL = re.compile(r"\b(small(er)? case|special case|let'?s try|try (n|x|k) ?(=|equals)|for example|say n (=|equals)|n (=|equals) (1|2|3|one|two|three)|simple case)\b", re.I)
FILL = re.compile(r"\b(u+m+|u+h+m*|e+r+m*|hmm+|mm+|you know|i mean|sort of|kind of|basically|literally)\b|(?:^|,\s*)like(?=\s*,)|\blike,", re.I)
CHECK = re.compile(r"\b(check|sanity|does that work|test (it|that)|verify|plug in|substitut)", re.I)


def compute(o):
    segs, log = o.transcript, o.log
    total = max(1.0, o.now())
    stud = [s for s in segs if s["speaker"] == "S"]
    words = sum(len(s["text"].split()) for s in stud)
    talk = min(1.0, words / WPS / total)
    first = next((s["t"] for s in stud if len(s["text"].split()) >= 8), None)
    hn = [e["t"] for e in log if e["kind"] == "hint"]
    hc = [e["t"] for e in log if e["kind"] == "cards" and "hint" in (e.get("reason") or "").lower()]
    hints = hn + [t for t in hc if not any(0 <= t - h <= 15 for h in hn)]   # a hint note + its card = one hint
    uptake = []
    for h in sorted(set(hints)):
        nxt = next((s["t"] for s in stud if s["t"] > h and len(s["text"].split()) >= 10), None)
        if nxt:
            uptake.append(nxt - h)
    errs = [e for g in o.s["stage_log"].values() for e in g["errors"]]
    stages = [g for k, g in o.s["stage_log"].items() if not k.startswith("personal")]
    longest_quiet = max([b["t"] - a["t"] for a, b in zip(stud, stud[1:])] or [0])
    m = {
        "talk_pct": round(100 * talk), "first_idea_s": round(first) if first is not None else None,
        "hints": len(set(hints)), "hint_uptake_s": round(sum(uptake) / len(uptake)) if uptake else None,
        "self_corrected": sum(e["self_corrected"] for e in errs), "errors_flagged": sum(not e["self_corrected"] for e in errs),
        "stages_done": sum(g["end"] is not None for g in stages), "stages_seen": len(stages),
        "extensions": sum(g["extensions"] for g in stages),
        "small_cases": sum(bool(SMALL.search(s["text"])) for s in stud), "self_checks": sum(bool(CHECK.search(s["text"])) for s in stud),
        "fillers": sum(len(FILL.findall(s["text"])) for s in stud),
        "filler_top": _top_fillers(stud),
        "talk_min": max(0.1, words / WPS / 60),
        "longest_quiet_s": round(longest_quiet), "pauses": o.s.get("pauses", 0), "paused_s": o.s.get("paused_s", 0),
    }
    return m


def _top_fillers(stud):
    c = {}
    for s in stud:
        for m in FILL.finditer(s["text"]):
            w = re.sub(r"[^a-z ]", "", m.group(0).lower()).strip()
            w = "um" if re.fullmatch(r"u+m+|e+r+m*|hmm+|mm+", w) else "uh" if re.fullmatch(r"u+h+m*", w) else w
            c[w] = c.get(w, 0) + 1
    return ", ".join(f'"{k}" ×{v}' for k, v in sorted(c.items(), key=lambda x: -x[1])[:4])


def grade(v, good, ok, lower_better=False):
    if v is None:
        return "-"
    if lower_better:
        return "Good" if v <= good else "OK" if v <= ok else "Work on it"
    return "Good" if v >= good else "OK" if v >= ok else "Work on it"


def md(m):
    """Full checklist, every criterion every time. No chance this session -> 'n/a' (not a miss, not counted)."""
    stuck = m["hints"] > 0 or m["longest_quiet_s"] >= 60          # specialising only matters when stuck
    errs = m["self_corrected"] + m["errors_flagged"]
    NA = "n/a"
    fpm = round(m.get("fillers", 0) / m.get("talk_min", 1), 1) if m.get("talk_min") else 0
    rows = [
        ("Thinking aloud", f"{m['talk_pct']}% of the time talking", grade(m["talk_pct"], 45, 25), "Interviewers assess *how you think*; silence hides it"),
        ("First idea", f"{m['first_idea_s']} s" if m["first_idea_s"] is not None else "never got going", grade(m["first_idea_s"], 60, 150, True) if m["first_idea_s"] is not None else "Work on it", "Say something concrete in the first minute"),
        ("Longest silence", f"{m['longest_quiet_s']} s", grade(m["longest_quiet_s"], 30, 60, True), "Narrate even when stuck"),
        ("Fillers", f"{m.get('fillers', 0)} ({fpm}/min of talk)" + (f": {m['filler_top']}" if m.get("filler_top") else ""), grade(fpm, 4, 8, True), "Um / like / you know blur a clear explanation"),
        ("Hint uptake", (f"{m['hint_uptake_s']} s avg over {m['hints']} hint(s)" if m["hint_uptake_s"] is not None else f"{m['hints']} hint(s), not picked up") if m["hints"] else "n/a: no hints needed",
         (grade(m["hint_uptake_s"], 25, 60, True) if m["hint_uptake_s"] is not None else "Work on it") if m["hints"] else NA, "Teachability: use hints fast"),
        ("Self-corrections", f"{m['self_corrected']} of {errs} slip(s) caught yourself" if errs else "n/a: no slips", grade(m["self_corrected"] - m["errors_flagged"], 0, -1) if errs else NA, "Critique your own ideas"),
        ("Small cases", f"{m['small_cases']} time(s)" + (" while stuck" if stuck else "") if (stuck or m["small_cases"]) else "n/a: never stuck",
         (grade(m["small_cases"], 1, 1) if stuck else "Good") if (stuck or m["small_cases"]) else NA, "Specialise when stuck"),
        ("Sanity checks", f"{m['self_checks']} time(s)" if (m["self_checks"] or m["errors_flagged"]) else "n/a: nothing needed checking",
         grade(m["self_checks"], 2, 1) if (m["self_checks"] or m["errors_flagged"]) else NA, "Test claims before asserting"),
        ("Depth", f"{m['stages_done']}/{m['stages_seen']} parts" + (f", {m['extensions']} extension(s)" if m["extensions"] else "") if m["stages_seen"] else "n/a",
         grade(m["stages_done"] / m["stages_seen"], 0.99, 0.5) if m["stages_seen"] else NA, "They push until it gets hard"),
        ("No pausing", f"{m['pauses']} pause(s), {m['paused_s']} s" if m["pauses"] else "none", "Work on it" if m["pauses"] else "Good", "No pause button on the day"),
    ]
    if m.get("mute"):                     # mute interview: speech criteria had no chance (these read only the transcript)
        for i, r in enumerate(rows):
            if r[0] in ("Thinking aloud", "Longest silence", "Fillers", "First idea", "Hint uptake", "Small cases", "Sanity checks"):
                rows[i] = (r[0], "n/a: mute interview (written only)", NA, r[3])
    out = ["## Checklist: what Cambridge assesses", "", "| Criterion | You | | Why it matters |", "|---|---|---|---|"]
    out += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rows]
    return "\n".join(out) + "\n\n_n/a = no chance to show it this session (not counted)._\n"
