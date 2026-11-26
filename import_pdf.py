"""PDF question bank -> cartridges/<id>.yaml (verified: false).

    python3 import_pdf.py bank/*.pdf

Text pages go to Claude as text (cheap). Scanned / maths-heavy PDFs: Claude reads the
file itself with its Read tool (vision).
"""
import asyncio
import re
import sys
from pathlib import Path

from pypdf import PdfReader

import ai
import carts
import config as C

SCHEMA = """Each cartridge:
{"id":"kebab-case","title":"...","topics":["..."],"warmup":bool,"answer_source":"pdf|generated",
 "statement":"full question, LaTeX in $...$",
 "stages":[{"goal":"...","expected":"...","solution":"...","common_errors":["..."],
            "hints":{"rephrase":"...","smaller_case":"...","key_idea":"..."},
            "check":"optional sympy expression equal to 0/True iff the stage answer is right"}],
 "extensions":{"3":["..."],"4":["..."],"5":["..."]},
 "spoken_vocab":["terms and symbols as spoken, for speech-to-text"]}
Hints must never contain the answer. warmup=true only for short warm-up questions.
answer_source=pdf if the PDF contains the answer, else generated."""


def pdf_text(p):
    pages = [(pg.extract_text() or "") for pg in PdfReader(str(p)).pages]
    txt = "\n\n".join(pages)
    weird = len(re.findall(r"[�\x00-\x08]", txt))
    return txt if len(txt) > 200 * len(pages) * 0.5 and weird < 20 else None


async def one(p):
    txt = pdf_text(p)
    task = (f"Split this Cambridge-style maths question bank into interview cartridges.\n{SCHEMA}\n"
            "Reply ONLY with a JSON array of cartridges.\n\n")
    if txt:
        raw = await ai.claude(task + "PDF TEXT:\n" + txt[:60000], C.WRITER_MODEL, 600)
    else:
        print(f"  {p.name}: little text layer -> Claude reads the PDF (vision)")
        raw = await ai.claude(task + f"Read the file ./{p.name} first.", C.WRITER_MODEL, 900,
                              tools="Read", cwd=p.parent, max_turns=6)
    items = ai.extract_json(raw)
    n = 0
    for c in items if isinstance(items, list) else [items]:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        c["verified"] = False
        for st in c.get("stages", []):
            if st.get("check"):
                st["check_ok"] = carts.verify(st["check"])
        carts.save(c)
        n += 1
        flag = " !! sympy check FAILED" if any(s.get("check_ok") is False for s in c.get("stages", [])) else ""
        print(f"  + cartridges/{c['id']}.yaml{flag}")
    return n


async def main(paths):
    total = 0
    for p in map(Path, paths):
        print(f"importing {p} ...")
        try:
            total += await one(p.resolve())
        except Exception as e:
            print(f"  failed: {e}")
    print(f"{total} cartridges written (verified: false). Pre-brief them in the dashboard.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    asyncio.run(main(sys.argv[1:]))
