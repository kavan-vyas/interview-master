# Solo mode: Claude as the interviewer

Goal: run a full mock interview by yourself. You speak and write on the iPad. Claude watches and listens like a Cambridge maths professor, speaks rarely, and gives hints only when needed.

## Decisions (agreed)
- Board: built-in canvas page. The iPad connects as a **Sidecar display over USB**, so no Wi-Fi is involved and it behaves the same on eduroam and at home.
- Voice out: macOS `say` (offline and instant).
- Behaviour: a real Cambridge interviewer. Mostly silent. Asks "why?", pushes once you're right, gives tiny nudges, never gives the answer.
- After the session: feedback report, board timeline replay, model-solution walk, progress tracker.

## Network reality
Everything runs on the Mac except Claude calls, which are outbound HTTPS and fine on eduroam. Sidecar over a cable doesn't need the iPad and Mac to reach each other over Wi-Fi.
- Requires: same Apple ID on both devices, Bluetooth and Handoff on, and the Mac set up in System Settings → Displays → Add display → iPad.
- Fallback if Sidecar ever fails: Tailscale. The board page works at any URL.

## Architecture
1. **Board** (`/board`, opened in its own window and dragged onto the iPad display)
   - Canvas with Pointer Events. Pen only, which handles palm rejection. Uses Pencil pressure. Pen colours, eraser, undo, new page.
   - Snapshot after 1.5 s of pen idle, plus every 20 s if the board changed. Downscaled to about 1024 px and skipped when unchanged (hash). Each snapshot is stored with a timestamp.
2. **Ears**: the existing whisper pipeline in solo mode, with a single speaker (you). No interviewer enrolment.
   - Echo guard: drop mic input while `say` is speaking.
   - Barge-in: if you start talking, kill `say`.
3. **Professor brain**: a server loop.
   - Triggers:
     - silence beyond the gauge threshold
     - a suspected error (the existing error hold of 30–60 s before mentioning it)
     - a direct question to the interviewer ("is that right?")
     - a stage finished, which leads to an extension
     - stuck, which climbs the hint ladder (rephrase → smaller case → key idea)
   - Each call gets the latest board image, the tail of the transcript, the problem stages and the hidden solution, and a professor persona prompt.
   - Output is at most 2 spoken sentences, or SILENT.
   - Model: Sonnet for replies. A cheap Haiku pass first decides whether to speak at all.
4. **Mouth**: `/api/say` runs `say -v Daniel -r 185` and can be interrupted.

## After the session
- Report: the existing one, plus board snapshots for evidence.
- Replay: a scrubber that syncs board snapshots with transcript lines.
- Model-solution walk: Sonnet compares the full solution with what you wrote.
- Progress: `bank/_work/progress.json` stores a score per topic. The picker gains a "Target weak spots" sort.

## Phases
1. Board page, snapshot store, Sidecar test. Done when you can write on the iPad and snapshots land on the Mac.
2. Voice loop: `say`, echo guard, barge-in, solo enrolment.
3. Professor brain: triggers, persona, hint ladder wired to speech.
4. Replay, model walk, progress tracker.

## Risks
- Reply lag of about 3–6 s for Claude with vision. This is acceptable, because real interviewers pause too.
- Claude might misread messy handwriting. Fix by asking you aloud ("what's that term?") rather than guessing.
- Cost per session is several Sonnet vision calls. The Haiku gate keeps this down.
