"""Whole question bank -> cartridges, with Haiku. Resumable: re-run any time, done work is skipped.

    python3 build_bank.py            # everything in bank/
    python3 build_bank.py --limit 5  # stop after 5 new cartridges

Per source unit (text PDF chunk / scanned-PDF page chunk / photo / Word file):
  1. extract  Haiku pulls every distinct question out (cached in bank/_work/extracted/)
  2. filter   local dedupe vs everything seen + existing cartridges; personal Qs -> personal_bank.yaml
  3. build    Haiku writes cartridges, 4 questions per call; sympy checks on stages
Cheapest units first, so cartridges appear in the dashboard within a minute.
"""
import argparse
import asyncio
import difflib
import hashlib
import json
import re
import subprocess
from pathlib import Path

import yaml
from pypdf import PdfReader, PdfWriter

import ai
import carts
import config as C
from import_pdf import SCHEMA

BANK = C.ROOT / "bank"
WORK = BANK / "_work"
EXTRACTED = WORK / "extracted"
BUILT = WORK / "built.json"
MODEL = "haiku"
CHUNK_CHARS = 12000
SCAN_PAGES = 8
EX_SEM = asyncio.Semaphore(3)   # extract calls
BU_SEM = asyncio.Semaphore(3)   # build calls: own lane so cartridges land while extraction continues

EXTRACT = """Extract every distinct interview question from this source (Oxbridge maths/CS-style admissions interviews).
Keep sub-parts together as one question. Skip headings, tips, commentary, and duplicate restatements.
For each question reply with:
{"q":"full question text, maths in $LaTeX$","kind":"maths|personal|other","answer":"answer or solution given in the source, else empty","short":true if it is a quick warm-up (under ~5 minutes)}
kind=personal: motivation, reading, personal statement, "why maths" style. kind=other: non-maths puzzles/logic you would not use in a maths interview.
Reply ONLY with a JSON array (empty array if none)."""

BUILD = """Turn each question below into a mock-interview cartridge for a strong-at-maths interviewer.
{schema}
Stage the problem as an interviewer would (2-5 stages). If a source answer is given use it and set answer_source=pdf.
Make sure every solution is actually correct. Reply ONLY with a JSON array, one cartridge per question, same order.

QUESTIONS:
{qs}"""


def log(*a):
    print(*a, flush=True)


def key(s):
    return hashlib.sha1(s.encode()).hexdigest()[:12]


def norm(t):
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", t.lower()).split())


# ---------------------------------------------------------------- units
def units():
    """[(cost_rank, unit_id, kind, payload)] kind: text|file"""
    out = []
    for p in sorted(BANK.rglob("*")):
        if WORK in p.parents or not p.is_file():
            continue
        rel = str(p.relative_to(BANK))
        ext = p.suffix.lower()
        if ext == ".pdf":
            try:
                r = PdfReader(str(p))
                pages = [(pg.extract_text() or "") for pg in r.pages]
            except Exception as e:
                log("skip", rel, e)
                continue
            txt = "\n\n".join(pages)
            if len(txt) / max(1, len(pages)) >= 150:
                for i in range(0, len(txt), CHUNK_CHARS):
                    out.append((0, f"{rel}#{i // CHUNK_CHARS}", "text", txt[i:i + CHUNK_CHARS]))
            else:  # scanned: split into small PDFs Claude reads as images
                d = WORK / "scans" / key(rel)
                d.mkdir(parents=True, exist_ok=True)
                for i in range(0, len(r.pages), SCAN_PAGES):
                    part = d / f"part-{i // SCAN_PAGES:02d}.pdf"
                    if not part.exists():
                        w = PdfWriter()
                        for pg in r.pages[i:i + SCAN_PAGES]:
                            w.add_page(pg)
                        w.write(str(part))
                    out.append((2, f"{rel}#p{i + 1}", "file", part))
        elif ext in (".doc", ".docx", ".rtf"):
            t = subprocess.run(["textutil", "-convert", "txt", "-stdout", str(p)], capture_output=True, text=True).stdout
            if t.strip():
                out.append((0, rel, "text", t[:CHUNK_CHARS * 2]))
        elif ext in (".jpg", ".jpeg", ".png"):
            out.append((1, rel, "file", p))
    return sorted(out, key=lambda u: u[0])


async def extract(uid, kind, payload):
    f = EXTRACTED / f"{key(uid)}.json"
    if f.exists():
        return json.loads(f.read_text())
    async with EX_SEM:
        if kind == "text":
            raw = await ai.claude(f"{EXTRACT}\n\nSOURCE ({uid}):\n{payload}", MODEL, 300)
        else:
            raw = await ai.claude(f"{EXTRACT}\n\nRead the file ./{payload.name} first, then reply.", MODEL, 600,
                                  tools="Read", cwd=payload.parent, max_turns=4)
    qs = ai.extract_json(raw)
    qs = [q for q in (qs if isinstance(qs, list) else []) if isinstance(q, dict) and q.get("q")]
    for q in qs:
        q["source"] = uid
    f.write_text(json.dumps(qs, indent=1))
    return qs


# ---------------------------------------------------------------- filter
class Seen:
    def __init__(self):
        self.items = [norm(c.get("statement", "")) for c in carts.load_all().values()]
        built = json.loads(BUILT.read_text()) if BUILT.exists() else {}
        self.items += [norm(q) for q in built.get("_texts", [])]

    def dup(self, text):
        n = norm(text)
        if len(n) < 12:
            return True
        ws = set(n.split())
        for s in self.items:
            sw = set(s.split())
            if ws and sw and len(ws & sw) / len(ws | sw) > 0.6:
                return True
            m = difflib.SequenceMatcher(None, n, s)
            if m.real_quick_ratio() > 0.8 and m.quick_ratio() > 0.8 and m.ratio() > 0.8:
                return True
        self.items.append(n)
        return False


def add_personal(qs):
    p = C.CARTS / "personal_bank.yaml"
    d = yaml.safe_load(p.read_text()) if p.exists() else {"questions": []}
    have = {norm(x) for x in d["questions"]}
    new = [q for q in qs if norm(q) not in have and "illegible" not in q.lower()]
    if new:
        d["questions"] += new
        p.write_text(yaml.safe_dump(d, sort_keys=False, allow_unicode=True, width=100))
    return len(new)


# ---------------------------------------------------------------- build
def record(texts, ids):
    b = json.loads(BUILT.read_text()) if BUILT.exists() else {"_texts": []}
    b["_texts"] += texts
    for t, i in zip(texts, ids):
        b[key(t)] = i
    BUILT.write_text(json.dumps(b, indent=1))


def save(c):
    base = re.sub(r"[^a-z0-9-]", "-", str(c.get("id") or "q").lower()).strip("-")[:60] or "q"
    cid, n = base, 2
    while (C.CARTS / f"{cid}.yaml").exists():
        cid, n = f"{base}-{n}", n + 1
    c["id"], c["verified"] = cid, False
    for st in c.get("stages", []) or []:
        if st.get("check"):
            st["check_ok"] = carts.verify(st["check"])
    carts.save(c)
    return cid


async def build(batch, stats):
    qs = "\n\n".join(f"[{i + 1}] (source: {q['source']}; warm-up: {bool(q.get('short'))})\n{q['q']}"
                     + (f"\nSource answer: {q['answer']}" if q.get("answer") else "") for i, q in enumerate(batch))
    async with BU_SEM:
        try:
            raw = await ai.claude(BUILD.format(schema=SCHEMA, qs=qs), MODEL, 400)
            cs = ai.extract_json(raw)
        except Exception as e:
            log("  build failed:", str(e)[:120])
            return
    cs = cs if isinstance(cs, list) else [cs]
    ids = []
    for q, c in zip(batch, cs):
        if not isinstance(c, dict) or not c.get("stages"):
            ids.append(None)
            continue
        c.setdefault("statement", q["q"])
        c["warmup"] = bool(c.get("warmup", q.get("short")))
        c["source"] = q["source"]
        ids.append(save(c))
        stats["built"] += 1
        bad = any(s.get("check_ok") is False for s in c["stages"])
        log(f"  + {ids[-1]}{'  !! sympy FAILED' if bad else ''}")
    record([q["q"] for q in batch], ids)


async def main(limit):
    WORK.mkdir(exist_ok=True)
    EXTRACTED.mkdir(exist_ok=True)
    us = units()
    log(f"{len(us)} source units")
    seen, stats = Seen(), {"built": 0, "dupes": 0, "personal": 0, "other": 0}

    async def run(u):
        _, uid, kind, payload = u
        try:
            qs = await extract(uid, kind, payload)
        except Exception as e:
            log(f"extract failed {uid}: {str(e)[:120]}")
            return
        maths, personal = [], []
        for q in qs:
            if seen.dup(q["q"]):
                stats["dupes"] += 1
            elif q.get("kind") == "personal":
                personal.append(q["q"])
            elif q.get("kind") == "maths":
                maths.append(q)
            else:
                stats["other"] += 1
        stats["personal"] += add_personal(personal)
        log(f"{uid}: {len(qs)} found, {len(maths)} new maths, {len(personal)} personal")
        if limit:
            maths = maths[:max(0, limit - stats["built"])]
        await asyncio.gather(*(build(maths[i:i + 4], stats) for i in range(0, len(maths), 4)))

    await asyncio.gather(*(run(u) for u in us))
    log(f"done: {stats}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    asyncio.run(main(ap.parse_args().limit))
