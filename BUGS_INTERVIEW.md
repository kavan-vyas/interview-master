# Interview Master: bug report (solo interview first)

Inspection date: 26 Nov 2026, 01:08 to 01:45. Code inspected: a byte-identical snapshot of this folder taken at 01:13
(re-checked identical at 01:37), in `/private/tmp/claude-501/bughunter-interview/work/app/`. No project file was changed
except this report. The live server (pid 25320, port 8000) and its whisper-server processes were not touched; no POST
was sent to it.

Method: every Python and JS file read end to end; flows traced (start, opening line, student speech, end of turn, reply,
part done, next part, pause, time up, end, report, reset, reload). Each bug below was reproduced with a script in
`/private/tmp/claude-501/bughunter-interview/work/` (outputs in `out_*.txt` next to each script). In-process tests use
`work/h.py`: the real orchestrator, professor and server code with the voice output, Claude calls and macOS `say`
stubbed (no sound, no network). Lifecycle and UI tests ran a separate copy of the server on port 8765 (silent voice stub,
`CLAUDE_BIN=/usr/bin/false`) and a browser pointed at that copy. Owner session data was read only; two sessions were
copied to `work/evidence/` because sessions were being deleted during the inspection.

Checklists applied from the Claude-BugHunter toolkit (reference only, nothing run from it): bb-methodology (map every
state machine and timer), hunt-race-condition (parallel identical requests, check-then-act between async tasks),
hunt-business-logic (UI-only gates, skipped steps), hunt-exceptional-conditions (fail-open on empty or failed
dependencies), hunt-dom / hunt-csrf / hunt-cors / hunt-lfi (innerHTML sinks, Origin/Host guard, path parameters),
hunt-llm-ai (model output reaching an eval sink), triage-validation (reproduce before reporting).

## Summary

Counts: Critical 1, High 7, Medium 15, Low 12 (35 bugs, all reproduced by a test; for BUG-19, BUG-30 and BUG-34 one
consequence is inferred or SUSPECTED, as stated in each entry).

The 5 worst:
1. BUG-01: closing the dashboard tab mid-interview stops the server 8 s later; the interview ends with no report, cannot be resumed, and is hidden from Reports.
2. BUG-02 + BUG-03: when Claude fails, the solo professor says the canned "Go on." (even as his opening line) or goes silent, and the error toast that explains it is hidden by CSS in solo mode.
3. BUG-04: the professor's own voice is transcribed as the student's words (seen 6 times in the 26 Nov 00:19 session), polluting the professor's context and the report.
4. BUG-05: one press of the M key on the last part puts a solo interview into a dead "wrapup" state: silent professor, no report, the time limit no longer ends it.
5. BUG-06: for the 7 problems whose statement starts with "(i)", the live screen shows the wrong part at every stage and reveals the next part (including the formula to prove) during part 1.

---

## Critical

### BUG-01: Closing the dashboard tab kills the running interview; no recovery
- Severity: Critical. Area: lifecycle.
- Where: `server.py:185-212` (life_watch, GRACE_S 8 s), `reports.py:66` (archive lists only sessions with report.md or board doc), `server.py:357-370, 388-405` (resume only when `sessions/_test_cart.txt` exists; no resume UI anywhere in `static/`).
- What is wrong: the server counts only WebSocket dashboard tabs from 127.0.0.1. 8 s after the last one closes it shuts itself down, whatever the phase: mid-interview, or while the report is being written. The iPad board (no WebSocket) does not count. On restart the interview cannot be resumed from the UI (the resume code is only reachable with the hidden test-pin file), no report is ever produced, and a session with no report and no board doc is not even listed in Reports, so its transcript is invisible in the app.
- Reproduce: `work/l1_lastTab.py` starts a server copy on 8765 without `--keep-alive`, opens a WebSocket, starts an interview, injects a student line, closes the socket.
- Observed: "server exited: True after 9.3 s"; log "last tab closed: stopping"; the session's `live.json` still says phase `main`, no report.md; `GET /api/reports` on the copy did not list that session (2026-11-26_01-21-06) while listing the ones with reports (`out_l1.txt`).
- Expected: never stop while phase is live/wrapup (or at least finish the report first); offer to resume or at least show the interrupted session.
- Status: CONFIRMED (`work/l1_lastTab.py`, `work/out_l1.txt`).
- Fix needs: gate the shutdown on phase (setup/done only) or a much longer grace while live; a resume entry point that reads `live.json`; list report-less sessions in the archive.

## High

### BUG-02: Every error and warning toast is invisible during a solo or mute interview
- Severity: High. Area: UI.
- Where: `static/flow.css:46` hides `.toasts` in solo live; `static/flow.js:76-78` claims errors and warnings still pop up in live.
- What is wrong: "Professor offline: Claude sign-in failed...", "Professor's AI call failed...", "whisper: speech-to-text down", "Time's up. Writing your report", "Setup failed" are all sent but rendered into a `display:none` container. The student sees only "Thinking..." then "Listening" and silence.
- Reproduce: browser on the server copy, click "Solo interview · start" with Claude unavailable.
- Observed: `#toasts` holds "Professor's AI call failed (ProcessError). Retrying on your next line." with computed `display: none`.
- Expected: errors and warnings visible in solo, as flow.js intends.
- Status: CONFIRMED (browser test against the copy on port 8765; values recorded in this report).
- Fix needs: exempt `.toasts` (or error/warn toasts) from the solo-live hide rule.

### BUG-03: Claude failure or an empty reply at a must-speak moment produces a canned "Go on." instead of the question
- Severity: High. Area: turn-taking.
- Where: `professor.py:475-477` and `professor.py:486-490` (FALLBACK "default"), auth branch `professor.py:480-487`.
- What is wrong: when the opening line, a new part, a hint or a silence prompt must be spoken and the AI call fails twice (or the model leaves SAY empty), the professor says "Go on." (or "Talk me through what you're thinking"). As the first words of an interview this is meaningless: the question is never read. On an auth error he says nothing at all. Combined with BUG-02 the student cannot tell the professor is offline.
- Reproduce: `work/t01_ai_fail_opening.py` (brain raises twice on the opening; then empty SAY; then a failure on "next part").
- Observed: opening spoken = `['Go on.']`; empty SAY on opening -> `['Go on.']`; next part with AI down -> stage 1, spoken "Go on.". Browser run showed the same "Go on." caption, then "Talk me through what you're thinking..." 60 s later.
- Expected: for a new stage, read the part from the cartridge (statement/goal) as the fallback; show an offline notice.
- Status: CONFIRMED (`work/t01_ai_fail_opening.py`, `work/out_t01.txt`).
- Fix needs: reason-specific fallbacks (stage text for new_stage), a visible offline state, and a real retry timer.

### BUG-04: The professor's own voice is transcribed as student speech
- Severity: High. Area: turn-taking / report.
- Where: `professor.py:97-119` (is_echo: drop only if at least 60% of words match), `server.py:238` (echo check skipped entirely when the output is classified as headphones).
- What is wrong: when the mic segment contains the professor's line followed by the student's own words, the word-overlap ratio stays under 0.6 and the whole segment is kept as "S". The professor is then fed his own question as the student's answer, and the transcript and report quote it as the student.
- Reproduce: `work/t13_echo_filter.py` uses the real texts from `sessions/2026-11-26_00-19-35` (10:00 professor line, 10:22 student segment).
- Observed: overlap ratio 0.51, `is_echo False`, segment kept as student speech. In that real session the same thing happened at 04:04, 10:22, 10:50, 13:11, 17:18 and 18:41 (student lines that begin with the professor's previous sentence word for word; copy in `work/evidence/2026-11-26_00-19-35/transcript.md`).
- Expected: the echoed prefix is stripped, the student's own words kept.
- Status: CONFIRMED (`work/t13_echo_filter.py`, `work/out_t13.txt`, owner session evidence).
- Fix needs: remove the matching leading span (sequence alignment against recent `said` lines) instead of a whole-segment ratio; apply it in both headphone and speaker modes.

### BUG-05: M on the last part leaves a solo interview stuck in "wrapup"
- Severity: High. Area: stage progression / keyboard.
- Where: `orchestrator.py:500-508` (move_on past the last stage enters "wrapup" but never calls end), `static/app.js:389-393` (M is live in solo although the action bar is hidden).
- What is wrong: in solo the last part has no "next part"; one M press (or `POST /api/action/moveon`) sets phase "wrapup". tick() ignores non-live phases, so the time limit never fires, `turn()` treats the session as dead, and no report is generated. The only way out is the hidden "End interview" option.
- Reproduce: `work/t18_moveon_last_part.py`.
- Observed: phase `wrapup`, no `report_busy`, report_md None, nothing spoken after the student's question.
- Expected: either ignore M on the last part in solo or treat it as End (report).
- Status: CONFIRMED (`work/t18_moveon_last_part.py`, `work/out_t18.txt`).
- Fix needs: in solo, route "main -> wrapup" through the same path as End, or block it.

### BUG-06: Statements that start with "(i)" show the wrong part on the live screen
- Severity: High (for the 7 affected problems). Area: live UI / problem display.
- Where: `static/app.js:104-109` (partOnly treats the first split chunk as shared setup).
- What is wrong: when the statement begins with "(i)" there is no setup line, so chunk 0 is part (i) and chunk k+1 is shown for stage k. During part 1 the screen shows (i) and (ii) (revealing the next part, e.g. the derangement formula to prove); during part 2 it shows (i) and (iii), never (ii); the last stages repeat the last part.
- Reproduce: Python port of partOnly over all cartridges (`work/out_partonly.txt`).
- Observed: 7 cartridges affected: digit-sums-alternating-sums-divisibility-99, euler-trails-double-dominoes, inclusion-exclusion-and-derangements, ln-inequality-agm-e-limit, ln2-series-rearrangement, ode-blowup-nonuniqueness-euler, sum-of-powers-telescoping. Example (inclusion-exclusion): stage 2 screen = "(i) How many of the integers..." + "(iii) Compute D_4 and D_5."
- Expected: stage k shows part k only (plus any genuine setup text).
- Status: CONFIRMED (static analysis run over the real cartridge data, `work/out_partonly.txt`).
- Fix needs: detect that chunk 0 is itself a part (starts with "(i)"/"(a)") and index parts from 0; fall back to the stage goal when part count differs from stage count.

### BUG-07: Starting a second server instance kills the first one's speech engines and pidfile
- Severity: High. Area: lifecycle / launcher.
- Where: `server.py:300-309` (pidfile written in the startup hook, before the port is bound), `server.py:154-182` (cleanup pkills every process whose command line contains `models/ggml-` or `tts/stt_worker.py`).
- What is wrong: uvicorn runs the startup hook before binding. A second launch (`scripts/run.sh`, `python3 server.py`, or a launch with the wrong port variable, BUG-33) overwrites the pidfile, fails to bind, runs shutdown/atexit cleanup, which pkills the running app's whisper-server/MLX worker and deletes the pidfile. The running interview loses speech-to-text; `ci.sh status` then reports "not running".
- Reproduce: `work/l2_double_launch.py` (instance A plus a dummy engine whose command line contains the copy's `models/ggml-` path; instance B on the same port).
- Observed: B exits with "[Errno 48] address already in use"; afterwards A still runs but the engine is dead and the pidfile is gone.
- Expected: a second instance exits without touching the first one's processes or pidfile.
- Status: CONFIRMED (`work/l2_double_launch.py`, `work/out_l2.txt`, `work/l2_B.log`).
- Fix needs: bind (or check the port / pidfile owner) before any side effect; kill only processes this instance started.

### BUG-08: After "Give me 2 min", the next part's question is silently dropped
- Severity: High. Area: turn-taking / stage progression.
- Where: `professor.py:373-376` (hold_until drop exempts asked/help/stuck/hint/time/repeat, but not "stage"), `orchestrator.py:464-467`.
- What is wrong: the "Give me 2 min" option sets hold_until for 120 s. If the part then advances (M, or the professor marks it done), the new part's question is generated and then dropped as "student asked for time". The stage counter moves on, but the professor never poses the part.
- Reproduce: `work/t02_hold_drops_new_stage.py` path A.
- Observed: stage 1, log "[prof] dropped (student asked for time): 'Good. Now differentiate x^x...'", spoken = opening only. (Path B shows that a 6+ word sentence clears the hold, so the automatic path needs short utterances.)
- Expected: new-part lines are always delivered.
- Status: CONFIRMED (`work/out_t02.txt`).
- Fix needs: add "stage" (and every must-speak reason) to the hold exemption; better, pass must_speak into `_emit_voice` and never drop must-speak lines there.

## Medium

### BUG-09: The professor poses the next part before the student can answer his follow-up question
- Severity: Medium. Area: turn-taking / stage progression.
- Where: `professor.py:460-471` (done_pending), `professor.py:492-499` (queued turn runs right after).
- What is wrong: DONE plus a reply ending in "?" sets done_pending ("let them answer"). But a turn queued while he was thinking runs immediately afterwards, pops done_pending and moves on, so the next part follows within a second.
- Reproduce: `work/t20_done_pending_defeated.py`.
- Observed: "How do you know it's a minimum rather than a maximum?" at 9.7 s, "Part two..." at 10.0 s, no student line in between. Same pattern in the real session at 12:43 then 12:48 (`work/evidence/2026-11-26_00-19-35`).
- Status: CONFIRMED (`work/out_t20.txt`).
- Fix needs: consume done_pending only on a turn triggered by student speech that arrived after the question was spoken.

### BUG-10: Copilot keyboard shortcuts stay active in solo, with no visible controls
- Severity: Medium. Area: UI / keyboard.
- Where: `static/app.js:389-393`; `static/flow.css:46` hides `.actions`.
- What is wrong: 1 (forced probe), 2 (hint), 3 (extension), M (skip part, raises difficulty if under 150 s), R (refresh) all work in solo, with no confirmation and no on-screen hint. A stray key skips a part or makes the professor speak.
- Reproduce: browser on the copy, solo interview, press "m".
- Observed: stage 0 -> 1, the screen switched to part 2; report later showed level 3 -> 4 for that part.
- Status: CONFIRMED (browser test).
- Fix needs: disable copilot keys when `S.solo`, or require a modifier/confirm.

### BUG-11: Next part or hint requested while paused is lost; the hint still counts
- Severity: Medium. Area: pause/resume.
- Where: `orchestrator.py:389-391` (request returns while paused), `orchestrator.py:477-488` (hint increments rung and count first), `static/app.js:389-393` (keys still work under the pause veil).
- Reproduce: `work/t03_pause_moveon_hint.py`.
- Observed: stage advanced to 1, hint rung 1, stage_log hints 1, nothing spoken after resume.
- Expected: actions blocked while paused, or replayed on resume.
- Status: CONFIRMED (`work/out_t03.txt`).
- Fix needs: reject actions while paused, or queue the request until resume.

### BUG-12: A stream error after SAY makes the professor speak twice for one turn
- Severity: Medium. Area: turn-taking.
- Where: `professor.py:432-439` (retry) with `on_say` (`414-423`) not checking `spoke`.
- What is wrong: SAY is spoken as soon as its line streams in. If the stream then times out or errors, the retry asks again and the second answer is spoken too.
- Reproduce: `work/t04_retry_double_speech.py`.
- Observed: "How do you know it is a minimum?" then "Why must the derivative change sign there?" for one student question.
- Status: CONFIRMED (`work/out_t04.txt`).
- Fix needs: skip the retry (or its on_say) when a line was already spoken.

### BUG-13: The "5 minutes left" prompt can be dropped and is never retried
- Severity: Medium. Area: time limit.
- Where: `orchestrator.py:597-599` (time_warned set before delivery); `professor.py:360-372` (the reason contains no "time" keyword, so the "held" and "stale" drops apply to it).
- Reproduce: `work/t05_five_min_warning_dropped.py` (held because the student had not replied to the previous line), `work/t05b_five_min_warning_stale.py` (dropped as stale because the student was mid-sentence).
- Observed: `time_warned: True`, warning never spoken in both cases.
- Status: CONFIRMED (`work/out_t05.txt`, `work/out_t05b.txt`).
- Fix needs: mark it with a "time" reason (or must-speak exemption) and set time_warned only after delivery.

### BUG-14: Turning the speaker off discards the professor's queued line
- Severity: Medium. Area: speaker on/off / mute.
- Where: `server.py:711-718` calls `professor.shh()`; `professor.py:131-135` bumps say_gen; `professor.py:370` drops lines queued before it.
- What is wrong: switching to captions only (V key, speaker button, or the mute toggle) should silence the voice, but it also throws away the next queued line (here the new part's question) instead of showing it as a caption. Non-must replies in flight are dropped too (shh_at).
- Reproduce: `work/t06_speaker_off_drops_line.py`.
- Observed: stage 1, captions = opening line only, log "dropped stale: 'Part two: differentiate x^x.'".
- Status: CONFIRMED (`work/out_t06.txt`).
- Fix needs: stop playback only (voice.stop), without bumping say_gen or shh_at.

### BUG-15: A queued must-speak turn is overwritten by the next one
- Severity: Medium. Area: turn-taking / races.
- Where: `professor.py:403-407` (single `next` slot; a must-speak replaces a must-speak).
- What is wrong: while one turn is in flight, a new-part request is queued; a student question or a hint request then replaces it. The new part is never posed.
- Reproduce: `work/t07_must_speak_replaced.py`.
- Observed: stage 1, brain triggers = the two questions only, no new-stage call.
- Status: CONFIRMED (`work/out_t07.txt`).
- Fix needs: a small queue for must-speak turns (or merge reasons) instead of one slot.

### BUG-16: The report undercounts finished parts and hints
- Severity: Medium. Area: report.
- Where: `metrics.py:32` (a stage counts as done only if `end` is set, which only move_on does), `professor.py:466-472` (the last stage is marked ready, never ended); `metrics.py:17-19` (hints counted only from `hint()` events or trigger reasons containing "hint", not from replies with TYPE: HINT).
- Observed: test gives "Depth 4/5 parts" after the professor marked part 5 DONE, and "Hint uptake: n/a: no hints needed" after a TYPE: HINT reply. The real 26 Nov 00:19 report says "no hints needed" and "4/5 parts" although the transcript shows several hints ("Try some numbers first...", "L'Hôpital's rule wants a fraction...") and the professor marked the final part done; progress.json scored it 3.2/10.
- Reproduce: `work/t10_report_counts.py`.
- Status: CONFIRMED (`work/out_t10.txt`, owner data).
- Fix needs: set `end` when the last stage is marked done; log the reply type with each "cards" note and count HINT types.

### BUG-17: Errors are flagged and scored after time is up, and paraphrases are counted separately
- Severity: Medium. Area: report / time limit.
- Where: `orchestrator.py:639-658` (error-hold expiry keeps running after time_up), `professor.py:455-457` (dedupe only on identical first 40 characters).
- Observed: test with a 4 s limit records 2 flagged errors at 8.2 s, after time up, both scored ("0 of 2 slips caught"). The real 00:19 session logged 4 near-identical flags in the last 11 s after "That's time", each counted against the score.
- Reproduce: `work/t09_timeup.py`.
- Status: CONFIRMED (`work/out_t09.txt`, owner data).
- Fix needs: stop error-hold processing once time_up is set; fuzzy dedupe of error notes.

### BUG-18: The board wipes itself at the end, so the model solution walk reads a blank board
- Severity: Medium. Area: report / board.
- Where: `board-src/src/main.jsx:61` (wipe when the session ends) plus `main.jsx:89` (the wipe counts as an edit and is snapshotted); `professor.py:523-532` (model walk uses `board.latest_png()`).
- Observed: browser test with the real board page: snapshot 0002 (696x496, a drawn shape) before End; after End the page posted 0003 (400x240, blank); `/api/solution_walk` wrote that blank image as `_boards/_live/board.png` (byte-identical to 0003). Board replay also ends on the blank frame.
- Status: CONFIRMED (browser test against the copy; files in `work/app/sessions/_boards/20261126013209/`).
- Fix needs: do not snapshot programmatic wipes (skip snap when SESS is null), or have the walk use the session's saved board.

### BUG-19: 3.8 s event-loop freezes during interviews, and the launcher can kill a healthy server
- Severity: Medium. Area: lifecycle / launcher / performance.
- Where: `carts.py:16-31` (re-parses all 715 YAML files on the event loop whenever any cartridge changes), `static/picker.js:191-194` (polls `/api/cartridges`, 627 KB, every 10 s even during the interview: the `#setup` element is never hidden), `scripts/ci.sh:19, 29-33` (healthy = 2 s probe; unhealthy means stop + restart).
- Observed: cold `load_all` 3.78 s; during a rebuild the ci.sh-style probe `curl -m 2 /api/mic` timed out (exit 28 after 2.02 s). Browser network log during a live interview: 3 `/api/cartridges` requests in 22 s. While the bank builder or rater rewrites cartridges, every poll freezes the interview loop (timers, captions, professor) for about 4 s, and clicking the Dock app at that moment would run `stop()` on a live session.
- Reproduce: `work/l3_loop_stall.py`, `work/out_blocking.txt`.
- Status: CONFIRMED for the freeze and the failed health probe; the resulting kill by `ci.sh open` is inferred from the script (not run, it acts on the real app).
- Fix needs: load cartridges in a thread, poll only on the setup view, and make ci.sh check the pid before killing.

### BUG-20: The report loader is replaced by the empty live screen while the report is written
- Severity: Medium. Area: report / UI.
- Where: `static/app.js:210-214` (phase "wrapup" maps to the live view), `server.py:543-548`, `professor.py:492-495` (an in-flight turn pushes state after report_busy).
- Observed: message order `phase, state:wrapup, report_busy, state:wrapup, state:done, report`; in the browser, `report_busy` -> view "report", next `state(wrapup)` -> view "live". A reload during report writing also lands on the live screen.
- Reproduce: `work/t15_state_after_report_busy.py` plus browser check.
- Status: CONFIRMED (`work/out_t15.txt`).
- Fix needs: map "wrapup" to the report loader view once End was pressed (or keep REP_BUSY authoritative).

### BUG-21: Report Markdown rendering corrupts maths, tables and board images
- Severity: Medium. Area: report.
- Where: `static/app.js:408-421` (`_..._` italics applied to LaTeX; table split on every "|"; no image syntax); `report.py:36` (stage goals put into a pipe table unescaped); `report.py:66-76` (relative `board/...png` URL that no route serves).
- Observed: `$a_1 + a_2$` -> `$a<i>1 + a</i>2$` (maths broken, following text rendered as maths); the per-stage row for "Sketch $|x|+|y|=1$" splits into 10 cells (28 cartridges have "|" in a goal); "![page 1](board/...png)" shown as raw text.
- Reproduce: `work/t23_md_renderer.mjs` (runs md() extracted verbatim from app.js) and the browser report.
- Status: CONFIRMED (`work/out_t23.txt`).
- Fix needs: protect `$...$` spans before inline formatting, escape "|" in table cells, render images via `/api/board/img/...` or an endpoint for session files.

### BUG-22: The iPad QR overlay covers the opening question for the first 60 s
- Severity: Medium. Area: UI overlays.
- Where: `static/flow.js:36`, `static/solo.js:274-280`, `static/solo.css:74-75` (z-index 105 over the caption's 60).
- What is wrong: if the board is not yet connected when the interview starts, the QR panel sits in the middle of the screen exactly where the caption is, for 60 s, which is when the opening question is spoken.
- Observed: at 1440x900 the caption box (643,414,153x79) lies inside the QR box (442,325,978x250); `elementFromPoint` at the caption centre returns the QR image.
- Status: CONFIRMED (browser test).
- Fix needs: place the QR away from the caption area, or hide it while a caption is showing.

### BUG-23: Mute interview in a noisy room: every reply waits up to 12 s
- Severity: Medium. Area: mute mode.
- Where: `professor.py:365-368` (waits while VAD/STT is busy, no mute check); the mic pipeline keeps transcribing noise in mute mode.
- Observed: typed question answered after 12.2 s while the fake VAD reported speech.
- Reproduce: `work/t16_mute_noise_delay.py`.
- Status: CONFIRMED (`work/out_t16.txt`).
- Fix needs: skip the listen-first wait in mute mode (and pause STT there).

## Low

### BUG-24: After a reload mid-interview the last question is not visible anywhere
- Severity: Low. Area: UI / reload.
- Where: `static/solo.js:211-226` (caption only from live "caption" messages), `static/flow.css:36` (transcript hidden in solo).
- Observed: after reload, caption hidden and empty; the last interviewer line exists only in the hidden transcript.
- Status: CONFIRMED (browser test).
- Fix needs: on hello, show the last interviewer line as the caption.

### BUG-25: Pause veil unreadable in Plain mode with a dark appearance
- Severity: Low. Area: theme readability.
- Where: `static/solo.css:39` (calm veil is always 92% white) while text uses the dark-theme ink.
- Observed: veil `rgba(255,255,255,0.92)`, "Paused" text `rgb(230,229,227)`, hint text `rgb(155,154,151)`: white on white; the caption also shows through.
- Status: CONFIRMED (browser test, Plain mode, dark colour scheme).
- Fix needs: use the theme background variable for the veil.

### BUG-26: End twice (or End during the 15 s goodbye) generates the report twice
- Severity: Low. Area: report / races.
- Where: `server.py:537-548` (phase "wrapup" passes the guard), `orchestrator.py:111-118` (auto-end).
- Observed: two concurrent `/api/end`: 2 narrative AI calls, 2 report_busy, 2 report messages.
- Reproduce: `work/t08_double_end.py`.
- Status: CONFIRMED (`work/out_t08.txt`).
- Fix needs: a single in-flight report task; return it to later callers.

### BUG-27: A Claude CLI run with no output gives an empty report with no Retry button
- Severity: Low. Area: report / failure path.
- Where: `ai.py:51-57` (`json.loads(out or "{}")` returns "" instead of raising); `static/app.js:429` (Retry only when the text contains "Narrative failed").
- Observed: `ai.claude` returned `''` with `CLAUDE_BIN=/usr/bin/false`; browser report had only the checklist and tables, Retry hidden.
- Reproduce: `work/t22_claude_empty_output.py`.
- Status: CONFIRMED (`work/out_t22.txt`).
- Fix needs: treat empty stdout or a non-zero exit code as an error.

### BUG-28: Typing to the interviewer while paused is silently discarded
- Severity: Low. Area: pause / mute mode.
- Where: `orchestrator.py:220-221`; the type bar (`static/solo.css:125`, z-index 110) stays usable above the veil.
- Observed: `/api/inject` returns ok, input cleared, line not in the transcript.
- Reproduce: `work/t21_typed_while_paused.py`.
- Status: CONFIRMED (`work/out_t21.txt`).
- Fix needs: hide the type bar while paused or keep the line.

### BUG-29: "Repeat last line" does nothing visible with the speaker off
- Severity: Low. Area: mute mode / speaker off.
- Where: `professor.py:346-348` and `157-161` (say() with speaker off returns without a caption).
- Observed: `/api/repeat` ok, zero messages sent to the page.
- Reproduce: `work/t17_repeat_captions_off.py`.
- Status: CONFIRMED (`work/out_t17.txt`).
- Fix needs: send a caption on repeat.

### BUG-30: A placeholder SAY ("(silent)", "SILENT", "-") is spoken aloud
- Severity: Low. Area: turn-taking.
- Where: `professor.py:292-301, 414-416` (no filter for placeholder text).
- Observed: all four placeholders spoken, captioned and stored as interviewer lines.
- Reproduce: `work/t14_say_silent_literal.py`.
- Status: CONFIRMED for the code path; SUSPECTED for frequency (depends on how often the model writes a placeholder instead of an empty SAY; not seen in the surviving session data).
- Fix needs: treat placeholder-only SAY text as empty.

### BUG-31: Board replay shifts everything said before a pause by the pause length
- Severity: Low. Area: report / replay.
- Where: `orchestrator.py:526-527` (t0 moved forward on resume), `static/solo.js:113` (at = t0 + seg.t), `server.py:605` (same t0 saved in replay.json).
- Observed: pre-pause line placed +3.0 s late after a 3 s pause; post-pause line correct.
- Reproduce: `work/t12_replay_pause.py`.
- Status: CONFIRMED (`work/out_t12.txt`).
- Fix needs: store wall-clock time per segment or the pause intervals.

### BUG-32: AI-written sympy "check" strings can run arbitrary Python
- Severity: Low (local app, needs model output steered by a prompt injection). Area: security.
- Where: `carts.py:10, 54-75` (substring blocklist around sympify, which uses eval with builtins).
- Observed: `Eq(globals()['op'+'en'](path,'w').write(...), 30)` contains no blocked substring and created a file.
- Reproduce: `work/t19_sympy_sandbox.py` (writes a marker file in the work dir only).
- Status: CONFIRMED (`work/out_t19.txt`).
- Fix needs: parse with `parse_expr` and an empty builtins dict / whitelist, never sympify raw model text.

### BUG-33: The PORT variable is ignored; the port is IM_PORT, and ci.sh hard-codes 8000
- Severity: Low. Area: launcher.
- Where: `config.py:31` (reads `IM_PORT`), `scripts/ci.sh:11`.
- Observed: `PORT=8765` gives `config.PORT = 8000`. A second instance started that way targets the live port and triggers BUG-07.
- Status: CONFIRMED (`work/out_port_env.txt`).
- Fix needs: document IM_PORT (or accept PORT) and let ci.sh read the same variable.

### BUG-34: Monitors, USB or Bluetooth speakers are treated as headphones
- Severity: Low. Area: speaker / echo / barge-in.
- Where: `professor.py:61-83` (only names containing "speaker"/"display" or open-ear brands count as speakers).
- Observed: classification check: "DELL U2720Q", "LG HDR 4K", "JBL Flip 5", "Creative Pebble V3" all come out as headphones (`work/out_output_detect.txt`). With those, the echo guard is off and barge-in is on, so the mic hearing the professor would make him cut himself off.
- Status: CONFIRMED for the classification; SUSPECTED for the self-interruption (needs real audio).
- Fix needs: invert the default (unknown device = speakers) or let the user choose.

### BUG-35: Time remaining is never visible in solo mode
- Severity: Low (may be intended). Area: UI / time limit.
- Where: `static/flow.css:34, 46` hide `.top`; `static/solo.js:236-240` still updates the "mm:ss left" clock and its low-time colour.
- Observed: `.top` computed `display: none` while the clock text read "39:59 left". With BUG-02 and BUG-13 the student gets no time signal except the professor's goodbye.
- Status: CONFIRMED (browser test).
- Fix needs: decide; if intended, remove the dead clock code, otherwise show a small timer.

---

## Notes

- The owner's live server (pid 25320) was started around 00:15. `server.py`, `orchestrator.py` and `static/solo.js` changed at 01:00, so the running app is older than the code on disk until it restarts. The 00:19 session evidence above ran on that older orchestrator (professor.py, metrics.py and report.py were already current).
- Several session folders under `sessions/` were deleted by someone else between 01:08 and 01:13; the two used as evidence are copied in `work/evidence/`.
- Test runner: `work/run_all.sh` re-runs all in-process tests. The l1/l2/l3 scripts start a server copy on 8765 and overwrite the pidfile while running: back it up and restore it around them, as was done here.

## Not tested, and why

- Real microphone, VAD timing, whisper or MLX speech-to-text, barge-in on a real voice: forbidden to use the mic; segments were injected and VAD state faked.
- Real voice output (Kokoro, Chatterbox, macOS `say`): no sound allowed; voice was stubbed. Caption pacing against real audio not checked.
- Real Claude behaviour (latency, whether the persona keeps to the line format, how often it writes placeholders or DONE with a question): replies were scripted; no AI calls were made.
- Real iPad, Sidecar, Safari, Apple Pencil, Tailscale/LAN access with the key and cookie: no device available. The board was tested only in a desktop browser against the copy.
- `scripts/ci.sh` and the Dock app: not run, because `stop()` and `open` act on the real app on port 8000. Their behaviour is inferred from the script plus the measured health-probe failure (BUG-19).
- Mac sleep or lid close mid-interview, and a full 40-minute real-time session: not feasible in this window; time limits were shortened or the clock moved.
- 1-to-1 copilot mode (gate, card writer, voice enrolment, personal statement, pre-brief chat and commit, PDF import, bank build and rating scripts): read but not exercised; lower priority per the brief.
- Agent SDK brain session behaviour (lock waits after a reset while a turn is in flight, session recycle every 10 turns, orphaned claude processes after shutdown): needs real Claude processes; not tested.
