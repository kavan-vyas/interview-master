"""Solo mode: Claude is the interviewer. Sees the iPad board, hears the student, speaks via macOS `say`.

One Sonnet-with-vision call per trigger (replaces the card writer in solo mode). Returns one short
utterance or SILENT. Mic input is dropped while speaking (echo guard).
"""
import asyncio
import base64
import re
import json
import shutil
import time

import ai
import board
import carts
import config as C

VOICE = C._env("SOLO_VOICE", "bm_george")   # Kokoro: light + fast; "prof" = Chatterbox
RATE = "185"
LIVE_DIR = C.SESSIONS / "_boards" / "_live"

PERSONA = """You voice TWO Cambridge mathematics interviewers in an in-person undergraduate admissions interview (Churchill style, ~40 min, one main multi-part problem):
LEAD (asks almost everything) and SECOND (quieter colleague: roughly 1 utterance in 6, a sharp side-question, an alternative angle, or "why?" on a claim the lead let pass).
The student is thinking aloud and writing on a whiteboard (latest image attached when it changed).
How you behave:
- You are mostly silent. Struggle is the point; you are watching how they think, not whether they finish.
- When you speak: at most two short sentences, under 30 words total, ONE question at most, no lists. Put any maths in $...$ LaTeX (it is read aloud properly).
- Never give the answer or the result of a step. Never say "correct!" gushingly; at most "good" / "yes, go on".
- Ask "why?" / "how do you know?" on unjustified claims. Push when they are right ("and if n were odd?").
- Spotted an error: do not name it. Ask a question that makes them re-check ("what happens at x equals zero?").
- Productive struggle is GOOD: if they are trying things, testing ideas or making slow progress, stay silent even if it takes a while. Don't rescue.
- Unproductive struggle needs a hint: they say they're stuck / ask for help, go round the same failed idea twice, or go quiet with nothing new. Then follow the hint rung exactly (rephrase -> smaller case -> key idea), smallest nudge first, framed as a question.
- Asked directly for a hint: give one (smallest that unblocks), never refuse.
- Asked "is that right?": NEVER confirm or deny, and never hand them a counterexample. Ask how THEY could check it (smallest nudge).
- Stage finished correctly: say "Good." (or similar, brief) then push a harder follow-up straight away. Interviewers extend until it gets difficult.
- Silent writing: ask them to talk you through what they are writing. Thinking aloud is what is assessed.
- Can't read their writing: ask them to say what it is.
- Dry, warm, curious, a little understated. Like a real Cambridge don. Relaxed, never testy or hurried.
- ASK WHY, like real Cambridge interviewers: once they commit to a step or result, often ask about their reasoning: "Why did you go for the cosine rule?", "What made you think of that?", "Would that work for any triangle?", "Is there a quicker way?". About once per part, never mid-calculation, never instead of letting them finish.
- CHILL. Thinking aloud is not answering. Fragments, half-sums, "so...", "that gives me...", a number mid-calculation: they are still working. Stay silent until they state a finished claim or ask you something.
- Don't correct on the fly. A slip in working-out (wrong label, arithmetic) gets time: they often catch it themselves. Only nudge once they COMMIT to a conclusion built on it.
- One nudge per issue. If they push back or say they've checked, accept it for now ("OK, carry on") and move on; never repeat the same challenge in different words.
- "Wait", "let me draw", "give me a second": stay silent. Hesitation ("maybe", "I think") is not a claim to attack.
- If they seem rattled or frustrated, ease off: "No rush." or a lighter question.
Reply in EXACTLY this line format, nothing else (SAY comes early so it can be spoken while you finish):
WHO: lead|second
SAY: <what you say aloud, or leave empty to stay silent>
TYPE: PROBE|HINT|EXTEND|ACK
ERROR: <suspected error on board/speech, or empty>
FIXED: yes|no   (the student fixed an error flagged earlier)
DONE: yes|no    (the current part/stage is now complete)
ESCALATE: yes|no (only if you are unsure and a deeper look is needed; leave SAY empty then)
BOARD: <one line: your reading of the board>"""

state = {"spans": [], "proc": None, "last": "", "busy": False, "epoch": 0}


_out = {"speakers": True, "name": "?"}


def _watch_output():
    """Background thread: which device is the default OUTPUT? (laptop speakers -> mic hears the professor).
    Polled every 4 s so plugging in headphones/AirPods mid-session just works. Never touches PortAudio."""
    import subprocess
    import threading

    def loop():
        while True:
            try:
                out = subprocess.run(["system_profiler", "SPAudioDataType"], capture_output=True, text=True, timeout=5).stdout
                name = None
                for line in out.splitlines():
                    if re.match(r"^\s{8}\S.*:$", line):
                        name = line.strip().rstrip(":")
                    elif "Default Output Device: Yes" in line and name:
                        _out["name"] = name
                        n = name.lower()
                        _out["speakers"] = (("speaker" in n or "display" in n) and "headphone" not in n) or any(k in n for k in ("shokz", "openrun", "openfit", "openmove", "aeropex", "open-ear"))   # open-ear leaks into mics
                        break
            except Exception:
                pass
            time.sleep(4)
    threading.Thread(target=loop, daemon=True).start()


_watch_output()


def on_speakers():
    return _out["speakers"]


def speaking():
    return any(e is None for _, e in state["spans"])


def echo_blocked(seg=None):
    """Drop mic segments whose AUDIO overlapped our own speech (+margin). Whisper finalises 1-3 s late,
    so judge by the segment's own timing (t_end - dur), not arrival time."""
    now = time.time()
    state["spans"] = [sp for sp in state["spans"] if sp[1] is None or now - sp[1] < 120]
    if seg and seg.get("t_end"):
        s0, s1 = seg["t_end"] - seg.get("dur", 0) - 0.3, seg["t_end"]
    else:
        s0 = s1 = now
    return any(s0 <= (b if b is not None else now) + 0.8 and s1 >= a for a, b in state["spans"])


def is_echo(seg):
    """Keep listening while the professor talks: only drop a segment that overlaps his speech AND mostly repeats
    his own words (mic hearing the speaker). Anything else said over him is the student -> keep it."""
    if not echo_blocked(seg):
        return False
    W = lambda t: re.findall(r"[a-z0-9]+", (t or "").lower())
    mine = set(w for t0, tx in state.get("said", []) if time.time() - t0 < 60 for w in W(tx))
    heard = W(seg.get("text"))
    if not heard:
        return True
    return sum(w in mine for w in heard) / len(heard) >= 0.6


_say_lock = asyncio.Lock()


def outlasted(seg):
    """Student kept talking >1 s past the end of the professor's line -> it's them, keep it."""
    ends = [sp[1] for sp in state["spans"] if sp[1]]
    return bool(seg.get("t_end") and ends and seg["t_end"] - max(ends) > 1.0 and not speaking())


async def shh():
    from tts import voice
    state["say_gen"] = state.get("say_gen", 0) + 1     # drop anything still queued
    state["shh_at"] = time.time()                       # replies to turns asked before this are dropped too
    voice.stop()
    p = state.get("proc")
    if p and p.returncode is None:
        p.kill()


async def say(text, who=None, voice_id=None):
    """Chatterbox/Kokoro with maths-aware reading; falls back to macOS say."""
    if not text:
        return
    gen = state.get("say_gen", 0)
    async with _say_lock:                    # queue lines; a new line never cuts off the one playing
        if gen != state.get("say_gen", 0):
            return                           # shh'd while waiting
        await _say(text, who, voice_id)


async def _say(text, who=None, voice_id=None, on_start=None):
    span = [time.time(), None]
    state["spans"].append(span)
    state.setdefault("said", []).append((span[0], text))
    state["said"] = state["said"][-12:]
    if state.get("speaker_off"):          # muted speaker: caption only, no sound
        if on_start:
            on_start(len(text) / 15)
        span[1] = time.time()
        return
    try:
        from tts import voice
        v = voice_id or state.get("voice") or VOICE
        if who == "second":
            v = "second" if v == "prof" else ("bf_emma" if v.startswith("bm") else "bm_lewis")
        await asyncio.to_thread(voice.speak, text, v, float(state.get("speed") or 1.12), on_start)
    except Exception as e:
        print("voice failed, using say:", e, flush=True)
        if on_start:
            on_start(len(text) / 15)
        if shutil.which("say"):
            state["proc"] = await asyncio.create_subprocess_exec("say", "-v", "Daniel", "-r", RATE, text)
            await state["proc"].wait()
    finally:
        span[1] = time.time()


def reset():
    """New session: forget the last line, cancel queued turns, bump epoch so in-flight thinks are discarded."""
    state.update(last="", last_who=None, busy=False, epoch=state["epoch"] + 1)
    for k in ("next", "speaker_off", "shh_at"):
        state.pop(k, None)                  # speaker off never leaks into the next session
    for b in (FAST, DEEP):
        b.stale = True                      # fresh conversation + image next connect (warm on next solo setup)


def warm():
    """Background at startup: Kokoro (light, default). Chatterbox only if that voice is chosen."""
    try:
        from tts import voice
        voice.load()
        if (state.get("voice") or VOICE) in ("prof", "second"):
            voice.cb_start()
    except Exception as e:
        print("voice warm failed:", e)


# ---------------- brains: persistent Claude sessions, board image inline, streamed ----------------
class Brain:
    """One warm Agent-SDK session (no process spawn per turn). Image goes in the message itself.
    History is kept for continuity but reset every MAX turns so context (and latency) stays small."""
    MAX = 10

    def __init__(self, model):
        self.model, self.client, self.lock, self.turns, self.img_sent = model, None, asyncio.Lock(), 0, None
        self.stale = False

    async def _connect(self):
        if self.stale:                      # new session: forget the last one's history (under self.lock)
            self.stale = False
            await self.drop()
        if self.client is None:
            from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
            c = ClaudeSDKClient(ClaudeAgentOptions(
                model=self.model, system_prompt=PERSONA, tools=[], strict_mcp_config=True,
                setting_sources=["project"], cli_path=C.CLAUDE_BIN, cwd=str(C.SANDBOX), max_turns=1,
                include_partial_messages=True, effort="low"))   # live talk: answer fast, don't deliberate
            await asyncio.wait_for(c.connect(), 30)
            self.client, self.turns, self.img_sent = c, 0, None

    async def drop(self):
        c, self.client = self.client, None
        if c:
            try:
                await asyncio.wait_for(c.disconnect(), 5)
            except Exception:
                pass

    async def warm(self):
        try:
            async with self.lock:
                await self._connect()
        except Exception as e:
            print(f"[brain {self.model}] warm failed: {e}", flush=True)

    async def ask(self, text, png, on_say, timeout=40):
        """Stream one reply. on_say(who, text) fires the moment the SAY line is complete."""
        from claude_agent_sdk import ResultMessage, StreamEvent
        async with self.lock:
            await self._connect()
            blocks = []
            if png and png != self.img_sent:
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                           "data": base64.b64encode(png).decode()}})
                self.img_sent = png
            blocks.append({"type": "text", "text": text + ("" if blocks[:1] and blocks[0]["type"] == "image" else
                                                           "\n(Board unchanged since your last look.)" if png else "\n(No board yet.)")})

            async def msg():
                yield {"type": "user", "message": {"role": "user", "content": blocks},
                       "parent_tool_use_id": None, "session_id": "default"}
            buf, fired = "", False

            async def read():
                nonlocal buf, fired
                async for m in self.client.receive_response():
                    if isinstance(m, StreamEvent):
                        d = (m.event or {}).get("delta") or {}
                        if d.get("type") == "text_delta":
                            buf += d["text"]
                            if not fired and re.search(r"^SAY:.*\n", buf, re.M):
                                fired = True
                                r = parse(buf)
                                await on_say(r)
                    elif isinstance(m, ResultMessage):
                        if m.is_error:
                            raise ai.AIError(str(m.result)[:200])
                        buf = buf or (m.result or "")
            async def run():
                await self.client.query(msg())
                await read()
            try:
                await asyncio.wait_for(run(), timeout)     # send + stream both bounded
            except BaseException:
                await self.drop()             # session state unknown -> fresh next time
                raise
            r = parse(buf)
            if not fired:
                await on_say(r)
            self.turns += 1
            if self.turns >= self.MAX:
                asyncio.create_task(self._recycle())
            return r

    async def _recycle(self):
        async with self.lock:
            await self.drop()
            await self._connect()


def parse(txt):
    r = {}
    for line in txt.splitlines():
        m = re.match(r"\s*(WHO|SAY|TYPE|ERROR|FIXED|DONE|ESCALATE|BOARD):\s*(.*)", line)
        if m:
            r[m.group(1).lower()] = m.group(2).strip()
    yes = lambda k: r.get(k, "").lower().startswith("y")
    return {"who": r.get("who", "lead"), "say": r.get("say", ""), "type": (r.get("type") or "PROBE").split("|")[0].upper(),
            "error": "" if r.get("error", "").lower() in ("", "none", "-", "empty", "no") else r["error"],
            "self_corrected": yes("fixed"), "stage_done": yes("done"), "escalate": yes("escalate"), "board": r.get("board", "")}


FAST = Brain(C.GATE_MODEL)     # Haiku: quick acks, silence/writing nudges, end-of-turn judgements
DEEP = Brain(C.WRITER_MODEL)   # Sonnet: hints, errors, questions, stage changes
DEEP_KEYS = ("hint", "error", "asked", "stage", "finished", "explain", "time", "repeat", "end of turn")  # Sonnet is ~1 s warm; Haiku only for nudges


async def warm_brains():
    await asyncio.gather(FAST.warm(), DEEP.warm())


MUTE_NOTE = ("MUTE INTERVIEW: the student is somewhere they cannot talk. They work on the board and read your words as "
             "text; they may type to you. Never ask them to talk, explain aloud or think aloud. Keep lines short; maths in LaTeX. "
             "Mostly leave them alone: no 'talk me through it', no check-ins. Comment only on a real error that stands, "
             "a finished part (brief 'Good.' + next part), a long stall, or what they typed.")


def context(o, reason, rung):
    tail = ai.fmt_lines(o.transcript[-25:]) or "(nothing said yet)"
    rung_txt = ai.RUNG_TEXT.get(rung, "") if rung else ""
    ext = ((o.cart or {}).get("extensions") or {})
    ideas = ext.get(str(o.level)) or ext.get(o.level) or []
    return f"""{ai._stage_block(o.cart, o.s['stage']) if o.cart else 'Personal-statement discussion.'}
Difficulty {o.level}/5: {ai.LEVELS[o.level]}
Established so far: {'; '.join(o.s['established'][-8:]) or '-'}
Pending (unflagged) errors you noted: {'; '.join(e['note'] for e in o.s['pending_errors']) or '-'}
You said last: {state['last'] or '-'}
Transcript (S = student, I = you):
{tail}

TRIGGER: {reason}
{('Hint rung: ' + rung_txt) if rung_txt else ''}
Extension ideas (for follow-ups): {'; '.join(map(str, ideas)) or 'invent a natural harder variant'}
Time used: {round(o.now()/60)} of {round((o.s.get('limit_s') or 2400)/60)} min.
{MUTE_NOTE if o.s.get("mute") else ""}
Decide now. Prefer staying silent unless the trigger genuinely needs you."""


FALLBACK = {"silence": "Talk me through what you're thinking, even if it's half-formed.",
            "writing": "Talk me through what you're writing.",
            "finished": "Good. Can you push that any further?",
            "default": "Go on."}


async def repeat_last(o):
    if state["last"]:
        asyncio.create_task(say(state["last"], state.get("last_who")))


async def emit(o, text, who="lead", typ="PROBE", reason=""):
    """Queue a line. When its turn comes it is DROPPED if the student spoke after it was queued (it would answer
    old speech); captions + transcript only for lines actually spoken."""
    t_line, gen = time.time(), state.get("say_gen", 0)
    asyncio.create_task(_emit_voice(o, text, who, typ, reason, t_line, gen))


async def _emit_voice(o, text, who, typ, reason, t_line, gen):
    async with _say_lock:
        quiet_since = time.time() - (state.get("line_end") or 0)
        if (not o.s.get("mute") and state.get("line_at") and state.get("student_at", 0) < state["line_at"] and quiet_since < 30
                and not any(k in reason for k in ("stage", "time", "asked", "repeat"))):
            print(f"[prof] held (no reply yet to last line): {text[:60]!r}", flush=True)   # one line per student turn
            return
        a, t_w = getattr(o, "audio", None), time.time()
        while (a and "time" not in reason and time.time() - t_w < 12
               and (getattr(a, "in_speech", False) or getattr(a, "pending", 0) > 0)):
            await asyncio.sleep(0.12)      # they're talking / STT still working: listen first
        core = any(k in reason for k in ("stage", "resum", "time", "asked", "help", "stuck", "hint", "TYPED"))
        if gen != state.get("say_gen", 0) or (state.get("student_at", 0) > t_line + 0.2 and not core):
            print(f"[prof] dropped stale: {text[:60]!r}", flush=True)
            return
        if (time.time() < getattr(o, "hold_until", 0)
                and not any(k in reason for k in ("asked", "help", "stuck", "hint", "time", "repeat"))):
            print(f"[prof] dropped (student asked for time): {text[:60]!r}", flush=True)
            return
        state["last"], state["last_who"] = text, who
        loop = asyncio.get_running_loop()
        card = {"type": typ, "text": text, "latex": "", "spoken": text, "check": "", "verified": None, "who": who}

        def started(dur):       # first audio is out: caption types in step with the voice
            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(
                o.send({"type": "caption", "text": text, "who": who, "dur": round(dur, 2)})))
        v = asyncio.create_task(_say(text, who, None, started))
        await o.send({"type": "prof", "s": "speaking"})
        o.cards, o.card_reason = [card], reason
        o.note("cards", reason=reason, cards=[text], who=who)
        await o.send({"type": "cards", "cards": [card], "reason": "Professor: " + reason, "level": o.level})
        await o.add_segment({"speaker": "I", "text": text, "conf": 1.0, "spk_conf": 1.0, "prof": True, "who": who})
        state["line_at"] = time.time()
        await v
        state["line_end"] = time.time()
        await o.send({"type": "prof", "s": "listening"})


def _live(o, epoch):
    return epoch == state["epoch"] and not o.s["paused"] and o.s["phase"] in ("personal", "warmup", "main") and not o.s.get("time_up")


async def turn(o, reason, rung=None, must_speak=False):
    """Solo turn: stream a reply from the right brain; speak as soon as the SAY line lands.
    A non-forced reply is dropped if the student starts talking again before it is spoken."""
    if state["busy"]:
        nx = state.get("next")
        if must_speak or not (nx and nx[4]):        # keep the latest; a queued must-speak is never replaced
            state["next"] = (reason, rung, time.time(), state["epoch"], must_speak)
        return
    state["busy"], epoch, t_req = True, state["epoch"], time.time()
    spoke = {"v": False}
    must = {"v": must_speak}
    t0 = time.time()
    hushed = lambda: not must["v"] and state.get("shh_at", 0) > t_req   # Shh'd after this was asked

    async def on_say(r):
        text = (r.get("say") or "").strip()
        if not _live(o, epoch) or not text or hushed():
            return
        if not must["v"] and o.last_speech > t_req + 0.3:
            o.note("prof_dropped", reason=reason)      # they kept talking: never cut in
            return
        spoke["v"] = True
        print(f"[prof] {round(time.time() - t0, 2)}s -> {text[:70]!r}", flush=True)
        await emit(o, text, "second" if r.get("who") == "second" else "lead", r.get("type") or "PROBE", reason)

    try:
        o.s["writer_busy"] = True
        await o.push_state()
        png = board.latest_png()
        deep = must_speak or any(k in reason.lower() for k in DEEP_KEYS)
        brain = DEEP if deep else FAST
        msg = context(o, reason + (" (you MUST say something now; silence is not allowed)" if must_speak else ""), rung)
        try:
            r = await brain.ask(msg, png, on_say, timeout=30)
        except Exception as e:                     # API hiccup / timeout: one fresh retry, never silent forever
            print(f"[prof] retry after {type(e).__name__}", flush=True)
            if not _live(o, epoch):
                raise
            brain = DEEP
            r = await brain.ask(msg, png, on_say, timeout=30)
        if r.get("escalate") and not spoke["v"] and brain is FAST:
            r = await DEEP.ask(msg, png, on_say)
        ai.stats["writer_calls"] += 1
        ai.stats["last_writer_s"] = round(time.time() - t0, 2)
        if not _live(o, epoch):
            return
        if r.get("board"):
            o.s["last_note"] = "board: " + r["board"][:160]
            o.note("board_seen", text=r["board"][:240])
        if r.get("self_corrected") and o.s["pending_errors"]:
            e = o.s["pending_errors"].pop()
            log = o.s["stage_log"].get(o.key())
            if log:
                log["errors"].append({"note": e["note"], "self_corrected": True})
        err = (r.get("error") or "").strip()
        known = [x["note"].lower()[:40] for x in o.s["pending_errors"]] + [(o.s.get("flag") or "").lower()[:40]]
        if err and not r.get("self_corrected") and not any(err.lower()[:40] == k for k in known if k):
            o.s["pending_errors"].append({"note": err, "t": o.now(), "deadline": time.time() + o.s["hold_s"]})
        said = [x for x in o.transcript if x["speaker"] == "S" and x["t"] >= (o.s["stage_log"].get(o.key()) or {}).get("start", 0)]
        wrote = o.s.get("mute") and (board.STATE["t"] or 0) > (o.s.get("stage_started") or 0)   # mute: board work counts
        pend = o.s.pop("done_pending", False)
        newly_done = ((bool(r.get("stage_done")) or pend) and o.s["phase"] != "personal" and not o.s["stage_ready"]
                      and "new stage" not in reason and (len(said) >= 1 or wrote))   # never chain-skip parts
        if newly_done and not pend and (r.get("say") or "").rstrip().endswith("?"):
            o.s["done_pending"] = True        # he just asked something: let them answer, move on next turn
            newly_done = False
        if newly_done:
            o.s["stage_ready"] = True
            if o.s["stage"] + 1 < len((o.cart or {}).get("stages", [])):
                o.note("prof_stage_done", stage=o.s["stage"])
                asyncio.create_task(o.move_on())        # next part of the cartridge = the follow-up
                return
            must["v"], reason = True, reason + " (finished)"
        if not spoke["v"] and not must["v"] and reason.startswith("end of turn") and _live(o, epoch) and not hushed():
            await _backchannel(o)
        if must["v"] and not spoke["v"]:                 # guaranteed prompt
            k = next((k for k in FALLBACK if k in reason), "default")
            await emit(o, FALLBACK[k], "lead", "PROBE", reason)
    except Exception as e:
        print(f"[prof] FAILED {type(e).__name__}: {str(e)[:200]}", flush=True)
        auth = any(k in str(e) for k in ("authenticate", "OAuth", "login", "401"))
        if time.time() - state.get("ai_warned", 0) > 60:      # say WHY he's silent, never fake it with "Go on."
            state["ai_warned"] = time.time()
            await o.send({"type": "toast", "kind": "error", "text":
                          "Professor offline: Claude sign-in failed. In Terminal run: claude  then /login" if auth
                          else f"Professor's AI call failed ({type(e).__name__}). Retrying on your next line."})
        if auth:
            return
        if must_speak and _live(o, epoch):
            k = next((k for k in FALLBACK if k in reason), "default")
            await emit(o, FALLBACK[k], "lead", "PROBE", reason)
    finally:
        if epoch == state["epoch"]:         # after a reset, busy/next belong to the new session: leave them
            state["busy"] = False
            o.s["writer_busy"] = False
            await o.push_state()
            nxt = state.pop("next", None)
            if (nxt and nxt[3] == state["epoch"] and not (o.last_speech > nxt[2] and ("silence" in nxt[0] or nxt[0].startswith("end of turn")))
                    and (nxt[4] or state.get("shh_at", 0) <= nxt[2])):     # they spoke since: a fresh end-of-turn follows
                asyncio.create_task(turn(o, nxt[0], nxt[1], must_speak=nxt[4]))


ACKS = ["Mm-hm.", "Okay.", "Right.", "Mm.", "Sure.", "Okay, go on."]


async def _backchannel(o):
    """He chose silence: now and then a quiet 'mm-hm' so it doesn't feel like talking to a wall."""
    import random
    now = time.time()
    last = next((x for x in reversed(o.transcript) if x["speaker"] == "S"), None)
    a = getattr(o, "audio", None)
    if (not last or len(last["text"].split()) < 6 or now - state.get("ack_at", 0) < 30
            or now < getattr(o, "hold_until", 0) or now - o.last_speech < 1.0
            or (a and (getattr(a, "in_speech", False) or getattr(a, "pending", 0) > 0))
            or random.random() > 0.6):
        return
    state["ack_at"] = now
    ack = random.choice([x for x in ACKS if x != state.get("last_ack")])
    state["last_ack"] = ack
    o.note("ack", text=ack)
    await say(ack)


def _board_file():
    """model_walk (one-off CLI call) still reads the board from disk."""
    png = board.latest_png()
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    f = LIVE_DIR / "board.png"
    if png:
        f.write_bytes(png)
    elif f.exists():
        f.unlink()
    return bool(png)


async def model_walk(o):
    """After the session: full model solution, compared against what the student did."""
    has_board = _board_file()
    c = (carts.load(o.s["main_id"]) if o.s.get("main_id") else None) or o.cart or {}
    stages = "\n".join(f"{i+1}. {s.get('goal')}: {s.get('solution')}" for i, s in enumerate(c.get("stages", [])))
    prompt = f"""You are a Cambridge maths interviewer debriefing a student after a mock interview.
Problem: {c.get('title')}: {c.get('statement')}
Reference solution by stage:
{stages}
Student transcript:
{ai.fmt_lines(o.transcript[-120:])}
Final board: {'./board.png (read it)' if has_board else 'none'}

Write a model-solution walk in Markdown with $LaTeX$ maths: for each stage, the clean argument, then
"**You:**" one line on what the student did (or missed), then "**Better:**" the sharper move. End with
3 bullet "next time" habits. Concise."""
    return await ai.claude(prompt, C.WRITER_MODEL, 240, tools="Read" if has_board else None,
                           cwd=LIVE_DIR, max_turns=3 if has_board else 1)
