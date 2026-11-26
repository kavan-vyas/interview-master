# Interview Master

Local copilot for in-person Cambridge maths mock interviews (M-series Mac). Listens on one mic, works out
where the student is, shows the interviewer three full-sentence follow-ups (PROBE / HINT / EXTEND).
Everything free + open source except the AI: Claude via your Claude Code subscription (`claude -p`).

## Run

```bash
scripts/setup.sh          # once: whisper-cpp, ffmpeg, python deps, large-v3-turbo model
scripts/run.sh            # whisper-server :8080 + dashboard http://localhost:8000
```

No mic / dry run: `python3 server.py --no-audio --sim sim/demo.txt` (or type speech into the box under the transcript).
Terminal audio test (build step 1): `python3 audio.py`.
Import a PDF bank: `python3 import_pdf.py bank/*.pdf` → `cartridges/<id>.yaml` (`verified: false`).

## Files

| File | Job |
|---|---|
| `audio.py` | sounddevice → Silero VAD → whisper-server (in-memory WAV) → ECAPA speaker match |
| `orchestrator.py` | state + every rule/timer: silence 20/45/90 s, error hold, hint ladder, auto-adapt ±1 |
| `ai.py` | Haiku gate, Sonnet writer, pre-brief chat, report narrative |
| `carts.py` | cartridge YAML + sympy checks (EXTEND cards failing sympy are dropped) |
| `report.py` | `sessions/<date-time>/transcript.md`, `report.md`: only with consent |
| `server.py` | FastAPI + WebSocket |
| `static/` | dashboard (vanilla JS + KaTeX) |

Hotkeys: `1` Probe · `2` Hint · `3` Extend · `M` Move on · `R` Refresh. Click a card to mark it used; click a speaker chip to correct it.

## Token + latency notes

- Live loop = two warm Claude Code processes via the Agent SDK (your subscription login): Haiku gate, Sonnet writer.
  Each is primed once per stage with that stage only, then sent just the new transcript lines + the request.
  Sessions reset on every stage change, on cancel/error, and after 40 turns, so history never grows unbounded.
- Measured: gate 1.1–3.9 s (was 5–11 s with `claude -p`), writer 2.4–8.5 s (was ~12 s).
  A 10-call warm-up + main run used ~12k fresh input tokens + ~13k cache reads.
- Sessions start with no MCP servers, plugins, hooks, tools or CLAUDE.md, from an empty `.claude_sandbox/` cwd.
- One-off jobs (pre-brief, report, PDF import) still use `claude -p` with the same lean flags.
  Without the SDK installed, the live loop falls back to `claude -p` automatically.
- Silence 20 s think-aloud card is local (no AI call). Gate skipped on interviewer speech; concurrent gate calls coalesce.

## Tunables

`config.py`, override with env `IM_<NAME>` (e.g. `IM_HOLD_S=30`, `IM_SPK_THRESH=0.35`, `IM_GATE_MODEL=haiku`).
