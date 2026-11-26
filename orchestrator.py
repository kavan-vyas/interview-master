"""Owns state + every rule/timer. The AI never tracks time."""
import asyncio
import itertools
import json
import re
import time

import ai
import report
import board
import professor
import carts
import config as C

RUNGS = ["rephrase", "smaller_case", "key_idea"]
HELP = re.compile(r"\b(i['’]?m (really |completely |totally |a bit |kind of )?(stuck|lost)"
                  r"|(no|not sure|no clue|i have no idea|i['’]?ve got no idea) (idea )?(how|where) to (even )?(start|begin|approach|go|proceed|do)"
                  r"|i (don['’]?t|do not) know (how|where) to (even )?(start|begin|approach|proceed|go from here)"
                  r"|(can|could|may) (i|you) (please )?(have|get|give( me)?) (a|another|some|any) (hint|clue|nudge)"
                  r"|any hints?|(a|another) hint,? please|hint please|help me|where (do|should) i (even )?start)\b")
LIVE = ("personal", "warmup", "main")
THINK_ALOUD = {"type": "PROBE", "text": "Talk me through what you're thinking right now, even if it's half-formed.",
               "latex": "", "spoken": "", "local": True}


class Orchestrator:
    def __init__(self):
        self.clients = set()
        self.seq = itertools.count(1)
        self.personal = {"statement": "", "bank": carts.personal_bank(), "name": ""}
        self.audio = None
        self.reset()

    # ------------------------------------------------------------ state
    def reset(self):
        for t in (getattr(self, "gate_task", None), getattr(self, "writer_task", None)):
            if t and not t.done():
                t.cancel()
        if getattr(self, "_drill_h", None):
            self._drill_h.cancel()
        if getattr(self, "_eot_h", None):
            self._eot_h.cancel()
        self._eot_h = None
        self._drill_h = None
        professor.reset()
        self.gen = getattr(self, "gen", 0) + 1         # session token: slow work started earlier checks it
        board.STATE.update(last=None, t=0.0, n=0, sid=None)     # next session never sees the previous board
        self.walk_md = self.progress_row = None
        self.t0 = None
        self.report_md = self.report_dir = None
        self.transcript = []
        self.log = []                       # event log -> report
        self.cards, self.card_reason = [], ""
        self.cart = None
        self.last_speech = time.time()
        self.fired = set()
        self.gate_task = self.writer_task = None
        self.gate_again = False
        self.writer_next = None
        self.hold_until = self.grace_until = 0             # per-session timers: never leak into the next one
        self._mute_seen = self._mute_chk = self._mute_hint = self._mute_save = 0
        self._resume_stage, self._resuming, self._resume_rs = 0, False, None
        self.s = {
            "phase": "setup", "warmup_id": None, "main_id": None, "stage": 0,
            "established": [], "pending_errors": [], "flag": None, "hint_rung": {},
            "silence": 0, "slider": 3, "adapt": 0, "adapt_note": "", "stage_started": None,
            "stage_ready": False, "personal_on": False, "consent": True, "hold_s": C.HOLD_S,
            "stage_log": {}, "gate_busy": False, "writer_busy": False, "enrolled": [],
            "audio": "off", "elapsed": 0, "unverified": False, "last_note": "", "solo": False,
            "limit_s": 0, "paused": False, "pauses": 0, "paused_s": 0, "drill": None, "time_warned": False, "mute": False,
        }

    @property
    def level(self):
        return max(1, min(5, self.s["slider"] + self.s["adapt"]))

    def key(self):
        return f"{self.s['phase']}:{self.s['stage']}"

    def stage(self):
        if not self.cart:
            return {}
        st = self.cart.get("stages", [])
        return st[self.s["stage"]] if self.s["stage"] < len(st) else {}

    def now(self):
        return round(time.time() - self.t0, 1) if self.t0 else 0.0

    def snapshot(self):
        s = dict(self.s, level=self.level, ai=ai.stats, board_age=round(time.time() - board.SEEN["t"]),
                 speaker_on=not professor.state.get("speaker_off"))
        c = self.cart or {}
        s["cart"] = {"id": c.get("id"), "title": c.get("title"), "statement": c.get("statement"),
                     "goals": [x.get("goal") for x in c.get("stages", [])], "verified": c.get("verified")}
        s["rung"] = self.s["hint_rung"].get(self.key(), 0)
        s["pending_errors"] = [dict(e, left=max(0, round(e["deadline"] - time.time()))) for e in self.s["pending_errors"]]
        return s

    # ------------------------------------------------------------ io
    async def send(self, msg):
        data = json.dumps(msg, default=str)
        for ws in list(self.clients):
            try:
                await ws.send_text(data)
            except Exception:
                self.clients.discard(ws)

    async def push_state(self):
        await self.send({"type": "state", "state": self.snapshot()})

    async def _auto_end(self, gen):
        """Time's up -> let the goodbye play and the student say thanks, then write the report by itself."""
        await asyncio.sleep(3)
        while professor.speaking():
            await asyncio.sleep(0.5)
        await asyncio.sleep(12)
        if self.gen == gen and self.s["phase"] not in ("setup", "done") and getattr(self, "auto_end", None):
            await self.auto_end()

    async def toast(self, text, kind="info"):
        await self.send({"type": "toast", "text": text, "kind": kind})

    def note(self, kind, **kw):
        self.log.append({"t": self.now(), "phase": self.s["phase"], "stage": self.s["stage"], "kind": kind, **kw})

    # ------------------------------------------------------------ setup
    async def configure(self, body):
        if self.s["phase"] != "setup":                     # live: a reload / second dashboard / picker refresh
            if "barge" in body:                               # can't touch the running session (only barge)
                self.s["barge"] = body["barge"]
            await self.push_state()
            return
        for k in ("warmup_id", "main_id", "personal_on", "solo", "drill", "barge", "mute"):
            if k in body:
                self.s[k] = body[k]
        if "slider" in body:
            self.s["slider"] = int(body["slider"])
        if self.audio:
            self.audio.solo = bool(self.s["solo"])          # skip speaker-ID model in solo
        if self.s["solo"] and self.s["phase"] == "setup":
            asyncio.ensure_future(professor.warm_brains())   # connect before Begin -> opening line is instant
        if "limit_s" in body:
            self.s["limit_s"] = max(0, int(body["limit_s"] or 0))
        if "hold_s" in body:
            self.s["hold_s"] = max(30, min(60, int(body["hold_s"])))
        await self.push_state()

    async def start(self):
        if not self.s["main_id"]:
            raise ValueError("pick a main cartridge")
        self.t0 = time.time()
        if self.audio:
            self.audio.solo = bool(self.s["solo"])
        first = "personal" if self.s["personal_on"] else ("warmup" if self.s["warmup_id"] else "main")
        rs = self.s.pop("resume", None)          # continue an interrupted session at a given part
        if rs and rs.get("main_id") and rs["main_id"] != self.s["main_id"]:
            rs = None                            # set up for a different problem: never resume across problems
        self._resume_rs = rs                     # applied when main is entered (after warm-up / personal too)
        await self.enter(first)

    async def enter(self, phase):
        if phase not in LIVE and self.s["paused"]:
            await self.pause(False, force=True)
        self.s.update(phase=phase, stage=0, stage_ready=False, pending_errors=[], flag=None)
        cid = {"warmup": self.s["warmup_id"], "main": self.s["main_id"]}.get(phase)
        self.cart = carts.load(cid) if cid else None
        self.s["unverified"] = bool(self.cart and not self.cart.get("verified"))
        if self.audio and self.cart:
            import re as _re
            base = ["circumcentre", "incentre", "inradius", "circumradius", "semi-perimeter", "perpendicular bisector",
                    "sine", "cosine", "tangent", "hypotenuse", "Pythagoras", "integer", "prime", "modulo", "coefficient",
                    "derivative", "integral", "polynomial", "induction", "contradiction", "x squared", "root two"]
            words = _re.findall(r"[A-Za-z][a-z]{5,}", _re.sub(r"\$[^$]*\$", " ", self.cart.get("statement") or ""))
            v = list(dict.fromkeys(list(self.cart.get("spoken_vocab", [])) + words[:25] + base))
            self.audio.vocab = ", ".join(v)[:600]
        self.note("phase", phase=phase, cart=cid)
        await self.send({"type": "phase", "phase": phase, "title": (self.cart or {}).get("title", "")})
        if phase == "main" and self._resume_rs:
            rs, self._resume_rs = self._resume_rs, None
            self.s["established"] = list(rs.get("established", []))
            self._resume_stage, self._resuming = int(rs.get("stage", 0)), True
        if phase == "main" and (getattr(self, "_resume_stage", 0) or getattr(self, "_resuming", False)):
            self.s["stage"], self._resume_stage, self._resuming = self._resume_stage, 0, False
            self.note("resume", stage=self.s["stage"])
        if phase in LIVE:
            await self.begin_stage()
            await self.request("new_stage" if phase != "personal" else "open personal section")
        elif phase == "wrapup":
            self.cards = []
        await self.push_state()
        try:
            report.autosave(self)              # live.json follows the phase (a finished run is never "main")
        except Exception as e:
            print("autosave failed:", e, flush=True)

    async def begin_stage(self):
        if self.s["solo"] and self.s["drill"] == "hint":
            async def _drill_hint():
                if self.s["phase"] in LIVE and not self.s["paused"]:
                    await self.hint("hint drill: give the first hint now")
            if self._drill_h:
                self._drill_h.cancel()
            self._drill_h = asyncio.get_event_loop().call_later(45, lambda: asyncio.ensure_future(_drill_hint()))
        self.s["stage_started"] = time.time()
        self.s["stage_ready"] = False
        self.last_speech = time.time()
        self.fired = set()
        self.s["stage_log"].setdefault(self.key(), {
            "goal": self.stage().get("goal", self.s["phase"]), "start": self.now(), "end": None,
            "hints": 0, "errors": [], "extensions": 0, "level_start": self.level})
        if self.s["solo"]:
            asyncio.ensure_future(professor.warm_brains())     # warm Haiku + Sonnet professor sessions
        else:
            await ai.new_stage(self.cart, self.s["stage"], self.s["phase"], self.personal)  # fresh warm sessions

    # ------------------------------------------------------------ transcript
    async def add_segment(self, seg):
        if self.s.get("mute") and seg.get("speaker") == "S" and not seg.get("typed") and self.s["phase"] != "setup":
            return                       # mute interview: room noise / other people never reach the professor
        if self.s["phase"] not in LIVE + ("wrapup",) or self.s["paused"]:
            return  # pre-session chatter / paused speech is not recorded
        seg = {"id": next(self.seq), "t": self.now(), **seg}
        self.transcript.append(seg)
        if not (self.s["solo"] and ("dur" in seg or seg.get("prof"))):   # mic speech: VAD timed it; typed: now
            self.last_speech = time.time()
        if not seg.get("prof"):
            self.fired = set()               # only the student resets silence steps (else 120/180 s never fire)
        await self.send({"type": "seg", "seg": seg})
        try:
            report.autosave(self)          # every line hits disk immediately
        except Exception as e:
            print("autosave failed:", e, flush=True)
        if seg["speaker"] == "S" and self.s["phase"] in LIVE:
            if self.s["solo"] and (not seg.get("typed") or not self.s.get("mute")):
                professor.state["student_at"] = time.time()
            low = seg["text"].lower()
            if self.s.get("mute") and seg.get("typed"):
                q = seg["text"].rstrip().endswith("?") or re.search(r"\b(hint|help|stuck|lost|explain|repeat|what|why|how|is it|correct|right)\b", low)
                await self.request(f"student TYPED to you (mute interview, they work silently): \"{seg['text']}\". "
                                   + ("Reply in brief text: answer / hint as asked, never ask them to talk aloud." if q else
                                      "It is just a note or their working: stay SILENT unless it states a finished result (then react) or a clear error."),
                                   "ANSWER" if q else None, RUNGS[min(self.s["hint_rung"].get(self.key(), 0), 2)])
                return
            if self.s["solo"] and any(k in low for k in ("give me a second", "give me a minute", "give me a moment", "let me think", "one second", "hang on", "bear with me", "just a sec")):
                self.grace_until = time.time() + 90
                self.hold_until = time.time() + 45
                self.fired = set()
            elif self.s["solo"] and re.search(r"\b(wait|hold on|let me (draw|check|see|redo|write|try|just)|one sec|i'?m (thinking|working|checking))\b", low):
                self.hold_until = time.time() + 25      # mid-thought: don't jump in
            elif self.s["solo"] and (len(low.split()) >= 6 or re.search(r"\b(answer|so|therefore|hence)\b.*\b(is|=|equals?|get|got)\b", low)):
                self.hold_until = 0                     # they're back with real content: judge normally again
            if self.s["solo"] and HELP.search(low):
                self.hold_until = 0
                await self.hint("student asked for help / said they're stuck")
            elif self.s["solo"] and any(k in low for k in ("repeat that", "say that again", "repeat the question", "pardon", "say again")):
                await professor.repeat_last(self)
            elif self.s["solo"] and seg["text"].rstrip().endswith("?"):
                rung = RUNGS[min(self.s["hint_rung"].get(self.key(), 0), 2)]
                await self.request(f"student asked you directly: \"{seg['text']}\"", "ANSWER", rung)
            elif self.s["solo"]:
                self._eot_check()          # smart pause: judge only once they've actually stopped
            else:
                self.kick_gate()

    async def fix_speaker(self, sid, spk):
        for seg in self.transcript:
            if seg["id"] == sid:
                was, seg["speaker"] = seg["speaker"], spk
                await self.send({"type": "seg_update", "seg": seg})
                if spk == "S" and was != "S" and self.s["phase"] in LIVE:
                    self.kick_gate()
                return

    def recent(self, secs):
        cut = self.now() - secs
        return [s for s in self.transcript if s["t"] >= cut][-30:]

    def activity(self):
        now = time.time()
        if self.s.get("mute"):
            return
        if self.s["solo"] and professor.speaking() and not self.barge_ok():
            return                                # that's the professor's voice in the mic
        if now - self.last_speech > 0.3:          # new run of voiced audio
            self._run_start = now
        self.last_speech = now
        # barge-in: only with headphones (on speakers the mic hears the professor itself), and only after
        # 0.4 s of sustained voice, so a cough or click doesn't cut it off
        if (self.s["solo"] and self.barge_ok() and professor.speaking()
                and now - getattr(self, "_run_start", now) >= 0.4):
            asyncio.ensure_future(professor.shh())

    def barge_ok(self):
        return self.s.get("barge", True) and not professor.on_speakers()

    EOT_S = 1.5

    def _eot_check(self):
        """Run the professor once the student has been quiet for EOT_S after their latest words."""
        if getattr(self, "_eot_h", None):
            self._eot_h.cancel()
        mark = self.transcript[-1]["id"] if self.transcript else 0

        def fire():
            quiet = time.time() - self.last_speech
            last = (self.transcript[-1]["text"] if self.transcript else "").rstrip().lower()
            need = self.EOT_S + (2.0 if self.s["solo"] else 0) + (3.0 if self.s["solo"] and (last.endswith(("...", ",", "-"))
                   or last.split()[-1:] and last.split()[-1] in ("so", "and", "but", "then", "is", "to", "equals", "of", "the", "because", "like", "um", "uh")) else 0)
            done_say = bool(re.search(r"(\b(so|therefore|hence|which gives|that gives|so yeah)\b.*\b(is|=|equals|equal to|get|got)\s+[\w$\\/.-]+[.!]?$)"
                                      r"|\b(that'?s (my|the) answer|i'?m done|that'?s it|done)[.!]?$", last))
            if done_say and self.s["solo"]:
                need = self.EOT_S + 0.5          # they just stated a result: answer promptly
            if quiet < need - 0.05:                  # still talking / mid-thought: look again shortly
                self._eot_h = asyncio.get_event_loop().call_later(need - quiet, fire)
                return
            if time.time() < getattr(self, "hold_until", 0):   # they asked for time / are mid-working: stay out,
                self._eot_h = asyncio.get_event_loop().call_later(self.hold_until - time.time() + 0.1, fire)   # judge when it ends
                return
            if self.transcript and self.transcript[-1]["id"] == mark and self.s["phase"] in LIVE:
                asyncio.ensure_future(self.request(("end of turn: the student just STATED A RESULT. If right, react now (brief 'good' + why-question or next step); if wrong, one nudge. "
                                                   if done_say else "") + "end of turn: the student paused. Judge it; usually stay silent. "
                                                   "Half-finished working, a number mid-calculation or 'so...' is NOT an answer: stay silent and let them finish. "
                                                   "If this part is now complete, set DONE: yes and say 'Good.'"))
        # measure the pause from when they actually stopped (VAD), not from when whisper delivered the text
        self._eot_h = asyncio.get_event_loop().call_later(max(0.05, self.EOT_S - (time.time() - self.last_speech)), fire)

    # ------------------------------------------------------------ gate
    def kick_gate(self):
        if self.s["solo"]:
            return                       # solo: the professor judges everything; no Haiku gate (less load)
        if self.gate_task and not self.gate_task.done():
            self.gate_again = True     # coalesce: re-run once on the latest segment
            return
        self.gate_task = asyncio.create_task(self._gate())

    async def _gate(self):
        self.s["gate_busy"] = True
        await self.push_state()
        try:
            while True:
                self.gate_again = False
                studs = [s for s in self.transcript if s["speaker"] == "S"]
                if not studs:
                    break
                try:
                    r = await ai.gate(self.transcript, self.s["established"])
                except Exception as e:
                    await self.toast(f"gate failed: {str(e)[:120]}", "warn")
                    break
                await self.on_gate(r)
                if not self.gate_again:
                    break
        finally:
            self.s["gate_busy"] = False
            await self.push_state()

    async def on_gate(self, r):
        ev, now = r.get("events", []), time.time()
        if self.s["solo"]:   # professor (board + speech, Sonnet) owns errors/stage calls; Haiku gate was giving false errors
            ev = [e for e in ev if e not in ("error_suspected", "stage_done", "self_corrected")]
            r["events"] = ev
        self.s["last_note"] = r.get("note", "")
        if r.get("fact"):
            self.s["established"].append(r["fact"])
        log = self.s["stage_log"].get(self.key())
        reason = None
        if "self_corrected" in ev and self.s["pending_errors"]:
            e = self.s["pending_errors"].pop()
            if log:
                log["errors"].append({"note": e["note"], "self_corrected": True})
            await self.toast("Self-corrected inside the hold window", "good")
        if "error_suspected" in ev and "self_corrected" not in ev:
            self.s["pending_errors"].append({"note": r.get("note", "possible error"), "t": self.now(),
                                             "deadline": now + self.s["hold_s"]})
        if "stage_done" in ev and self.s["phase"] != "personal":
            self.s["stage_ready"] = True
            reason = "student appears to have finished this stage; extend or consolidate"
            await self.toast("Stage looks done. Press M to move on.", "good")
        if "stuck" in ev:
            reason = reason or "student seems stuck"
        if "off_track" in ev:
            reason = reason or "student is going off track: " + r.get("note", "")
        self.note("gate", events=ev, note=r.get("note"))
        if reason or (r.get("refresh") and not self.s["solo"]):
            await self.request(reason or "new student contribution: " + r.get("note", ""))
        await self.push_state()

    # ------------------------------------------------------------ writer
    async def request(self, reason, forced=None, rung=None, urgent=False, speak=None):
        if self.s["solo"] and (self.s.get("time_up") or self.s["paused"]):
            return
        if self.s["solo"]:
            if reason == "new_stage" and any(e["kind"] == "resume" for e in self.log[-3:]):
                reason = ("resuming the SAME problem from last time (new stage): their old board is restored. "
                          "Say 'Let's pick up where we left off', one-line recap of what they found so far "
                          "(from the board and established facts), then pose THIS part naturally")
            elif reason == "new_stage":
                reason = ("new stage begins: read the problem naturally, then ask them to think aloud" if self.s["stage"] == 0 else
                          "previous part done well: a brief 'good', then pose THIS next part naturally as the follow-up (new stage)")
                ends = [sp[1] for sp in professor.state["spans"] if sp[1]]
                if self.s["stage"] and (professor.speaking() or (ends and time.time() - max(ends) < 10)):
                    reason = ("new stage: you JUST spoke a moment ago, so no 'good' and no recap: pose THIS next part "
                              "directly, in one or two sentences, as the natural follow-up (new stage)")
            if self.s.get("mute"):
                reason = reason.replace(", then ask them to think aloud", "; they will work on the board in silence (mute interview)")
            must = bool(forced) or "new stage" in reason or "finished this stage" in reason
            if speak is not None:
                must = speak                     # e.g. error re-checks: silence allowed if already fixed
            if "finished this stage" in reason:
                reason = "student finished this stage correctly: brief 'good', then a harder follow-up (finished)"
            asyncio.create_task(professor.turn(self, reason, rung, must_speak=must))
            return
        job = (reason, forced, rung)
        if self.writer_task and not self.writer_task.done():
            if urgent:
                self.writer_task.cancel()
            else:
                self.writer_next = job
                return
        self.writer_task = asyncio.create_task(self._write(*job))

    async def _write(self, reason, forced, rung):
        self.s["writer_busy"] = True
        await self.push_state()
        try:
            exts = (self.cart or {}).get("extensions", {}) or {}
            ext_ideas = exts.get(self.level) or exts.get(str(self.level)) or []
            rung = rung or RUNGS[min(self.s["hint_rung"].get(self.key(), 0), 2)]
            cards = await ai.writer(
                level=self.level, reason=reason, forced=forced, rung=rung,
                established=self.s["established"], transcript=self.transcript, ext_ideas=ext_ideas)
            for c in cards:
                c["verified"] = await asyncio.to_thread(carts.verify, c.get("check")) if c.get("check") else None
            bad = [c for c in cards if c["verified"] is False]
            cards = [c for c in cards if c["verified"] is not False] or cards
            if bad:
                await self.toast(f"Dropped {len(bad)} card(s) that failed the sympy check", "warn")
            await self.show(cards, reason)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self.toast(f"writer failed: {str(e)[:140]}", "warn")
        finally:
            self.s["writer_busy"] = False
            await self.push_state()
            if self.writer_next and self.writer_task is asyncio.current_task():   # a cancelled/stale task never chains
                job, self.writer_next = self.writer_next, None
                self.writer_task = asyncio.create_task(self._write(*job))

    async def show(self, cards, reason):
        self.cards, self.card_reason = cards, reason
        self.note("cards", reason=reason, cards=[c["text"] for c in cards])
        await self.send({"type": "cards", "cards": cards, "reason": reason, "level": self.level})

    # ------------------------------------------------------------ buttons
    async def action(self, a):
        if self.s["phase"] not in LIVE:
            return
        log = self.s["stage_log"].get(self.key())
        if a == "probe":
            await self.request("interviewer wants probing questions", "PROBE", urgent=True)
        elif a == "hint":
            await self.hint("interviewer asked for a hint")
        elif a == "minute":                       # "give me a minute" button: he stays out for 2 min
            self.hold_until = self.grace_until = time.time() + 120
            self.fired = set()
            await professor.shh()
        elif a == "extend":
            if log:
                log["extensions"] += 1
            await self.request("interviewer wants extensions", "EXTEND", urgent=True)
        elif a == "refresh":
            await self.request("interviewer pressed refresh", urgent=True)
        elif a == "moveon":
            await self.move_on()

    async def hint(self, reason):
        k = self.key()
        n = self.s["hint_rung"].get(k, 0)
        rung = RUNGS[min(n, 2)]
        self.s["hint_rung"][k] = min(n + 1, 3)
        log = self.s["stage_log"].get(k)
        if log:
            log["hints"] += 1
        if self.s["hint_rung"][k] >= 2:
            self.adapt(-1, "two hint rungs used")
        self.note("hint", rung=rung)
        await self.request(f"{reason} (ladder: {rung})", "HINT", rung, urgent=True)

    async def move_on(self):
        log = self.s["stage_log"].get(self.key())
        dur = time.time() - (self.s["stage_started"] or time.time())
        if log:
            log["end"] = self.now()
            log["level_end"] = self.level
            log["unresolved_errors"] = [e["note"] for e in self.s["pending_errors"]]
            if dur < C.FAST_STAGE_S and log["hints"] == 0 and self.s["phase"] != "personal":
                self.adapt(+1, "fast stage, no hints")
        self.s["pending_errors"], self.s["flag"] = [], None
        n = len((self.cart or {}).get("stages", []))
        if self.s["phase"] != "personal" and self.s["stage"] + 1 < n:
            self.s["stage"] += 1
            await self.begin_stage()
            await self.send({"type": "phase", "phase": self.s["phase"], "title": f"Stage {self.s['stage']+1}"})
            await self.request("new_stage", urgent=True)
        else:
            nxt = {"personal": "warmup" if self.s["warmup_id"] else "main", "warmup": "main", "main": "wrapup"}
            await self.enter(nxt[self.s["phase"]])
        await self.push_state()

    def adapt(self, d, why):
        new = max(-1, min(1, self.s["adapt"] + d))
        if new != self.s["adapt"]:
            self.s["adapt"], self.s["adapt_note"] = new, why
            self.note("adapt", delta=d, why=why, level=self.level)

    async def pause(self, on, force=False):
        if on == self.s["paused"] or (on and self.s["phase"] not in LIVE):
            return
        if on:
            self.s["paused"], self._pause_t = True, time.time()
            self.s["pauses"] += 1
            self.note("pause")
            await professor.shh()
        else:
            d = time.time() - self._pause_t     # paused time never counts
            self.t0 += d
            self.last_speech += d
            if self.s["stage_started"]:
                self.s["stage_started"] += d
            for e in self.s["pending_errors"]:
                e["deadline"] += d
            self.s["paused"], self.s["paused_s"] = False, self.s["paused_s"] + round(d)
            self.note("resume", s=round(d))
        await self.push_state()

    async def set_slider(self, v):
        self.s["slider"] = max(1, min(5, int(v)))
        await self.push_state()

    # ------------------------------------------------------------ clock
    async def tick_loop(self):
        while True:
            await asyncio.sleep(1)
            try:
                await self.tick()
            except Exception as e:
                print("tick error", e)

    async def _mute_tick(self, now):
        """Mute interview: they write, we watch. Silent board checks; speak (text) only for a real error that
        stands, a long stall, or a finished part."""
        bt = board.STATE["t"] or 0
        self.s["silence"] = round(now - max(bt, self.t0 or now))
        if now - self._mute_save >= 15:                        # few typed lines: keep live.json fresh anyway
            self._mute_save = now
            try:
                report.autosave(self)
            except Exception as e:
                print("autosave failed:", e, flush=True)
        if bt and bt > getattr(self, "_mute_seen", 0) and now - bt >= 12 and now - getattr(self, "_mute_chk", 0) >= 40:
            self._mute_seen, self._mute_chk = bt, now          # board settled after changes: quiet check
            await self.request("silent board check (mute interview: student writes, does not speak). Read the board. "
                               "Stay SILENT unless this part is finished (then DONE: yes) or there is a clear error "
                               "(note it as ERR, say nothing yet: they get time to catch it).", speak=False)
        stall = now - max(bt, getattr(self, "_mute_hint", 0), self.s.get("stage_started") or 0)
        if stall >= 180 and self.s["phase"] in LIVE:
            self._mute_hint = now
            await self.hint("board unchanged for 3 min (mute interview): one gentle written hint, smallest nudge")
        due = [e for e in self.s["pending_errors"] if e["deadline"] <= now]
        if due and now - bt < 15 and now - due[0].get("t0", now) < 150:
            for e in due:                                      # still writing (maybe fixing it): look again
                e.setdefault("t0", e["deadline"]); e["deadline"] = now + 15
            due = []
        for e in due:
            self.s["pending_errors"].remove(e)
            self.s["flag"] = e["note"]
            log = self.s["stage_log"].get(self.key())
            if log:
                log["errors"].append({"note": e["note"], "self_corrected": False})
            self.note("error_flag", note=e["note"])
            await self.request(f"possible uncorrected slip: {e['note']}. FIRST re-read the LATEST board: if fixed, say nothing "
                               "(FIXED: yes). Otherwise one short written nudge that makes them re-check, without stating the error.",
                               "PROBE", urgent=True, speak=False)
        await self.push_state()

    async def tick(self):
        if self.s["phase"] not in LIVE:
            if self.audio:
                self.s["audio"] = "live"
            return
        now = time.time()
        if self.s["paused"]:
            return
        if self.s["solo"] and self.s["limit_s"]:
            left = self.s["limit_s"] - self.now()
            if self.s["limit_s"] > 900 and left <= 300 and not self.s["time_warned"]:
                self.s["time_warned"] = True
                await self.request("about 5 minutes left: ask them to explain their whole solution clearly, as to a first-year", "PROBE")
            if left <= 0 and not self.s.get("time_up"):
                self.s["time_up"] = True
                last = (professor.state.get("last") or "").lower()
                if not ("time" in last and "thank" in last):      # he already closed it: no second goodbye
                    await professor.emit(self, "That's time. Thank you, that was very interesting.", "lead", "ACK", "time up")
                await self.send({"type": "timeup"})
                asyncio.create_task(self._auto_end(self.gen))
        self.s["elapsed"] = round(self.now())
        ref = self.last_speech
        if self.s["solo"]:                                   # clock starts when the professor STOPS talking
            ends = [sp[1] for sp in professor.state["spans"] if sp[1]]
            ref = now if professor.speaking() else max([ref] + ends)
            if now < getattr(self, "grace_until", 0):       # "give me a second" -> leave them alone
                ref = now
        if self.s.get("mute"):
            await self._mute_tick(now)
            return
        sil = now - ref
        self.s["silence"] = round(sil)
        a, b, c = (8, 16, 40) if self.s["drill"] == "talk" else ((60, 120, 180) if self.s["solo"] else C.SILENCE_STEPS)
        if sil >= a and a not in self.fired:
            self.fired.add(a)
            self.note("silence", s=a)
            if self.s["solo"]:
                if time.time() - board.STATE["t"] < 20:
                    await self.request(f"{a}s writing without talking (writing): ask them to talk you through it", "PROBE")
                else:
                    await self.request(f"{a}s of silence: invite them to think aloud, vary your wording", "PROBE")
            else:
                await self.show([dict(THINK_ALOUD)] + self.cards[1:3], f"{a}s silence: think-aloud prompt")
        if sil >= b and b not in self.fired:
            self.fired.add(b)
            if self.s["hint_rung"].get(self.key(), 0) == 0:
                await self.hint(f"{b}s of silence")
        if sil >= c and c not in self.fired:
            self.fired.add(c)
            self.adapt(-1, f"stuck past {C.STUCK_S}s")
            await self.request(f"{c}s of silence: offer a simpler case", "HINT", "smaller_case", urgent=True)
        # error hold expiry -> flag + nudge
        due = [e for e in self.s["pending_errors"] if e["deadline"] <= now]
        busy = (now - self.last_speech < 15 or now < getattr(self, "hold_until", 0)
                or (self.audio and (getattr(self.audio, "in_speech", False) or getattr(self.audio, "pending", 0) > 0)))
        if due and busy:                     # still working (maybe fixing it right now): look again later
            for e in due:
                e["deadline"] = now + 15
            due = []
        for e in due:
            self.s["pending_errors"].remove(e)
            self.s["flag"] = e["note"]
            log = self.s["stage_log"].get(self.key())
            if log:
                log["errors"].append({"note": e["note"], "self_corrected": False})
            self.note("error_flag", note=e["note"])
            await self.toast(f"Error not self-corrected: {e['note']}", "error")
            await self.request(f"possible uncorrected slip from {self.s['hold_s']}s+ ago: {e['note']}. FIRST re-read their "
                               "LATEST words and board: if they have since fixed it or reached the right result, do NOT mention it "
                               "(set FIXED: yes; react to their latest point instead). Only if it still stands, give one nudge "
                               "question that makes them re-check, without stating the error.",
                               "PROBE", urgent=True, speak=False)
        await self.push_state()
