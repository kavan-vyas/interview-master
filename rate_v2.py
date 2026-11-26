"""Standard ratings for every problem: difficulty + realism on 1.0-10.0 (one decimal), Thursday topic tags.

    python rate_v2.py            # rate everything not yet rated (resumable)
    python rate_v2.py --rerate   # start over

Uses free opencode models, rotating to the next when one fails / runs out; Haiku last.
Cache: bank/_work/ratings_v2.json. Writes rating.d10, rating.real, rating.by and top-level tags into each YAML.
"""
import argparse
import asyncio
import json
import re
import subprocess
import tempfile
import time

import ai
import carts
import config as C

WORK = C.ROOT / "bank" / "_work"
CACHE = WORK / "ratings_v2.json"
LOG = WORK / "rate_v2.log"
MODELS = ["opencode/deepseek-v4.1-flash-free", "opencode/glm-5.3-flash-free", "opencode/minimax-m3.1-flash-free",
          "opencode/mimo-v2.6-flash-free", "opencode/big-pickle", "haiku"]
TOPICS = ["proof", "algebra_and_functions", "inequalities", "curve_sketching", "coordinate_geometry", "geometry",
          "sequences_and_series", "trigonometry", "exponentials_and_logarithms", "differentiation", "integration",
          "differential_equations", "numerical_methods", "vectors", "complex_numbers", "matrices", "polar_coordinates",
          "hyperbolic_functions", "roots_of_polynomials", "number_theory", "combinatorics", "functional_equations",
          "limits_and_approximation", "probability", "mechanics"]
SAND = tempfile.mkdtemp(prefix="rate2-")   # opencode runs here: empty dir, nothing to touch

PROMPT = """You grade problems for a Cambridge maths admissions-interview mock. Be strict and CONSISTENT: use the anchors.

difficulty (1.0-10.0, one decimal) = how hard for a strong Year 13 student working live, with hints:
 1.5 GCSE-plus puzzle (e.g. "is 999999 divisible by 27?")
 3.5 13-14-15 triangle: find the area / height
 5.0 typical STEP I question part
 6.5 typical STEP II question
 8.0 hard STEP III question
 9.5 olympiad-level, few would finish

realism (1.0-10.0, one decimal) = how much it looks like a REAL Cambridge maths interview question:
 short spoken statement, unseen, multi-part building up, can be discussed at a whiteboard, invites "what if",
 no long algebra grind, no recall, no calculator, no multiple choice.
 9+ classic interview question (e.g. "how many zeros at the end of 100!?" then generalise)
 7 good interview material with small changes
 5 fine exam question, awkward to talk through
 3 mostly grind / recall / MCQ / needs a diagram that is missing
 1 garbled or unusable
 Be HARSH on realism: median about 5, only about 1 in 6 problems earns 8+, 9+ is rare. If unsure, score lower.

topics = 1-3 from this list only, most important first: {topics}

For EACH problem reply with {{"id": same id, "d": number, "r": number, "t": [topics]}}.
Reply ONLY with a JSON array in the same order, nothing else.

PROBLEMS:
{items}"""


def log(*a):
    line = time.strftime("%H:%M:%S ") + " ".join(map(str, a))
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def compact(c):
    goals = " | ".join(s.get("goal", "") for s in c.get("stages", []) or [])
    return (f"id: {c['id']}\ntitle: {c.get('title')}\nstatement: {(c.get('statement') or '')[:900]}\n"
            f"parts ({len(c.get('stages') or [])}): {goals[:400]}")


def ask(model, prompt):
    if model == "haiku":
        return asyncio.run(ai.claude(prompt, C.GATE_MODEL, 180))
    p = subprocess.run(["opencode", "run", "-m", model, prompt], cwd=SAND, capture_output=True, text=True, timeout=240)
    return p.stdout


def parse(out, ids):
    m = re.search(r"\[.*\]", out or "", re.S)
    arr = json.loads(m.group(0))
    got = {}
    for o in arr:
        cid = o.get("id")
        if cid not in ids:
            continue
        d, r = round(float(o["d"]), 1), round(float(o["r"]), 1)
        t = [x for x in (o.get("t") or []) if x in TOPICS][:3]
        if not (1 <= d <= 10 and 1 <= r <= 10):
            continue
        got[cid] = {"d10": d, "real": r, "tags": t}
    return got


def main(rerate):
    WORK.mkdir(parents=True, exist_ok=True)
    R = {} if rerate or not CACHE.exists() else json.loads(CACHE.read_text())
    cs = carts.load_all()
    todo = [c for c in cs.values() if c["id"] not in R]
    log(f"{len(todo)} to rate, {len(R)} cached")
    mi, fails = 0, 0
    for i in range(0, len(todo), 6):
        batch = todo[i:i + 6]
        ids = {c["id"] for c in batch}
        prompt = PROMPT.format(topics=", ".join(TOPICS), items="\n\n---\n\n".join(compact(c) for c in batch))
        while True:
            model = MODELS[mi % len(MODELS)]
            try:
                got = parse(ask(model, prompt), ids)
                if len(got) < len(ids) // 2:
                    raise ValueError(f"only {len(got)}/{len(ids)}")
                for v in got.values():
                    v["by"] = model.split("/")[-1]
                R.update(got)
                CACHE.write_text(json.dumps(R))
                write(got, cs)          # live: picker sees ratings as they land
                fails = 0
                break
            except Exception as e:
                fails += 1
                log(f"{model} failed ({str(e)[:80]}), rotating")
                mi += 1
                if fails >= len(MODELS) * 2:
                    log("all models failing; sleeping 5 min then retrying")
                    time.sleep(300)
                    fails = 0
        log(f"{min(i + 6, len(todo))}/{len(todo)} via {model}")
    write(R, cs)
    log("done")


def write(R, cs):
    for cid, v in R.items():
        c = cs.get(cid)
        if not c:
            continue
        r = dict(c.get("rating") or {})
        new = {**r, "d10": v["d10"], "real": v["real"], "by": v.get("by")}
        if r != new or c.get("tags") != v["tags"]:
            c["rating"], c["tags"] = new, v["tags"]
            carts.save(c)


if __name__ == "__main__":
    a = argparse.ArgumentParser()
    a.add_argument("--rerate", action="store_true")
    a.add_argument("--write", action="store_true", help="only write cached ratings into the YAMLs")
    x = a.parse_args()
    if x.write:
        write(json.loads(CACHE.read_text()), carts.load_all())
    else:
        main(x.rerate)
