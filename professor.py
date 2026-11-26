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
_HEADPHONES = ("headphone", "headset", "airpods", "earpods", "earphone", "earbud", "buds", "in-ear", "beats", "powerbeats",
               "wh-1000", "wf-1000", "quietcomfort", "qc35", "qc45", "qc ultra", "bose nc", "momentum", "jabra", "sennheiser",
               "pxc", "px7", "px8", "nothing ear", "soundcore life", "soundcore space", "liberty")
_OPEN_EAR = ("shokz", "openrun", "openfit", "openmove", "aeropex", "open-ear", "openswim", "opencomm")


def is_speaker_device(name):
    """Default output -> does its sound reach the mic? Unknown devices (monitors, USB / Bluetooth speakers, virtual
    devices) count as SPEAKERS: echo guard on, no barge-in. Only known headphones / earbuds count as headphones."""
    n = (name or "").lower()
    if any(k in n for k in _OPEN_EAR):          # bone-conduction / open-ear: leaks into mics
        return True
    return not any(k in n for k in _HEADPHONES)


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
                        _out["speakers"] = is_speaker_device(name)
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


def _toks(t):
    """Words with their character spans; accents folded (whisper writes 'Hopital' for 'Hôpital')."""
    import unicodedata
    out = []
    for m in re.finditer(r"[^\W_]+", t or ""):
        w = "".join(c for c in unicodedata.normalize("NFKD", m.group(0).lower()) if not unicodedata.combining(c))
        out.append((w, m.start(), m.end()))
    return out


def strip_echo(seg):
    """Remove the professor's own words picked up by the mic from a segment, keep the student's.
    Only lines whose speech overlapped this segment (echo_blocked) are matched. Each run of >= 5 heard words that
    lines up, in order, with one of his recent lines (>= 70% of the run matching) is cut out. Returns the cleaned
    text ('' = it was all echo)."""
    import difflib
    text = seg.get("text") or ""
    if not text or not echo_blocked(seg):
        return text
    heard = _toks(text)
    if not heard:
        return text
    hw = [w for w, _, _ in heard]
    cut = [False] * len(heard)
    for t0, said in state.get("said", []):
        if time.time() - t0 > 90:
            continue
        sw = [w for w, _, _ in _toks(said)]
        if len(sw) < 3:
            continue
        blocks = [b for b in difflib.SequenceMatcher(None, hw, sw, autojunk=False).get_matching_blocks() if b.size]
        runs, cur = [], None                  # merge blocks separated by small gaps (a misheard word or two)
        for b in blocks:
            if cur and b.a - (cur[1]) <= 2 and b.b - cur[3] <= 2:
                cur = [cur[0], b.a + b.size, cur[2] + b.size, b.b + b.size]
            else:
                if cur:
                    runs.append(cur)
                cur = [b.a, b.a + b.size, b.size, b.b + b.size]
        if cur:
            runs.append(cur)
        for a0, a1, matched, _ in runs:
            if matched >= min(5, len(sw)) and matched / max(1, a1 - a0) >= 0.7:
                for i in range(a0, a1):
                    cut[i] = True
    if not any(cut):
        return text
    keep, pos = [], 0                         # rebuild from the original characters of the kept words
    i = 0
    while i < len(heard):
        if cut[i]:
            j = i
            while j + 1 < len(heard) and cut[j + 1]:
                j += 1
            keep.append(text[pos:heard[i][1]])
            pos = heard[j][2]
            pos += len(re.match(r"[\s.,;:!?'\"]*", text[pos:]).group(0))   # the echoed sentence's punctuation
            i = j + 1
        else:
            i += 1
    keep.append(text[pos:])
    out = re.sub(r"\s{2,}", " ", "".join(keep)).strip(" ,;:")
    return out if len(_toks(out)) >= 2 else ""


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


def stop_voice():
    """Speaker switched off: stop the sound only. Queued and in-flight lines still arrive (as captions)."""
    from tts import voice
    voice.stop()
    p = state.get("proc")
    if p and p.returncode is None:
        p.kill()


async def say(text, who=None, voice_id=None, on_start=None):
    """Chatterbox/Kokoro with maths-aware reading; falls back to macOS say."""
    if not text:
        return
    gen = state.get("say_gen", 0)
    async with _say_lock:                    # queue lines; a new line never cuts off the one playing
        if gen != state.get("say_gen", 0):
            return                           # shh'd while waiting
        await _say(text, who, voice_id, on_start)


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
    for k in ("next", "queue", "speaker_off", "shh_at", "offline"):
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


_PLACEHOLDER = re.compile(r"[\s(\[<{]*(silent|silence|stay silent|staying silent|\(?silent\)?|nothing|none|empty|n/?a|"
                          r"no reply|say nothing|-+|—|–|\.{2,}|…)?[\s)\]>}.]*", re.I)


def parse(txt):
    r = {}
    for line in txt.splitlines():
        m = re.match(r"\s*(WHO|SAY|TYPE|ERROR|FIXED|DONE|ESCALATE|BOARD):\s*(.*)", line)
        if m:
            r[m.group(1).lower()] = m.group(2).strip()
    yes = lambda k: r.get(k, "").lower().startswith("y")
    say = r.get("say", "")
    if _PLACEHOLDER.fullmatch(say.strip().strip("*_`\"'").strip()):
        say = ""                              # "(silent)", "SILENT", "-", "<empty>": the model meant silence
    return {"who": r.get("who", "lead"), "say": say, "type": (r.get("type") or "PROBE").split("|")[0].upper(),
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
            "default": "Take your time. Tell me where you've got to."}
RUNG_IDX = {"rephrase": 0, "smaller_case": 1, "key_idea": 2}
_PART_RE = re.compile(r"\n\s*(?=\((?:i{1,3}|iv|v|vi{1,3}|ix|x|[a-h])\)\s)")


def part_text(cart, k):
    """What part k asks, from the cartridge (python twin of app.js partOnly): statement part if the parts line up
    with the stages, else the stage goal. Stage 0 also gets any shared setup text."""
    cart = cart or {}
    st, goals = (cart.get("statement") or "").strip(), [s.get("goal") or "" for s in cart.get("stages") or []]
    bits = [b.strip() for b in _PART_RE.split(st) if b.strip()]
    if len(bits) <= 1:                       # no (i)/(ii) labels: the stage goal (what the screen shows)
        return goals[k] if len(goals) > 1 and k < len(goals) and goals[k] else st
    starts_with_part = bool(re.match(r"\((?:i|a)\)\s", bits[0]))
    setup, parts = ("", bits) if starts_with_part else (bits[0], bits[1:])
    p = parts[k] if len(parts) == len(goals) and k < len(parts) else (goals[k] if k < len(goals) else parts[-1])
    return (setup + "\n\n" + p).strip() if k == 0 and setup else p


def fallback_line(o, reason, rung=None):
    """Must-speak line when the AI is down or stayed silent: never a contentless 'Go on.'. A new part is read
    straight from the cartridge; a hint comes from the stage's own hint list."""
    low = reason.lower()
    cart, k, mute = o.cart, o.s.get("stage", 0), o.s.get("mute")
    if cart and any(x in low for x in ("new stage", "resum", "pose this")):
        txt = part_text(cart, k)
        if txt:
            if k == 0:
                return "Here's the question. " + txt + (" Work on it on the board." if mute else " Talk me through your first thoughts.")
            return "Next part. " + txt
    if cart and any(x in low for x in ("hint", "stuck", "help", "simpler case")):
        hints = [h for h in ((o.stage() or {}).get("hints") or []) if isinstance(h, str) and h.strip()]
        if hints:
            return hints[min(RUNG_IDX.get(rung or "", 0), len(hints) - 1)]
    if "time" in low and "minute" in low:
        return "About five minutes left. Can you explain your whole solution clearly, as if to a first-year?"
    k2 = next((x for x in FALLBACK if x in low), "default")
    if k2 == "silence" and mute:
        k2 = "default"
    return FALLBACK[k2]


async def repeat_last(o):
    """Repeat his last line: spoken (if the speaker is on) and always shown as a caption."""
    if not state["last"]:
        return
    text, who = state["last"], state.get("last_who")
    loop = asyncio.get_running_loop()

    def started(dur):
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(
            o.send({"type": "caption", "text": text, "who": who, "dur": round(dur, 2)})))
    asyncio.create_task(say(text, who, on_start=started))


async def emit(o, text, who="lead", typ="PROBE", reason="", must=False):
    """Queue a line. When its turn comes it is DROPPED if the student spoke after it was queued (it would answer
    old speech); captions + transcript only for lines actually spoken. A must-speak line (new part, time, a
    guaranteed prompt) is never dropped for that, only by an explicit Shh / pause / reset."""
    t_line, gen = time.time(), state.get("say_gen", 0)
    asyncio.create_task(_emit_voice(o, text, who, typ, reason, t_line, gen, must))


async def _emit_voice(o, text, who, typ, reason, t_line, gen, must=False):
    async with _say_lock:
        quiet_since = time.time() - (state.get("line_end") or 0)
        if (not must and not o.s.get("mute") and state.get("line_at") and state.get("student_at", 0) < state["line_at"] and quiet_since < 30
                and not any(k in reason for k in ("stage", "time", "asked", "repeat"))):
            print(f"[prof] held (no reply yet to last line): {text[:60]!r}", flush=True)   # one line per student turn
            return
        a, t_w = getattr(o, "audio", None), time.time()
        while (a and not o.s.get("mute") and "time" not in reason and time.time() - t_w < 12   # mute: room noise is not the student
               and (getattr(a, "in_speech", False) or getattr(a, "pending", 0) > 0)):
            await asyncio.sleep(0.12)      # they're talking / STT still working: listen first
        core = must or any(k in reason for k in ("stage", "resum", "time", "asked", "help", "stuck", "hint", "TYPED"))
        if gen != state.get("say_gen", 0) or (state.get("student_at", 0) > t_line + 0.2 and not core):
            print(f"[prof] dropped stale: {text[:60]!r}", flush=True)
            return
        if (time.time() < getattr(o, "hold_until", 0) and not must
                and not any(k in reason for k in ("asked", "help", "stuck", "hint", "time", "repeat", "stage", "resum"))):
            print(f"[prof] dropped (student asked for time): {text[:60]!r}", flush=True)
            return
        if o.s.get("paused") or o.s.get("phase") not in ("personal", "warmup", "main", "wrapup"):
            print(f"[prof] dropped (paused / not live): {text[:60]!r}", flush=True)
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
        o.note("cards", reason=reason, cards=[text], who=who, typ=typ)
        await o.send({"type": "cards", "cards": [card], "reason": "Professor: " + reason, "level": o.level})
        await o.add_segment({"speaker": "I", "text": text, "conf": 1.0, "spk_conf": 1.0, "prof": True, "who": who})
        state["line_at"] = time.time()
        await v
        state["line_end"] = time.time()
        await o.send({"type": "prof", "s": "offline" if state.get("offline") else "listening"})


def _live(o, epoch):
    return epoch == state["epoch"] and not o.s["paused"] and o.s["phase"] in ("personal", "warmup", "main") and not o.s.get("time_up")


def _same_error(a, b):
    """Paraphrases of one slip count once ('sign error in the derivative' ~ 'the derivative has a sign error')."""
    a, b = (a or "").lower().strip(), (b or "").lower().strip()
    if not a or not b:
        return False
    if a[:40] == b[:40]:
        return True
    A = {w for w in re.findall(r"[a-z0-9]+", a) if len(w) > 2}
    B = {w for w in re.findall(r"[a-z0-9]+", b) if len(w) > 2}
    return bool(A and B) and len(A & B) / len(A | B) >= 0.5


async def _offline(o, on, auth=False):
    """Visible AI state: the status line says 'Professor offline' while Claude is failing; a background retry
    re-connects every 20 s and clears it."""
    was = state.get("offline")
    state["offline"] = on
    if on and not was:
        await o.send({"type": "prof", "s": "offline"})
        if not (state.get("recover") and not state["recover"].done()):
            state["recover"] = asyncio.create_task(_recover(o, state["epoch"]))
    elif not on and was:
        await o.send({"type": "prof", "s": "listening"})
        await o.send({"type": "toast", "kind": "good", "text": "Professor back online."})


async def _recover(o, epoch):
    for _ in range(30):
        await asyncio.sleep(20)
        if epoch != state["epoch"] or not state.get("offline"):
            return
        try:
            await DEEP.warm()
            if getattr(DEEP, "client", True) is not None:
                await _offline(o, False)
                return
        except Exception:
            pass


async def turn(o, reason, rung=None, must_speak=False, t_asked=None):
    """Solo turn: stream a reply from the right brain; speak as soon as the SAY line lands.
    A non-forced reply is dropped if the student starts talking again before it is spoken.
    While a turn is in flight, must-speak turns queue in order (none is lost); only the latest other turn is kept."""
    if state["busy"]:
        item = (reason, rung, time.time(), state["epoch"], must_speak)
        if must_speak:
            q = state.setdefault("queue", [])
            if not any(x[0] == reason and x[3] == item[3] for x in q):
                q.append(item)
        else:
            state["next"] = item
        return
    state["busy"], epoch, t_req = True, state["epoch"], time.time()
    t_asked = t_asked or t_req
    spoke = {"v": False}
    must = {"v": must_speak}
    said_r = {}
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
        said_r.update(r)
        print(f"[prof] {round(time.time() - t0, 2)}s -> {text[:70]!r}", flush=True)
        await emit(o, text, "second" if r.get("who") == "second" else "lead", r.get("type") or "PROBE", reason, must=must["v"])

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
            if spoke["v"]:                         # he already said this turn's line: a retry would speak twice
                print(f"[prof] stream ended with {type(e).__name__} after SAY: no retry", flush=True)
                r = dict(said_r)
            else:
                print(f"[prof] retry after {type(e).__name__}", flush=True)
                if not _live(o, epoch):
                    raise
                brain = DEEP
                r = await brain.ask(msg, png, on_say, timeout=30)
        if r.get("escalate") and not spoke["v"] and brain is FAST:
            r = await DEEP.ask(msg, png, on_say)
        if state.get("offline"):
            await _offline(o, False)
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
        log = o.s["stage_log"].get(o.key()) or {}
        known = [x["note"] for x in o.s["pending_errors"]] + [o.s.get("flag") or ""] + [x["note"] for x in log.get("errors", [])]
        if err and not r.get("self_corrected") and not any(_same_error(err, k) for k in known if k):
            o.s["pending_errors"].append({"note": err, "t": o.now(), "deadline": time.time() + o.s["hold_s"]})
        said = [x for x in o.transcript if x["speaker"] == "S" and x["t"] >= log.get("start", 0)]
        wrote = o.s.get("mute") and (board.STATE["t"] or 0) > (o.s.get("stage_started") or 0)   # mute: board work counts
        pend = False
        asked_at = o.s.get("done_pending")          # o.now() when he asked his last question on a finished part
        if asked_at is not None and asked_at is not False:
            rel_req = t_asked - (o.t0 or t_asked)
            answered = any(x["speaker"] == "S" and x["t"] > asked_at for x in o.transcript)
            if answered and rel_req > asked_at:      # only a turn triggered by their answer moves on
                o.s.pop("done_pending", None)
                pend = True
        newly_done = ((bool(r.get("stage_done")) or pend) and o.s["phase"] != "personal" and not o.s["stage_ready"]
                      and "new stage" not in reason and (len(said) >= 1 or wrote))   # never chain-skip parts
        if newly_done and not pend and (r.get("say") or "").rstrip().endswith("?") and spoke["v"]:
            o.s["done_pending"] = o.now()     # he just asked something: let them answer, move on after their reply
            newly_done = False
        if newly_done:
            o.s["stage_ready"] = True
            o.s.pop("done_pending", None)
            if o.s["stage"] + 1 < len((o.cart or {}).get("stages", [])):
                o.note("prof_stage_done", stage=o.s["stage"])
                asyncio.create_task(o.move_on())        # next part of the cartridge = the follow-up
                return
            if log and log.get("end") is None:          # last part finished: it counts as done in the report
                log["end"], log["level_end"] = o.now(), o.level
                o.note("prof_stage_done", stage=o.s["stage"])
            must["v"], reason = True, reason + " (finished)"
        if not spoke["v"] and not must["v"] and reason.startswith("end of turn") and _live(o, epoch) and not hushed():
            await _backchannel(o)
        if must["v"] and not spoke["v"]:                 # guaranteed prompt
            await emit(o, fallback_line(o, reason, rung), "lead", "HINT" if "hint" in reason.lower() else "PROBE", reason, must=True)
    except Exception as e:
        print(f"[prof] FAILED {type(e).__name__}: {str(e)[:200]}", flush=True)
        auth = any(k in str(e) for k in ("authenticate", "OAuth", "login", "401"))
        await _offline(o, True, auth)
        if time.time() - state.get("ai_warned", 0) > 60:      # say WHY his lines are canned / missing
            state["ai_warned"] = time.time()
            await o.send({"type": "toast", "kind": "error", "text":
                          "Professor offline: Claude sign-in failed. In Terminal run: claude  then /login" if auth
                          else f"Professor's AI call failed ({type(e).__name__}). Retrying in the background."})
        if must_speak and _live(o, epoch):               # the question / hint still gets asked, from the cartridge
            await emit(o, fallback_line(o, reason, rung), "lead", "HINT" if "hint" in reason.lower() else "PROBE", reason, must=True)
    finally:
        if epoch == state["epoch"]:         # after a reset, busy/next belong to the new session: leave them
            state["busy"] = False
            o.s["writer_busy"] = False
            await o.push_state()
            q = state.get("queue") or []
            nxt = q.pop(0) if q else state.pop("next", None)
            if nxt and nxt[3] == state["epoch"] and (nxt[4] or (
                    not (o.last_speech > nxt[2] and ("silence" in nxt[0] or nxt[0].startswith("end of turn")))
                    and state.get("shh_at", 0) <= nxt[2])):     # they spoke since: a fresh end-of-turn follows
                asyncio.create_task(turn(o, nxt[0], nxt[1], must_speak=nxt[4], t_asked=nxt[2]))


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


def _board_file(o=None):
    """model_walk (one-off CLI call) still reads the board from disk: the session's own last saved snapshot
    (what the student wrote), else the board page's latest."""
    png = None
    d = getattr(o, "report_dir", None) if o else None
    shots = sorted((d / "board").glob("*.png"), key=lambda f: f.stat().st_mtime) if d and (d / "board").exists() else []
    if shots:
        png = shots[-1].read_bytes()
    png = png or board.latest_png()
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    f = LIVE_DIR / "board.png"
    if png:
        f.write_bytes(png)
    elif f.exists():
        f.unlink()
    return bool(png)


async def model_walk(o):
    """After the session: full model solution, compared against what the student did."""
    has_board = _board_file(o)
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
