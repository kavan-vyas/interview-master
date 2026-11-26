"""Rate every cartridge (Haiku), rank them, then refine the top Jaymin multi-part ones (Sonnet).

    python rate_bank.py            # rate all unrated + refine top 20 Jaymin multi-part
    python rate_bank.py --refine 30 --rerate
Resumable: ratings cached in bank/_work/ratings.json, originals backed up to bank/_work/orig/.
Writes `rating:` (quality, difficulty, parts, minutes, flags, tagline, priority, tier) into each YAML.
"""
import argparse
import asyncio
import json
import shutil
import time

import ai
import carts
import config as C

WORK = C.ROOT / "bank" / "_work"
RATINGS = WORK / "ratings.json"
ORIG = WORK / "orig"
LOG = WORK / "rate.log"
SEM = asyncio.Semaphore(4)

RATE = """Rate these Cambridge/Oxbridge maths admissions-interview problems for use in a live mock interview.
For EACH one return an object:
{"id": same id, "quality": 1-10 interview value: richness, multi-step structure, scope for discussion and extension,
 clarity of statement. NOT difficulty. Use the FULL range: median about 5, at most about 1 in 10 gets 9+, trivial or
 MCQ/recall items get 1-3, garbled or unusable 1. "difficulty": 1-10 for a strong A-level student, independent of
 quality (an easy problem can be excellent, a hard one poor), "parts": number of distinct
 sub-questions a student works through, "minutes": realistic minutes in interview, "flags": subset of
 ["garbled","missing_info","trivial","needs_diagram","wrong_or_unclear_solution"], "tagline": <=12 words, what makes it good}
Reply ONLY with a JSON array, same order.

PROBLEMS:
{items}"""

REFINE = """You are refining one cartridge for a live Cambridge maths mock-interview copilot. Make it excellent:
- statement: clean, complete, faithful to the original problem; label the parts (i), (ii), (iii)... in order,
  hardest-last, all maths in $LaTeX$. Fix garbled text; never invent a different problem.
- stages: exactly one stage per part (split or merge existing stages so they match). Each stage:
  goal, expected (what a correct student answer contains), solution (full, correct, concise), common_errors (2-4
  real ones), hints {rephrase, smaller_case, key_idea} (escalating, never giving the answer), check (one sympy
  expression that evaluates True or 0 if the stage's key result is right, or omit if not checkable).
- extensions: {"3": [...], "4": [...], "5": [...]} follow-up "what if" questions by student level (1-2 each).
- title: short and specific. topics: 3-6.
Keep "id", "source", "warmup", "answer_source" unchanged. Reply ONLY with the full cartridge as one JSON object.

CARTRIDGE:
{cart}"""


def log(*a):
    line = time.strftime("%H:%M:%S ") + " ".join(map(str, a))
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def is_jaymin(c):
    return "jaymin" in (c.get("source") or "").lower()


def compact(c):
    goals = " | ".join(s.get("goal", "") for s in c.get("stages", []) or [])
    return (f"id: {c['id']}\ntitle: {c.get('title')}\nstatement: {(c.get('statement') or '')[:900]}\n"
            f"stages ({len(c.get('stages') or [])}): {goals[:500]}")


def priority(c, r):
    q = r.get("quality")
    if q is None:
        q = r.get("score", 5)
    p = q * 10 - 8 * len(r.get("flags") or [])
    parts = max(r.get("parts") or 1, len(c.get("stages") or []))
    p += 8 if parts >= 3 else 3 if parts == 2 else 0
    p += 15 if is_jaymin(c) else 0
    p += 3 if c.get("refined") else 0
    p += min(parts, 6)   # tiebreak: longer chains first; difficulty never affects priority
    return p


async def rate_batch(batch, R):
    async with SEM:
        try:
            raw = await ai.claude(RATE.replace("{items}", "\n\n---\n".join(map(compact, batch))), "haiku", 300)
            out = ai.extract_json(raw)
        except Exception as e:
            log("  rate failed:", str(e)[:120])
            return
    for r in out if isinstance(out, list) else []:
        if isinstance(r, dict) and r.get("id") in {c["id"] for c in batch}:
            R[r["id"]] = {k: r.get(k) for k in ("quality", "difficulty", "parts", "minutes", "flags", "tagline")}
    RATINGS.write_text(json.dumps(R, indent=1))
    log(f"  rated {len(R)}")


async def refine(c):
    async with SEM:
        try:
            raw = await ai.claude(REFINE.replace("{cart}", json.dumps(c, ensure_ascii=False)), "sonnet", 500)
            n = ai.extract_json(raw)
        except Exception as e:
            log("  refine failed", c["id"], str(e)[:120])
            return False
    if not isinstance(n, dict) or not n.get("stages") or not n.get("statement"):
        log("  refine bad output", c["id"])
        return False
    shutil.copy(C.CARTS / f"{c['id']}.yaml", ORIG / f"{c['id']}.yaml")
    for k in ("id", "source", "warmup", "answer_source"):
        if k in c:
            n[k] = c[k]
    for st in n["stages"]:
        st.pop("check_ok", None)
        if st.get("check"):
            st["check_ok"] = carts.verify(st["check"])
    n["verified"], n["refined"] = False, True
    n["rating"] = c.get("rating")
    carts.save(n)
    bad = sum(st.get("check_ok") is False for st in n["stages"])
    log(f"  * refined {c['id']} ({len(n['stages'])} parts){'  !! %d sympy failed' % bad if bad else ''}")
    return True


def write_ratings(R):
    cs = carts.load_all()
    ranked = []
    for cid, c in cs.items():
        r = dict(R.get(cid) or {})
        if not r:
            continue
        r["priority"] = priority(c, r)
        ranked.append((r["priority"], cid, r))
    ranked.sort(reverse=True)
    n = len(ranked)
    for i, (_, cid, r) in enumerate(ranked):
        r["rank"] = i + 1
        r["tier"] = "S" if i < n * .05 else "A" if i < n * .20 else "B" if i < n * .50 else "C" if i < n * .80 else "D"
        c = cs[cid]
        r["category"] = (c.get("source") or "other").split("/")[0]
        if c.get("rating") != r:
            c["rating"] = r
            carts.save(c)
    return ranked


async def main(n_refine, rerate):
    ORIG.mkdir(parents=True, exist_ok=True)
    R = {} if rerate else (json.loads(RATINGS.read_text()) if RATINGS.exists() else {})
    todo = [c for c in carts.load_all().values() if c["id"] not in R]
    log(f"rating {len(todo)} cartridges")
    await asyncio.gather(*(rate_batch(todo[i:i + 8], R) for i in range(0, len(todo), 8)))
    ranked = write_ratings(R)
    cs = carts.load_all()
    pick = [cid for _, cid, r in ranked
            if is_jaymin(cs[cid]) and not cs[cid].get("refined") and not cs[cid].get("warmup")
            and max(r.get("parts") or 1, len(cs[cid].get("stages") or [])) >= 2
            and not {"garbled", "missing_info"} & set(r.get("flags") or [])][:n_refine]
    log(f"refining {len(pick)} top Jaymin multi-part")
    await asyncio.gather(*(refine(cs[cid]) for cid in pick))
    ranked = write_ratings(R)
    log("top 15:")
    for p, cid, r in ranked[:15]:
        log(f"  {r['tier']} {p:>4} {cid}  ({r.get('parts')} parts, d{r.get('difficulty')})  {r.get('tagline')}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--refine", type=int, default=20)
    ap.add_argument("--rerate", action="store_true")
    a = ap.parse_args()
    asyncio.run(main(a.refine, a.rerate))
