# Question bank

Copied from ~/Downloads on 2026-11-18. Originals untouched there.

| Folder | Files | Format | Import route | Status |
|---|---|---|---|---|
| `nextstepmaths/` | 30 × 1-page question | PDF, text | text | building via `build_bank.py` |
| `past-interviews/` | 8 anon PDFs, C1–C6/E1–E2/B1 PDFs, Munroe 2019 | PDF, text | text | pending |
| `past-interviews/` | F1–F2, G1–G4, H1–H2 | JPG photos | vision (needs importer support) | pending |
| `past-interviews/` | St Catherine's 2009/2012, Trinity Hall 2005, Maths & Phil | .doc/.docx | convert to PDF first (`textutil` / Pages) | pending |
| `oxbridge-random/` | 3 pages, many short Qs | PDF, text | text | pending (good warm-up source) |
| `y13-lock-in/` | 12 pages | PDF, text | text | pending |
| `juan/` | 6 pages | PDF, text | text | pending |
| `jzmaths/` | 17 pages | PDF, text | text | pending |
| `jshah-workbook/` | 101 pages | PDF, **scanned** | vision, split into chunks | pending (biggest token cost) |
| `jaymin/` | 79 pages | PDF, **scanned** | vision, split into chunks | pending |

Full run later (cheapest first):

```bash
python3 import_pdf.py bank/nextstepmaths/*/*.pdf bank/past-interviews/*/*.pdf bank/oxbridge-random/*.pdf bank/y13-lock-in/*.pdf bank/juan/*.pdf bank/jzmaths/*.pdf
```

Then scanned PDFs, JPGs and .doc files once the importer handles them.

## Now: `python3 build_bank.py`

Haiku pipeline over everything above (text, photos, .doc, scans in 8-page chunks). Resumable: extracted questions
cache in `_work/extracted/`, built ones in `_work/built.json`; re-run to continue. Log: `_work/build.log`.
Duplicates merged locally; personal questions → `cartridges/personal_bank.yaml`. All cartridges `verified: false`.
