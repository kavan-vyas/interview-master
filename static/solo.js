/* Solo mode UI: setup toggle + board launcher, live board mirror + Shh, report replay + model walk, progress. */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
  const post = (p, b = {}) => fetch("/api/" + p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) }).then(r => r.ok ? r.json() : r.json().then(j => Promise.reject(new Error(j.detail || r.status))));
  const get = p => fetch("/api/" + p).then(r => r.json());
  const openBoard = () => window.open("/board", "im-board", "popup,width=1180,height=820");

  /* QR for the iPad's own Safari: home Wi-Fi + Tailscale (eduroam) */
  async function qr(box) {
    let j; try { j = await get("remote"); } catch { box.innerHTML = ""; return; }
    const tab = w => `<figure><img src="/api/remote/qr?which=${w}&t=${Date.now()}" alt=""><figcaption>${w === "lan" ? "Home Wi-Fi" : "Tailscale (any network)"}</figcaption></figure>`;
    box.innerHTML = (j.lan ? tab("lan") : "") + (j.ts ? tab("ts") : "") +
      `<p class="solo-help">Scan with the iPad camera to open the board in Safari. Then tap the fullscreen button, or Share, Add to Home Screen.</p>`;
  }
  window.qrInto = qr;
  /* ---------- setup: solo card in Step 04 ---------- */
  const sess = [...document.querySelectorAll(".step")].find(s => /Session/.test(s.querySelector("h2")?.textContent || ""));
  if (sess) {
    const box = el("div", "solo-box", `
      <label class="toggle"><input type="checkbox" id="soloOn"><span></span>Solo: Claude is the interviewer</label>
      <label class="toggle"><input type="checkbox" id="bargeOn" checked><span></span>Headphones: talk over the professor to interrupt</label>
      <div class="solo-row"><button class="btn sm" id="soloBoard" type="button">Open iPad board ${ic("arrow-up-right")}</button><select id="soloVoice" title="Professor voice (Kokoro, local)">
        <option value="bm_george">George (fast, light)</option><option value="bm_fable">Fable (UK)</option><option value="bm_lewis">Lewis (UK)</option>
        <option value="bm_daniel">Daniel (UK)</option><option value="bf_emma">Emma (UK)</option><option value="bf_isabella">Isabella (UK)</option></select>
      <label class="solo-speed">Speed <input type="range" id="soloSpeed" min="0.9" max="1.35" step="0.05" value="1.12"><b id="soloSpeedV">1.12×</b></label>
      <button class="btn sm ghost" id="soloTest" type="button">Test voice</button></div>
      <div class="solo-row"><label class="toggle"><input type="checkbox" id="sidecarOn"><span></span>Sidecar (cable) instead of iPad Safari</label></div>
      <div id="qrBox" class="solo-qr"></div>
      <p class="solo-help" id="scHelp" hidden>Control Centre, Screen Mirroring, iPad. Drag the board window onto the iPad, then press the fullscreen button.</p>`);
    sess.append(box);
    const on = $("#soloOn");
    try { on.checked = localStorage.getItem("im-solo") === "1"; } catch { }
    const sync = () => { document.body.classList.toggle("solo", on.checked); try { localStorage.setItem("im-solo", on.checked ? "1" : "0"); } catch { } window.pushSetup?.(); };
    on.onchange = sync; sync();
    const bg = $("#bargeOn");
    try { bg.checked = localStorage.getItem("im-barge") !== "0"; } catch { }
    const syncB = () => { try { localStorage.setItem("im-barge", bg.checked ? "1" : "0"); } catch { } post("setup", { barge: bg.checked }).catch(() => { }); };
    bg.onchange = syncB; syncB();
    $("#soloBoard").onclick = openBoard;
    const sc = $("#sidecarOn");
    try { sc.checked = localStorage.getItem("im-sidecar") === "1"; } catch { }
    const syncSc = () => { try { localStorage.setItem("im-sidecar", sc.checked ? "1" : "0"); } catch { }
      $("#scHelp").hidden = !sc.checked; $("#qrBox").hidden = sc.checked; $("#soloBoard").innerHTML = (sc.checked ? "Open board (Sidecar) " : "Open board on Mac ") + ic("arrow-up-right"); };
    sc.onchange = syncSc; syncSc(); qr($("#qrBox"));
    const V = $("#soloVoice");
    try { if (localStorage.getItem("im-voice-v2") !== "1") { localStorage.setItem("im-voice", "bm_george"); localStorage.setItem("im-voice-v2", "1"); } } catch { }
    try { V.value = localStorage.getItem("im-voice") || "bm_george"; } catch { }
    if (!V.value) V.value = "bm_george";
    const SP = $("#soloSpeed"), SPV = $("#soloSpeedV");
    try { SP.value = localStorage.getItem("im-speed") || "1.12"; } catch { }
    const setV = () => { try { localStorage.setItem("im-voice", V.value); localStorage.setItem("im-speed", SP.value); } catch { }
      SPV.textContent = (+SP.value).toFixed(2) + "×"; post("voice", { voice: V.value, speed: +SP.value }); };
    SP.oninput = () => { SPV.textContent = (+SP.value).toFixed(2) + "×"; };
    SP.onchange = setV;
    V.onchange = () => { setV(); $("#soloTest").click(); }; setV();
    $("#soloTest").onclick = () => post("say", { voice: V.value, text: "Right. Suppose $f(x) = x^2 - 2x$. Where is $f$ less than zero, and how do you know?" });
  }

  /* ---------- mic picker (Step 03) ---------- */
  const voices = document.querySelector(".enrol")?.closest(".step");
  if (voices) {
    const row = el("div", "mic-row", `<label>Mic <select id="micSel"><option>loading…</option></select></label>
      <label>Speaker <select id="spkSel"><option>loading…</option></select></label><button class="btn sm ghost" id="devRef" type="button" title="Re-scan devices">${ic("refresh-cw")}</button>`);
    voices.querySelector("header").after(row);
    const sel = $("#micSel"), spk = $("#spkSel"), o = (l, c) => l.map(d => `<option${d === c ? " selected" : ""}>${d.replace(/</g, "&lt;")}</option>`).join("");
    const fill = j => { sel.innerHTML = j.devices.length ? o(j.devices, j.current) : `<option>audio ${j.audio}</option>`; if (j.speakers) spk.innerHTML = o(j.speakers.devices, j.speakers.current); };
    const load = async () => { try { fill({ ...(await get("mic")), speakers: await get("speaker") }); } catch { } };
    sel.onchange = () => post("mic", { name: sel.value }).then(() => toast?.("Mic: " + sel.value, "good")).catch(e => toast?.(e.message, "error"));
    spk.onchange = () => post("speaker", { name: spk.value }).then(() => toast?.("Speaker: " + spk.value, "good")).catch(e => toast?.(e.message, "error"));
    $("#devRef").onclick = () => post("mic/refresh").then(fill).catch(e => toast?.(e.message, "error"));
    sel.onfocus = spk.onfocus = load; load(); setTimeout(load, 8000);
  }

  /* ---------- live: board mirror + controls ---------- */
  const side = $("#live .side");
  if (side) {
    const p = el("div", "panel solo-panel", `<h3>Board <small id="soloAge"></small></h3>
      <a id="soloImgA" target="_blank"><img id="soloImg" alt="" hidden></a><p class="empty" id="soloNo">No board yet: open it on the iPad.</p>
      <div class="solo-row"><button class="btn sm" id="soloBoard2" type="button">Board ${ic("arrow-up-right")}</button><button class="btn sm ghost" id="soloShh" type="button" title="Stop talking (S)">Shh</button><button class="btn sm ghost" id="soloQr" type="button">iPad QR</button></div><div id="qrLive" class="solo-qr" hidden></div>`);
    side.prepend(p);
    $("#soloBoard2").onclick = openBoard;
    $("#soloQr").onclick = () => { const b = $("#qrLive"); b.hidden = !b.hidden; if (!b.hidden) qr(b); };
    $("#soloShh").onclick = () => post("shh");
    addEventListener("keydown", e => { if (e.key === "s" && document.body.dataset.view === "live" && !e.target.matches("input,select,textarea") && !e.metaKey) post("shh"); });
    let last = null;
    setInterval(async () => {
      if (document.hidden || !["live", "setup"].includes(document.body.dataset.view)) return;
      try {
        const s = await get("board/state");
        if (s.url && s.url !== last) { last = s.url; $("#soloImg").src = s.url; $("#soloImgA").href = s.url; $("#soloImg").hidden = false; $("#soloNo").hidden = true; }
        $("#soloAge").textContent = s.age != null ? `#${s.n} · ${Math.round(s.age)}s ago` : "";
      } catch { }
    }, 2500);
  }

  /* ---------- report: replay + model walk ---------- */
  const rep = $("#report .repwrap");
  if (rep) {
    const bar = el("div", "row solo-rep", `<button class="btn" id="walkBtn" type="button">Model solution walk</button><button class="btn" id="replayBtn" type="button">Board replay</button>`);
    rep.querySelector(".row").before(bar);
    const out = el("div", "solo-out"); bar.after(out);
    $("#walkBtn").onclick = async e => {
      e.currentTarget.disabled = true; out.innerHTML = `<article class="md"><div class="loader"><i></i><i></i><i></i></div></article>`;
      try { const j = await post("solution_walk"); out.innerHTML = `<article class="md">${window.md ? md(j.md) : j.md}</article>`; window.renderMath?.(out); }
      catch (err) { out.innerHTML = `<p class="empty">Walk failed: ${esc(err.message)}</p>`; }
      e.currentTarget.disabled = false;
    };
    $("#replayBtn").onclick = async () => {
      const r = await get("replay");
      if (!r.board.length && !r.transcript.length) { out.innerHTML = `<p class="empty">Nothing to replay.</p>`; return; }
      if (window.__rpTimer) { clearInterval(window.__rpTimer); window.__rpTimer = null; }
      const t0 = r.t0 || (r.board.at(-1)?.t ?? 0), segs = r.transcript.map(s => ({ ...s, at: s.wall ?? t0 + s.t }));   // wall clock: pauses don't shift lines
      const tEnd = segs.length ? segs.at(-1).at + 5 : Date.now() / 1000;
      const shots = r.board.filter(b => b.t >= t0 - 60 && b.t <= tEnd + 5);     // this session only
      if (!shots.length && !segs.length) { out.innerHTML = `<p class="empty">Nothing to replay.</p>`; return; }
      const lo = Math.min(...[...segs.map(s => s.at), ...shots.map(b => b.t)]), hi = Math.max(...[...segs.map(s => s.at), ...shots.map(b => b.t)]);
      out.innerHTML = `<div class="replay"><div class="rp-img"><img id="rpImg" alt=""></div><div class="rp-lines" id="rpLines"></div>
        <div class="rp-ctl"><button class="btn sm" id="rpPlay" type="button">${ic("play")}</button><input type="range" id="rpT" min="${lo}" max="${hi}" step=".5" value="${lo}"><span id="rpClock"></span></div></div>`;
      const T = $("#rpT"), mm = x => { x = Math.max(0, Math.round(x - lo)); return `${String(x / 60 | 0).padStart(2, "0")}:${String(x % 60).padStart(2, "0")}`; };
      const show = () => {
        const t = +T.value, b = shots.filter(x => x.t <= t).at(-1) || shots[0];
        if (b) $("#rpImg").src = `/api/board/img/${r.board_sid}/${b.file}`;
        const vis = segs.filter(s => s.at <= t).slice(-14);
        $("#rpLines").innerHTML = vis.map(s => `<p class="${s.speaker}"><b>${s.speaker === "I" ? "Prof" : "You"}</b> ${s.text.replace(/</g, "&lt;")}</p>`).join("");
        $("#rpLines").scrollTop = 1e6; $("#rpClock").textContent = mm(t) + " / " + mm(hi);
      };
      T.oninput = show; show();
      $("#rpPlay").onclick = e => {
        if (window.__rpTimer) { clearInterval(window.__rpTimer); window.__rpTimer = null; e.currentTarget.innerHTML = ic("play"); return; }
        e.currentTarget.innerHTML = ic("pause");
        window.__rpTimer = setInterval(() => { if (!document.contains(T)) { clearInterval(window.__rpTimer); window.__rpTimer = null; return; }
          T.value = Math.min(hi, +T.value + 2); show(); if (+T.value >= hi) $("#rpPlay").click(); }, 250); // 8× speed
      };
    };
  }

  /* ---------- progress: feeds picker "Weak spots" + small strip in step 01 ---------- */
  const loadProg = async () => {
    try {
      const p = await get("progress");
      window.PROG = { topics: p.topics, done: new Set(p.done || p.rows.map(r => r.cart)) };
      window.drawPicker?.();
      const head = document.querySelector(".step.full header");
      let strip = $(".solo-prog");
      if (!strip && head) { strip = el("div", "solo-prog"); head.after(strip); }
      const weak = Object.entries(p.topics).sort((a, b) => a[1].avg - b[1].avg).slice(0, 5);
      if (strip) strip.innerHTML = p.rows.length
        ? `<small>Progress</small> ${p.rows.length} sessions · last ${p.rows.at(-1).score}/10 · weakest: ${weak.map(([t, v]) => `<i>${t} ${v.avg}</i>`).join(" ") || "-"}`
        : `<small>Progress</small> no sessions yet. Scores per topic appear here and feed the “Weak spots” sort.`;
    } catch { }
  };
  window.loadProg = loadProg; loadProg();
  new MutationObserver(() => { if (document.body.dataset.view === "setup") loadProg(); }).observe(document.body, { attributes: true, attributeFilter: ["data-view"] });
})();

/* ===== Churchill prep: presets, countdown plan, live clock, pause, captions ===== */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const post = (p, b = {}) => fetch("/api/" + p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) })
    .then(r => r.ok ? r.json() : r.json().catch(() => ({})).then(j => Promise.reject(new Error(j.detail || r.status))));
  const REH = "2026-12-05";
  const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
  const days = () => { const [y, m, d] = today().split("-").map(Number); return Math.round((Date.UTC(2026, 11, 7) - Date.UTC(y, m - 1, d)) / 864e5); };   // local calendar days
  const plan = () => {
    const d = days(), t = today();
    if (d < 0) return "Interview done. Good luck with the result.";
    if (d === 0) return "Interview day. Light 5 min talk drill only, then rest. Arrive Moller Centre by 16:45 with ID.";
    if (d <= 1) return "Rest day. One 5 min talk drill max. Sleep early.";
    if (t === REH) return "Dress rehearsal: full 40 min college interview mock at 17:15, best unseen problem, no pausing.";
    if (new Date().getDay() === 0) return "Rest day (Sunday). Optional 5 min talk drill.";
    return `Today: 5 min talk drill, then 40 min college interview mock at 17:15, read the report, then 10 min hint drill on the weakest skill.`;
  };

  /* setup: preset bar above Begin */
  const go = $(".go");
  if (go) {
    const bar = document.createElement("div"); bar.className = "prep";
    bar.innerHTML = `<div class="prep-cd"><b>${Math.max(0, days())}</b><small>days to College interview · 7 Dec · 17:15 ${ic("chevron-down","cd-chev")}</small></div>
      <p class="prep-plan">${plan()}</p>
      <div class="prep-btns"><button class="btn" id="pcMock" type="button">${ic("play")} Mock · 40 min</button>
      <button class="btn ghost" id="pcTalk" type="button">Talk drill · 5 min</button><button class="btn ghost" id="pcHint" type="button">Hint drill · 10 min</button></div>`;
    go.before(bar);
    const run = async (limit, drill, best) => {
      const c = window.PICK?.next({ best: best || today() === REH }) || window.PICK?.next({ tiers: ["S", "A", "B", "C"], minParts: 1 });
      if (!c) return toast("No unseen problem left in tiers S–C", "warn");
      window.PICK.set("main", c.id); window.PICK.set("warm", "");
      const so = $("#soloOn"); if (so && !so.checked) { so.checked = true; so.dispatchEvent(new Event("change")); }
      const po = $("#personalOn"); if (po) po.checked = false;
      post("spk", { on: true }).then(() => window.syncSpk?.(true)).catch(() => { });   // spoken preset: speaker on
      window.__presetCfg = { limit_s: limit, drill };
      try { await post("setup", { solo: true, limit_s: limit, drill, warmup_id: null, main_id: c.id, personal_on: false }); }
      catch (e) { window.__presetCfg = null; return toast("Setup failed: " + e.message, "error"); }
      $("#startBtn").click();
      setTimeout(() => window.__presetCfg = null, 1500);
    };
    $("#pcMock").onclick = () => run(2400, null);
    $("#pcTalk").onclick = () => run(300, "talk");
    $("#pcHint").onclick = () => run(600, "hint");
  }

  /* live: remaining clock, pause (discouraged), repeat, captions */
  const nav = $("#live .actions");
  if (nav) {
    const mk = (cls, html, fn) => { const b = document.createElement("button"); b.className = "act " + cls; b.innerHTML = html; b.onclick = fn; return b; };
    nav.append(mk("rep", "<kbd>⟲</kbd>Repeat", () => post("repeat")));
    nav.append(mk("pz", "<kbd>P</kbd>Pause", () => {
      if (S.paused) return post("pause", { on: false });
      if (confirm("There is no pause on 7 December.\n\nPause anyway? It's logged in your report and the clock stops.")) post("pause", { on: true });
    }));
  }
  const cap = document.createElement("div"); cap.className = "caption"; cap.hidden = true; document.body.append(cap);
  const veil = document.createElement("div"); veil.className = "pzveil"; veil.hidden = true;
  veil.innerHTML = `<div><b>Paused</b><p>On the day you'd keep talking through it.</p><button class="btn huge" type="button">Resume</button></div>`;
  veil.querySelector("button").onclick = () => post("pause", { on: false });
  document.body.append(veil);
  let capT;
  addEventListener("im-msg", e => {
    const m = e.detail;
    if (m.type === "hello" && document.body.classList.contains("solo") && ["personal", "warmup", "main"].includes(m.state?.phase)) {
      const last = (m.transcript || []).filter(x => x.speaker === "I").at(-1);    // reload: his last question stays visible
      if (last && cap.hidden) {
        cap.innerHTML = `<small>${last.who === "second" ? "Second interviewer" : "Interviewer"}</small>`;
        const t = document.createElement("span"); t.textContent = last.text; cap.append(t); window.renderMath?.(cap);
        cap.hidden = false;
      }
    }
    if (m.type === "caption" && document.body.classList.contains("solo")) {
      cap.innerHTML = `<small>${m.who === "second" ? "Second interviewer" : "Interviewer"}</small>`;
      const t = document.createElement("span"); cap.append(t); window.typeOut(t, m.text, () => window.renderMath?.(cap), m.dur);
      cap.hidden = false; cap.classList.remove("in"); void cap.offsetWidth; cap.classList.add("in");
      clearTimeout(capT); capT = setTimeout(() => { if (!document.body.classList.contains("solo")) cap.hidden = true; }, 9000);
    }
    if (m.type === "timeup") toast("Time's up. Writing your report…", "warn");
  });
  const clock = $("#elapsed");
  const sclock = document.createElement("div"); sclock.className = "solo-clock"; sclock.hidden = true; document.body.append(sclock);   // solo: time left, small
  setInterval(() => {   // keep the deck in step with the server's main problem (e.g. a pinned test problem)
    if (typeof S !== "undefined" && S.main_id && S.phase === "setup" && $("#mainSel") && $("#mainSel").value !== S.main_id && window.PICK)
      window.PICK.set("main", S.main_id);
  }, 1500);
  setInterval(() => {
    if (typeof S === "undefined" || !S) return;
    veil.hidden = !(S.paused && document.body.dataset.view === "live");
    if (document.body.dataset.view !== "live" || S.phase === "setup") cap.hidden = true;   // no stale caption off the live screen
    if (S.limit_s && document.body.dataset.view === "live" && clock && S.phase !== "wrapup") {
      const left = Math.max(0, S.limit_s - (S.elapsed || 0));
      clock.textContent = `${String(left / 60 | 0).padStart(2, "0")}:${String(left % 60).padStart(2, "0")} left`;
      clock.classList.toggle("low", left <= 300);
    }
    const showS = !!(S.limit_s && document.body.classList.contains("solo") && document.body.dataset.view === "live" && S.phase !== "setup");
    sclock.hidden = !showS;
    if (showS) { const left = Math.max(0, S.limit_s - (S.elapsed || 0));
      sclock.textContent = S.phase === "wrapup" ? "Time's up" : `${String(left / 60 | 0).padStart(2, "0")}:${String(left % 60).padStart(2, "0")} left`;
      sclock.classList.toggle("low", left <= 300); }
  }, 500);
  addEventListener("keydown", e => { if (e.key.toLowerCase() === "p" && document.body.dataset.view === "live" && !e.target.matches("input,select,textarea") && !e.metaKey) nav?.querySelector(".pz")?.click(); });
})();

/* live bar (every solo interview): speaker on/off + type to the interviewer */
(() => {
  const post = (p, b = {}) => fetch("/api/" + p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });
  const bar = document.createElement("form"); bar.className = "livebar"; bar.hidden = true;
  bar.innerHTML = `<button type="button" class="lb-spk" title="Speaker on/off (V)">${ic("volume-2")}</button>
    <button type="button" class="lb-key" title="Type to the interviewer (/)">${ic("keyboard")}</button>
    <input type="text" placeholder="Type to the interviewer…" maxlength="800" autocomplete="off">
    <button type="submit" class="lb-send">${ic("send")}</button>`;
  document.body.append(bar);
  const spk = bar.querySelector(".lb-spk"), inp = bar.querySelector("input");
  const open = v => { bar.classList.toggle("open", v); if (v) setTimeout(() => inp.focus(), 30); else inp.blur(); };
  bar.querySelector(".lb-key").onclick = () => open(!bar.classList.contains("open"));
  let on = true;
  window.syncSpk = v => { on = v; spk.innerHTML = ic(v ? "volume-2" : "volume-x"); spk.classList.toggle("off", !v); };
  spk.onclick = () => { const v = !on; window.syncSpk(v); post("spk", { on: v });
    try { if (document.body.classList.contains("mute")) localStorage.setItem("im-mute-spk", v ? "1" : "0"); } catch { } };
  bar.onsubmit = e => { e.preventDefault(); const t = inp.value.trim(); if (!t) return;
    post("inject", { text: t, typed: true }); inp.value = ""; open(false); };
  addEventListener("keydown", e => {
    if (bar.hidden || e.metaKey || e.ctrlKey || e.target.matches("input,textarea,select")) return;
    if (e.key === "/" && !bar.classList.contains("paused")) { e.preventDefault(); open(true); }
    if (e.key.toLowerCase() === "v") spk.click();
  });
  inp.addEventListener("keydown", e => { if (e.key === "Escape") open(false); e.stopPropagation(); });
  const qrc = document.createElement("div"); qrc.className = "liveqr"; qrc.hidden = true;
  qrc.innerHTML = `<small>Board: scan with iPad</small><div class="solo-qr"></div><button type="button" class="lb-x">Open on this Mac ${ic("arrow-up-right")}</button>`;
  document.body.append(qrc);
  qrc.querySelector(".lb-x").onclick = () => { window.open("/board", "im-board", "popup,width=1180,height=820"); window.__qrUntil = 0; };
  let qrLoaded = false;
  setInterval(() => {
    const live0 = document.body.classList.contains("solo") && document.body.dataset.view === "live" && typeof S !== "undefined" && S && S.phase !== "setup";
    const want = live0 && ((Date.now() < (window.__qrUntil || 0) && !(S.board_age < 6)) || Date.now() < (window.__qrForce || 0));   // board polled in the last 6 s = connected
    if (want && !qrLoaded) { window.qrInto?.(qrc.querySelector(".solo-qr")); qrLoaded = true; }
    if (!want) qrLoaded = false;
    qrc.hidden = !want;
  }, 700);
  setInterval(() => {
    const live = document.body.dataset.view === "live" && typeof S !== "undefined" && S && S.phase !== "setup";
    bar.hidden = !live;                    // every interview (solo, mute, 1-to-1)
    const pz = !!(typeof S !== "undefined" && S && S.paused);   // paused: no typing (it would not be recorded)
    bar.classList.toggle("paused", pz); if (pz && bar.classList.contains("open")) open(false);
    if (typeof S !== "undefined" && S) { document.body.classList.toggle("mute", !!S.mute);
      if ("speaker_on" in S && on !== S.speaker_on) window.syncSpk(S.speaker_on); }
  }, 500);
})();
