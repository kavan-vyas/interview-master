/* typewriter for live captions: ~45 chars/s, cancels the previous run on the same element */
window.typeOut = (el, text, done, dur) => {   // dur (s): pace to the voice
  clearInterval(el._tw); el.textContent = ""; let i = 0; text = String(text || "");
  const step = dur ? Math.max(1, text.length / (dur * 1000 / 45)) : 2;
  el._tw = setInterval(() => { i += step; el.textContent = text.slice(0, Math.floor(i)); if (i >= text.length) { clearInterval(el._tw); el.textContent = text; done?.(); } }, 45);
};
/* Simple flow: pick a problem → [Solo interview · start] or [1-to-1 interview setup]. Calm live screens. */
(() => {
  const $ = (s, r = document) => r.querySelector(s), B = document.body;
  const post = (p, b = {}) => fetch("/api/" + p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) })
    .then(r => r.ok ? r.json() : r.json().catch(() => ({})).then(j => Promise.reject(new Error(j.detail || r.status))));

  /* setup: steps 02–04 live behind the 1-to-1 button */
  document.querySelectorAll(".steps > .step").forEach((s, i) => { if (i) s.classList.add("adv"); });
  const go = $(".go");
  if (go) {
    const f = document.createElement("div"); f.className = "flow-go";
    f.innerHTML = `<button class="btn huge" id="soloGo" type="button">Solo interview · start <i>${ic("arrow-right")}</i></button>
      <button class="btn" id="muteGo" type="button" title="Somewhere you can't talk: write on the iPad, type if stuck">Mute interview · start</button>
      <button class="btn" id="irlGo" type="button">1-to-1 interview setup</button>
      <details class="flow-solo"><summary>Solo settings · iPad QR</summary></details>`;
    go.before(f);
    const mic = $(".mic-row"); if (mic) f.querySelector(".flow-solo").before(mic);   // devices: always reachable
    const box = $(".solo-box"); if (box) f.querySelector(".flow-solo").append(box);
    $("#irlGo").onclick = () => {
      const open = B.classList.toggle("irl");
      $("#irlGo").textContent = open ? "Hide 1-to-1 setup" : "1-to-1 interview setup";
      const so = $("#soloOn"); if (open && so?.checked) { so.checked = false; so.dispatchEvent(new Event("change")); }
      if (open) $(".step.adv")?.scrollIntoView({ behavior: "smooth", block: "start" });
    };
    const startSolo = async mute => {
      let main = $("#mainSel")?.value;
      if (!main) { const c = window.PICK?.next({}) || window.PICK?.next({ tiers: ["S", "A", "B", "C"], minParts: 1 }); if (!c) return toast("Pick a problem first", "warn"); window.PICK.set("main", c.id); main = c.id; }
      const so = $("#soloOn"); if (so && !so.checked) { so.checked = true; so.dispatchEvent(new Event("change")); }
      const po = $("#personalOn"); if (po) po.checked = false;
      window.__qrUntil = Date.now() + 60000;            // iPad QR shows on the live screen for the first 60 s
      window.__presetCfg = { limit_s: 2400, drill: null };
      B.classList.toggle("mute", mute);
      let spk = !mute; try { if (mute) spk = localStorage.getItem("im-mute-spk") === "1"; } catch { }
      post("spk", { on: spk }).then(() => window.syncSpk?.(spk)).catch(() => { });
      try { await post("setup", { solo: true, mute, limit_s: 2400, drill: null, warmup_id: null, main_id: main, personal_on: false }); }
      catch (e) { window.__presetCfg = null; return toast("Setup failed: " + e.message, "error"); }
      $("#startBtn").click();
      setTimeout(() => window.__presetCfg = null, 1500);
    };
    $("#soloGo").onclick = () => startSolo(false);
    $("#muteGo").onclick = () => startSolo(true);

    /* interrupted interviews (tab closed / app stopped mid-interview): resume, or write the report */
    const ib = document.createElement("div"); ib.className = "resume-bar"; ib.hidden = true; f.before(ib);
    const mm = t => `${String(t / 60 | 0).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`;
    const E = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    const loadInt = async () => {
      if (B.dataset.view !== "setup") return;
      let list = []; try { list = await (await fetch("/api/interrupted")).json(); } catch { }
      const r = Array.isArray(list) && list[0];
      ib.hidden = !r; if (!r) return;
      ib.innerHTML = `<div><b>Interrupted interview</b><span>${E(r.title)} · ${mm(r.elapsed)}${r.limit_s ? " of " + mm(r.limit_s) : ""} · part ${r.stage + 1}${r.stages ? " of " + r.stages : ""}${r.mute ? " · mute" : ""}</span></div>
        <div class="rb-acts"><button class="btn" type="button" data-a="resume">${ic("play")} Resume</button>
        <button class="btn ghost" type="button" data-a="report">Write report</button>
        <button class="btn ghost" type="button" data-a="dismiss" title="Hide (it stays in All reports)">${ic("x")}</button></div>`;
      window.renderMath?.(ib);
      ib.onclick = async e => {
        const a = e.target.closest("[data-a]")?.dataset.a; if (!a) return;
        ib.querySelectorAll("button").forEach(b => b.disabled = true);
        try {
          if (a === "resume") { const so = $("#soloOn"); if (r.solo && so && !so.checked) { so.checked = true; so.dispatchEvent(new Event("change")); } await post(`interrupted/${r.id}/resume`); }
          else if (a === "report") await post(`interrupted/${r.id}/report`);
          else { await post(`interrupted/${r.id}/dismiss`); loadInt(); }
        } catch (err) { toast(err.message, "error"); loadInt(); }
      };
    };
    loadInt();
    new MutationObserver(loadInt).observe(B, { attributes: true, attributeFilter: ["data-view"] });
  }

  /* solo: connect-the-iPad page, Continue -> interview begins */
  function connectStep() {
    return new Promise(done => {
      const v = document.createElement("div"); v.className = "qrstep";
      v.innerHTML = `<div class="qrcard"><h2>Connect your iPad</h2>
        <p>Scan with the iPad camera. The board opens in Safari, then tap the fullscreen button.</p>
        <p class="solo-help">On eduroam / uni Wi-Fi devices can't see each other: use the <b>Tailscale</b> code (iPad needs the Tailscale app, signed in to your account). Home Wi-Fi code works only at home.</p>
        <div class="solo-qr" id="qrStepBox"><p class="solo-help">Loading QR…</p></div>
        <div class="qr-acts"><button class="btn sm ghost" type="button" data-a="here">Open board on this Mac ${ic("arrow-up-right")}</button>
        <button class="btn sm ghost" type="button" data-a="back">Back</button>
        <button class="btn" type="button" data-a="go">Continue: start interview</button></div></div>`;
      document.body.append(v);
      window.qrInto?.(v.querySelector("#qrStepBox"));
      const end = ok => { v.remove(); removeEventListener("keydown", key); done(ok); };
      const key = e => { if (e.key === "Escape") end(false); if (e.key === "Enter") end(true); };
      addEventListener("keydown", key);
      v.onclick = e => { const a = e.target.closest("[data-a]")?.dataset.a; if (e.target === v || a === "back") end(false);
        if (a === "here") window.open("/board", "im-board", "popup,width=1180,height=820"); if (a === "go") end(true); };
      v.querySelector('[data-a="go"]').focus();
    });
  }

  /* prep bar: one line, tap to expand */
  const prep = $(".prep");
  if (prep) { prep.classList.add("collapsed"); prep.querySelector(".prep-cd").onclick = () => prep.classList.toggle("collapsed"); }

  /* live: only errors + warnings pop up (time's up, STT down, writer failed) */
  const t0 = window.toast;
  if (t0) window.toast = (text, kind = "info") => (B.dataset.view === "live" && kind !== "error" && kind !== "warn") ? undefined : t0(text, kind);

  /* 1-to-1 live: side panels folded away until wanted */
  const nav = $("#live .actions");
  if (nav) {
    const b = document.createElement("button"); b.className = "act panels"; b.type = "button"; b.innerHTML = "<kbd>O</kbd>Panels";
    b.onclick = () => B.classList.toggle("side-open"); nav.append(b);
    addEventListener("keydown", e => { if (e.key.toLowerCase() === "o" && B.dataset.view === "live" && !e.target.matches("input,select,textarea") && !e.metaKey) b.click(); });
  }
})();

/* solo live HUD: my mic level + what whisper heard from me, under the professor's caption */
(() => {
  const B = document.body, h = document.createElement("div");
  h.className = "hud"; h.innerHTML = `<div class="hud-me"><span class="hud-lvl"><i></i></span><small>You</small><span class="hud-txt"></span></div>`;
  B.append(h);
  const bar = h.querySelector(".hud-lvl i"), txt = h.querySelector(".hud-txt");
  /* professor status: Listening / Thinking… / Speaking */
  const st = document.createElement("div"); st.className = "hud-st"; st.textContent = "Listening"; h.prepend(st);
  let speakingNow = false, offline = false;
  const showSt = () => { const t = speakingNow ? "Speaking" : offline ? "Professor offline" : (typeof S !== "undefined" && S?.writer_busy) ? "Thinking…" : (document.body.classList.contains("mute") ? "Watching board" : "Listening");
    if (st.textContent !== t) { st.textContent = t; st.dataset.s = t.replace("…", "").replace("Professor ", "").toLowerCase(); } };
  addEventListener("im-msg", e => { if (e.detail.type === "prof") { speakingNow = e.detail.s === "speaking"; if (e.detail.s !== "speaking") offline = e.detail.s === "offline"; showSt(); } });
  setInterval(showSt, 250);
  const v0 = window.vu;
  if (v0) window.vu = v => { v0(v); bar.style.width = Math.min(100, Math.sqrt(Math.max(0, v)) * 260) + "%"; bar.classList.toggle("hot", v > .02); };
  const a0 = window.addLine;
  /* live words while you speak (small whisper, ~3 updates/s); the final line replaces them instantly */
  let pseq = -1;
  addEventListener("im-msg", e => { const m = e.detail; if (m.type !== "partial" || m.seq < pseq) return;
    if (m.seq !== pseq) { pseq = m.seq; h.classList.remove("in"); void h.offsetWidth; h.classList.add("in"); }
    clearInterval(txt._tw); txt.textContent = m.text; txt.classList.add("partial"); });
  if (a0) window.addLine = seg => { a0(seg); if (seg && seg.speaker !== "I") {
    clearInterval(txt._tw); txt.classList.remove("partial");
    if (pseq >= 0) txt.textContent = seg.text; else window.typeOut(txt, seg.text);
    pseq = Math.max(pseq, 0); } };
})();
