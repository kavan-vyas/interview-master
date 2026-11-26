"""Claude calls. Live loop: two warm Agent-SDK sessions (Haiku gate, Sonnet writer) fed deltas.
One-off jobs (pre-brief, report, import): `claude -p`. Lean flags, JSON out, no project context."""
import asyncio
import json
import re
import time

import config as C

LEVELS = {
    1: "Hints early and concrete. Accept intuition. Extensions rare.",
    2: "Hints early and concrete. Accept intuition. Extensions rare.",
    3: "Balanced hints. Ask 'why?' on key steps. Extend when a stage is finished.",
    4: "Hints late and vague. Demand proof or justification. Generalisations and 'what if' twists.",
    5: "Hints late and vague. Demand proof or justification. Generalisations and 'what if' twists.",
}
RUNG_TEXT = {
    "rephrase": "Rung 1 - rephrase the question or goal in different words. No new information.",
    "smaller_case": "Rung 2 - point the student at a smaller or concrete case.",
    "key_idea": "Rung 3 - name the key idea or technique, but never the answer or the result.",
}
EVENTS = {"stage_done", "stuck", "error_suspected", "self_corrected", "off_track"}

stats = {"gate_calls": 0, "writer_calls": 0, "last_gate_s": 0.0, "last_writer_s": 0.0}


class AIError(Exception):
    pass


SYSTEM = "You are a component of a live maths-interview copilot. Follow the output format exactly."


async def claude(prompt, model, timeout, tools=None, cwd=None, max_turns=1, system=SYSTEM):
    # Lean flags: no MCP servers, no user/plugin hooks, no skills, no built-in tools, custom
    # system prompt -> ~1.5k input tokens per call instead of ~30k. (--bare needs an API key.)
    args = [C.CLAUDE_BIN, "-p", "--model", model, "--output-format", "json",
            "--max-turns", str(max_turns), "--strict-mcp-config", "--setting-sources", "project",
            "--disable-slash-commands", "--no-session-persistence", "--system-prompt", system,
            "--tools", tools or ""]
    if tools:
        args += ["--allowedTools", tools]
    proc = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, cwd=str(cwd or C.SANDBOX))
    try:
        out, err = await asyncio.wait_for(proc.communicate(prompt.encode()), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        proc.kill()
        raise
    try:
        data = json.loads(out.decode() or "{}")
    except json.JSONDecodeError:
        raise AIError((err or out).decode()[:300])
    if data.get("is_error"):
        raise AIError(str(data.get("result"))[:300])
    return data.get("result", "")


def extract_json(text):
    """First JSON object/array in text (handles ```json fences and chatter)."""
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "[{":
            try:
                return dec.raw_decode(text[i:])[0]
            except json.JSONDecodeError:
                continue
    raise AIError("no JSON in reply: " + text[:200])


def fmt_lines(segs):
    return "\n".join(f"{s['speaker']}: {s['text']}" for s in segs) or "(none)"


# ---------------------------------------------------------------- warm sessions
GATE_SYS = """You are the GATE of a live Cambridge maths mock-interview copilot. I send you the stage context once,
then only new transcript lines (I=interviewer, S=student, ?=unknown). Speech-to-text is imperfect; read meaning from context.
For each message reply with ONLY one line of JSON:
{"refresh":bool,"events":[subset of "stage_done","stuck","error_suspected","self_corrected","off_track"],"note":"<=12 words","fact":"new result the student has now established, or empty"}
Judge the latest student line. refresh=true only if fresh follow-up questions would materially help the interviewer now."""

WRITER_SYS = """You are the WRITER of a live, in-person Cambridge maths mock-interview copilot. The interviewer is strong at
maths: be concise, no hand-holding. Student assumed to know A-level Maths + Further Maths.
I send the problem/stage context once, then only new transcript lines plus the current request.
Each request: write 3 follow-ups the interviewer can say aloud, each one full sentence (max 2) addressed to the student.
Never reveal the solution. HINT cards follow the hint rung named in the request.
Reply ONLY with a JSON array:
[{"type":"PROBE|HINT|EXTEND","text":"plain sentence","latex":"key maths in LaTeX or empty","spoken":"how to read the latex aloud or empty","check":"for EXTEND only: a sympy expression that equals 0 or True iff the maths claim is correct, else empty"}]"""

try:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage
    HAVE_SDK = True
except ImportError:  # falls back to one-shot `claude -p`
    HAVE_SDK = False


class Warm:
    """One long-lived Claude Code process per model (Agent SDK, subscription login).
    Primed once per stage, then fed deltas. Reset per stage, on cancel/error, or after MAX_TURNS."""
    MAX_TURNS = 40

    def __init__(self, name, model, system):
        self.name, self.model, self.system = name, model, system
        self.client, self.lock = None, asyncio.Lock()
        self.prime, self.primed, self.last_id, self.turns = "", False, 0, 0
        self.usage = {"in": 0, "cache_read": 0, "out": 0}

    async def _connect(self):
        if self.client is None and HAVE_SDK:
            c = ClaudeSDKClient(ClaudeAgentOptions(
                model=self.model, system_prompt=self.system, tools=[], strict_mcp_config=True,
                setting_sources=["project"], cli_path=C.CLAUDE_BIN, cwd=str(C.SANDBOX), max_turns=1))
            await asyncio.wait_for(c.connect(), 30)
            self.client = c

    async def warm(self):
        try:
            async with self.lock:
                await self._connect()
        except Exception as e:
            print(f"[{self.name}] warm failed: {e}")

    async def reset(self, prime=""):
        c, self.client = self.client, None
        self.prime, self.primed, self.last_id, self.turns = prime, False, 0, 0
        if c:
            try:
                await asyncio.wait_for(c.disconnect(), 5)
            except Exception:
                pass

    async def ask(self, text, timeout):
        async with self.lock:
            msg = text if self.primed else f"{self.prime}\n\n{text}"
            if not HAVE_SDK:  # no memory: always send the full context
                return await claude(f"{self.prime}\n\n{text}", self.model, timeout, system=self.system)
            try:
                await self._connect()
                await self.client.query(msg)
                out = ""
                async def read():
                    nonlocal out
                    async for m in self.client.receive_response():
                        if isinstance(m, ResultMessage):
                            out = m.result or ""
                            u = m.usage or {}
                            self.usage["in"] += u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                            self.usage["cache_read"] += u.get("cache_read_input_tokens", 0)
                            self.usage["out"] += u.get("output_tokens", 0)
                            if m.is_error:
                                raise AIError(out[:200])
                await asyncio.wait_for(read(), timeout)
            except BaseException:  # cancel / timeout / error: session state unknown -> drop it
                await self.reset(self.prime)
                raise
            self.primed = True
            self.turns += 1
            if self.turns >= self.MAX_TURNS:
                await self.reset(self.prime)
            return out


GATE = Warm("gate", C.GATE_MODEL, GATE_SYS)
WRITER = Warm("writer", C.WRITER_MODEL, WRITER_SYS)


def _stage_block(cart, idx):
    if not cart:
        return ""
    st = cart["stages"][idx] if idx < len(cart.get("stages", [])) else {}
    return f"""Problem ({cart.get('title')}): {cart.get('statement')}
Current stage {idx+1}/{len(cart.get('stages', []))}: {st.get('goal')}
Expected: {st.get('expected')}
Solution (interviewer only, NEVER reveal): {st.get('solution')}
Common errors: {'; '.join(st.get('common_errors', [])) or '-'}
Agreed hint ladder: {st.get('hints') if isinstance(st.get('hints'), dict) else '-'}"""


async def new_stage(cart, idx, phase, personal=None):
    """Called on every stage/phase change: fresh sessions, primed with this stage only."""
    if phase == "personal":
        p = personal or {}
        ctx = (f"PERSONAL SECTION. Test whether the student understands what they claim, in words.\n"
               f"Personal statement (excerpt): {p.get('statement') or '(none)'}"[:2700] +
               f"\nGeneric backup questions: {'; '.join(p.get('bank', [])[:12])}\n"
               "Card types here: PROBE = dig into a claim; HINT = gentler rephrase; EXTEND = connect to wider maths.")
        gctx = "PERSONAL SECTION: student is discussing their personal statement. stage_done never applies."
    else:
        ctx = _stage_block(cart, idx)
        st = cart["stages"][idx] if cart and idx < len(cart.get("stages", [])) else {}
        gctx = (f"STAGE CONTEXT\nGoal: {st.get('goal','-')}\nExpected: {st.get('expected','-')}\n"
                f"Common errors: {'; '.join(st.get('common_errors', [])) or '-'}")
    await asyncio.gather(GATE.reset(gctx), WRITER.reset("STAGE CONTEXT\n" + ctx))
    asyncio.create_task(GATE.warm())
    asyncio.create_task(WRITER.warm())


def _delta(sess, transcript, limit=40):
    """New segments since this session last saw the transcript (first call: last ~90 s window)."""
    new = [s for s in transcript if s["id"] > sess.last_id][-limit:]
    if transcript:
        sess.last_id = transcript[-1]["id"]
    return fmt_lines(new) if new else "(no new lines)"


async def gate(transcript, established):
    t = time.time()
    msg = f"New lines:\n{_delta(GATE, transcript)}\nEstablished so far: {'; '.join(established[-6:]) or '-'}"
    raw = await GATE.ask(msg, C.GATE_TIMEOUT)
    stats["gate_calls"] += 1
    stats["last_gate_s"] = round(time.time() - t, 2)
    r = extract_json(raw)
    if not isinstance(r, dict):
        raise AIError("gate: not an object")
    r["events"] = [e for e in r.get("events", []) if e in EVENTS]
    return r


async def writer(*, level, reason, forced, rung, established, transcript, ext_ideas):
    want = (f"All 3 cards must be type {forced}." if forced else
            "Exactly one PROBE, one HINT, one EXTEND, in that order.")
    msg = f"""New lines:
{_delta(WRITER, transcript)}
REQUEST
Trigger: {reason}
Difficulty {level}/5: {LEVELS[level]}
Hint rung: {RUNG_TEXT.get(rung, RUNG_TEXT['rephrase'])}
Extension ideas for this level: {'; '.join(map(str, ext_ideas)) or '-'}
Established by student: {'; '.join(established[-8:]) or '-'}
{want}"""
    t = time.time()
    raw = await WRITER.ask(msg, C.WRITER_TIMEOUT)
    stats["writer_calls"] += 1
    stats["last_writer_s"] = round(time.time() - t, 2)
    stats["tokens"] = {"gate": GATE.usage, "writer": WRITER.usage}
    cards = extract_json(raw)
    if isinstance(cards, dict):
        cards = cards.get("cards", [cards])
    out = []
    for c in cards[:3]:
        if isinstance(c, dict) and c.get("text"):
            out.append({k: str(c.get(k) or "") for k in ("type", "text", "latex", "spoken", "check")})
    if not out:
        raise AIError("writer: no cards")
    return out


async def chat(system, messages, model=None, timeout=90):
    convo = "\n\n".join(f"{m['role'].upper()}: {m['text']}" for m in messages[-20:])
    return await claude(f"{system}\n\n{convo}\n\nASSISTANT:", model or C.WRITER_MODEL, timeout)


PERSONAL_SECTION = ("## Personal section\n- Claims the student could not explain\n"
                    "- Topics they spoke about most convincingly")


async def report_narrative(summary_md, transcript_md, personal):
    psec = PERSONAL_SECTION if personal else ""
    pexc = "Personal statement excerpt: " + personal[:2000] if personal else ""
    prompt = f"""You are writing the feedback section of a Cambridge maths mock-interview report for the interviewer and student.
Session stats:
{summary_md}

Transcript:
{transcript_md[-14000:]}
{pexc}

Write markdown with these sections, concise and specific, quoting the transcript with [mm:ss] where useful:
## Top 3 to drill
(numbered; each = one concrete habit + the moment in this session that shows it)
## What went well
## Missed moments
(only real misses: a point where the student HAD the chance and didn't take it. Quote it.)
## Rigour and communication
{psec}
Rules:
- Never criticise something the session gave no opportunity for (e.g. no extensions if time ran out or the problem didn't reach one, no "didn't try small cases" if they never got stuck, no hint uptake if no hints).
- Don't list "0 times" skills or what wasn't covered. If there is nothing real to say in a section, write one line or skip it.
- The transcript is speech-to-text: ignore garbled words, mis-heard numbers and fragments; never criticise those.
- Half-sentences while working are thinking aloud, not wrong answers.
- Fillers (um, uh, like, you know) are counted in the checklist: mention them only if frequent, with one example.
- Struggle that led somewhere is a strength, not a weakness.
- Honest, specific, short. No preamble, no generic advice."""
    return await claude(prompt, C.WRITER_MODEL, 180)
