/* Interview Master dashboard. Vanilla JS, one WebSocket, no build step. */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const RM0 = matchMedia("(prefers-reduced-motion: reduce)").matches;
const calmNow = () => RM0 || document.body.dataset.motion === "calm" ||
  (document.body.classList.contains("solo") && document.body.dataset.view === "live");   // solo interview: no GPU-heavy background
let S = {}, CARDS = [], CARTS = [], CHAT = [], wsock, REP_BUSY = false;   // REP_BUSY: report still being written

/* ---------------- kinetic type helpers ---------------- */
function letters(el, text = el.textContent) {
  el.textContent = "";
  [...text].forEach((c, i) => {
    const s = document.createElement("span");
    s.className = "ch"; s.style.setProperty("--i", i);
    s.textContent = c === " " ? " " : c;
    el.appendChild(s);
  });
  return el;
}
/* ---------------- maths: $..$, $$..$$, \(..\), \[..\] rendered with KaTeX everywhere ---------------- */
const MATH_RE = /(\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\$[^$\n]+?\$)/g;
function unwrap(m) {
  if (m.startsWith("$$") || m.startsWith("\\[")) return [m.slice(2, -2), true];
  if (m.startsWith("\\(")) return [m.slice(2, -2), false];
  if (m.startsWith("$")) return [m.slice(1, -1), false];
  return [m, false];
}
function km(src, display) {
  const sp = document.createElement("span"); sp.className = "km";
  if (window.katex) { try { katex.render(src, sp, { throwOnError: false, displayMode: display, strict: "ignore" }); return sp; } catch { } }
  sp.textContent = src; return sp;
}
const isMath = p => { if (!p) return false; MATH_RE.lastIndex = 0; const m = p.match(MATH_RE); MATH_RE.lastIndex = 0; return !!m && m[0] === p; };
/* render maths inside every text node under root (safe on any element) */
function renderMath(root) {
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, { acceptNode: n => n.parentElement.closest(".katex,.km,script,style,select,option,textarea") ? 2 : (/[$\\]/.test(n.nodeValue) ? 1 : 2) });
  const nodes = []; while (w.nextNode()) nodes.push(w.currentNode);
  nodes.forEach(n => {
    const parts = n.nodeValue.split(MATH_RE); if (parts.length < 2) return;
    const f = document.createDocumentFragment();
    parts.forEach(p => { if (!p) return; if (isMath(p)) f.append(km(...unwrap(p))); else f.append(p); });
    n.replaceWith(f);
  });
  return root;
}
/* plain text for <option> labels, which cannot hold markup */
const plainMath = s => String(s ?? "").replace(MATH_RE, m => unwrap(m)[0].replace(/\\(text|mathrm|mathbf)\{([^}]*)\}/g, "$2").replace(/\\[a-zA-Z]+/g, "").replace(/[{}]/g, ""));

function words(el, text = el.textContent, d0 = 0) {
  el.textContent = "";
  el.style.setProperty("--d0", d0 + "ms");
  let i = 0;
  String(text ?? "").split(MATH_RE).forEach(chunk => {
    if (!chunk) return;
    if (isMath(chunk)) {   // a formula animates in as one word
      const s = document.createElement("span"); s.className = "w wm"; s.style.setProperty("--i", i++ / 2);
      s.append(km(...unwrap(chunk))); el.appendChild(s); return;
    }
    chunk.split(/(\s+)/).forEach(w => {
      if (!w) return;
      if (/^\s+$/.test(w)) return el.append(" ");
      const s = document.createElement("span");
      s.className = "w"; s.style.setProperty("--i", i++ / 2);
      s.textContent = w; el.appendChild(s);
    });
  });
  return el;
}
$$("[data-letters],[data-split]").forEach(e => letters(e));
$$("[data-words]").forEach(e => words(e, e.textContent, 500));

function sweep(text) {
  if (calmNow()) return;
  const sw = $("#sweep"), sp = $("span", sw);
  sw.classList.remove("go"); void sw.offsetWidth;
  letters(sp, text);
  sw.classList.add("go");
  clearTimeout(sweep.t); sweep.t = setTimeout(() => sw.classList.remove("go"), 1700);
}

function toast(text, kind = "info") {
  const t = document.createElement("div");
  t.className = "toast " + kind; t.textContent = text; renderMath(t);
  $("#toasts").appendChild(t);
  setTimeout(() => { t.classList.add("bye"); setTimeout(() => t.remove(), 400); }, 4200);
}

function ripple(btn, e) {
  const r = btn.getBoundingClientRect(), d = Math.max(r.width, r.height);
  const s = document.createElement("span");
  s.className = "ripple";
  const x = e && e.clientX ? e.clientX - r.left : r.width / 2, y = e && e.clientY ? e.clientY - r.top : r.height / 2;
  Object.assign(s.style, { width: d + "px", height: d + "px", left: x - d / 2 + "px", top: y - d / 2 + "px" });
  btn.appendChild(s); setTimeout(() => s.remove(), 700);
}

function tex(src) {
  const el = document.createElement("div"); el.className = "tex";
  const m = String(src).trim(), [inner] = isMath(m) ? unwrap(m) : [m];
  el.append(km(inner, true));
  return el;
}
/* live header shows only the part being asked: shared setup line + current (i)/(ii)/... */
function partOnly(cart, k) {
  const st = cart.statement || "", bits = st.split(/\n\s*(?=\((?:i{1,3}|iv|v|vi{1,3}|ix|x|[a-h])\)\s)/);
  if (bits.length > 1) { const p = bits[Math.min(k + 1, bits.length - 1)]; return bits[0].trim() + "\n\n" + p.trim(); }
  const g = (cart.goals || [])[k];
  return (cart.goals || []).length > 1 && g ? g : st;
}

/* inline $...$ inside statements */
function mathText(el, s) { el.textContent = s || ""; renderMath(el); }

const mmss = t => { t = Math.max(0, Math.round(t || 0)); return String(t / 60 | 0).padStart(2, "0") + ":" + String(t % 60).padStart(2, "0"); };
const api = (path, body) => fetch("/api/" + path, { method: body === undefined ? "GET" : "POST", headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) })
  .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.detail || r.statusText); return j; });

/* ---------------- aurora background (reacts to mic) ---------------- */
const cv = $("#aurora"), cx = cv.getContext("2d");
let LVL = 0, lvlS = 0;
const blobs = [["182,255,59", .18], ["77,226,255", .12], ["181,140,255", .10], ["92,255,170", .10]].map(([c, a], i) => ({ c, a, s: Math.random() * 9, sp: .00006 + i * .00002 }));
let AW = 0, AH = 0, lastF = 0;
function sizeAurora() { AW = cv.width = Math.round(innerWidth * .25); AH = cv.height = Math.round(innerHeight * .25); }  // quarter-res, CSS upscale = free blur
sizeAurora(); addEventListener("resize", sizeAurora);
function frame(t) {
  requestAnimationFrame(frame);
  if (calmNow() || document.hidden || t - lastF < 33) return;   // ~30 fps is plenty for slow blobs
  lastF = t;
  const w = AW, h = AH;
  cx.globalCompositeOperation = "source-over";
  cx.fillStyle = "#040705"; cx.fillRect(0, 0, w, h);
  lvlS += (LVL - lvlS) * .06;   // slow follow: no flicker
  cx.globalCompositeOperation = "lighter";
  blobs.forEach((b, i) => {
    const k = t * b.sp + b.s;
    const x = w * (.5 + .38 * Math.sin(k * 1.3 + i)), y = h * (.45 + .35 * Math.cos(k * .9 + i * 2));
    const r = Math.max(w, h) * (.35 + .08 * Math.sin(k * 2) + lvlS * .5);
    const g = cx.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, `rgba(${b.c},${b.a * (S.phase === "setup" ? 1 : .55) + lvlS * .12})`); g.addColorStop(1, "rgba(0,0,0,0)");
    cx.fillStyle = g; cx.fillRect(0, 0, w, h);
  });
}
requestAnimationFrame(frame);
const SPOT = document.querySelector(".spot");
addEventListener("pointermove", e => {
  SPOT && (SPOT.style.transform = `translate3d(${e.clientX}px,${e.clientY}px,0)`);   // transform only: no page-wide restyle
  const c = e.target.closest && e.target.closest(".card");
  if (c) { const r = c.getBoundingClientRect(); c.style.setProperty("--cx", e.clientX - r.left + "px"); c.style.setProperty("--cy", e.clientY - r.top + "px"); }
});
/* magnetic start button */
const mag = $(".magnetic");
mag.addEventListener("pointermove", e => { const r = mag.getBoundingClientRect(); mag.style.setProperty("--tx", (e.clientX - r.left - r.width / 2) * .25 + "px"); mag.style.setProperty("--ty", (e.clientY - r.top - r.height / 2) * .35 + "px"); });
mag.addEventListener("pointerleave", () => { mag.style.setProperty("--tx", "0px"); mag.style.setProperty("--ty", "0px"); });

function vu(v) {
  LVL = Math.min(.25, v * 3);
  $$("#vu b").forEach((b, i) => b.style.transform = `scaleY(${Math.max(.1, Math.min(1, v * 14 * (1 - Math.abs(i - 3) * .18) + Math.random() * .08))})`);
}

/* ---------------- websocket ---------------- */
/* lifecycle: server stops when the last tab closes. A tab left open after that must NOT keep reconnecting,
   or it would keep the next launch alive forever: it shows "closed" and waits for a click. */
let wsLost = 0, wsBye = false;
function closedScreen() {
  if (document.getElementById("ciClosed")) return;
  const d = document.createElement("div"); d.id = "ciClosed";
  d.innerHTML = `<div><h2>College Interview is closed</h2><p>Open it again from the College Interview app.</p>
    <button class="btn" type="button">Reconnect</button></div>`;
  d.querySelector("button").onclick = () => location.reload();
  document.body.append(d);
}
function connect() {
  wsock = new WebSocket(`ws://${location.host}/ws`);
  wsock.onopen = () => { wsLost = 0; };
  wsock.onmessage = e => { const m = JSON.parse(e.data); if (m.type === "bye") { wsBye = true; return closedScreen(); } on(m); };
  wsock.onclose = () => {
    pill("#aiPill", "offline", "warn");
    if (wsBye) return closedScreen();
    wsLost = wsLost || Date.now();
    if (Date.now() - wsLost > 30000) return closedScreen();   // server gone for good
    setTimeout(connect, 1500);
  };
}
function on(m) {
  switch (m.type) {
    case "hello":
      CARTS = m.carts; fillCarts(); setState(m.state);
      $("#lines").textContent = ""; m.transcript.forEach(addLine);
      if (m.cards.length) renderCards(m.cards, false);
      break;
    case "state": setState(m.state); break;
    case "seg": addLine(m.seg); break;
    case "seg_update": { const el = $(`.ln[data-id="${m.seg.id}"]`); if (el) el.replaceWith(lineEl(m.seg)); break; }
    case "cards": renderCards(m.cards, true); break;
    case "phase": sweep(m.phase === "wrapup" ? "Wrap-up" : (m.title && m.title.startsWith("Stage") ? m.title : ({ personal: "Personal", warmup: "Warm-up", main: "Main problem" })[m.phase] || m.phase)); break;
    case "toast": toast(m.text, m.kind); break;
    case "level": if (m.v < 0) enrolProg(-m.v); else vu(m.v); break;
    case "enrolled": { const b = $(`.voice[data-role="${m.role}"]`); b.classList.remove("rec"); b.classList.add("done"); $("small", b).textContent = "enrolled"; toast(`${m.role === "I" ? "Interviewer" : "Student"} voice enrolled`, "good"); break; }
    case "report_busy": REP_BUSY = true; view("report"); break;
    case "report": REP_BUSY = false; showReport(m); break;
    default: dispatchEvent(new CustomEvent("im-msg", { detail: m }));
  }
}

function pill(sel, text, cls) { const p = $(sel); p.className = "pill " + (sel === "#aiPill" ? "ai " : "phase ") + (cls || ""); $("span", p).textContent = text; }

/* ---------------- state -> UI ---------------- */
function view(v) { if (document.body.dataset.view !== v) document.body.dataset.view = v; }

function setState(s) {
  const prev = S; S = s;
  if (s.phase === "setup") REP_BUSY = false;
  const live = ["personal", "warmup", "main", "wrapup"].includes(s.phase);
  view(s.phase === "setup" ? "setup" : s.phase === "done" ? "report" : "live");
  pill("#phasePill", s.phase + (live && s.phase !== "wrapup" && s.phase !== "personal" ? ` · ${s.stage + 1}` : ""), live ? "on" : "");
  const busy = s.writer_busy || s.gate_busy;
  pill("#aiPill", s.writer_busy ? "writing" : s.gate_busy ? "gating" : `AI idle · ${s.ai.gate_calls}g ${s.ai.writer_calls}w`, busy ? "on" : "");
  $("#cards").classList.toggle("busy", !!s.writer_busy);
  if (s.writer_busy && !CARDS.length) writingPlaceholder();
  if (!s.limit_s) $("#elapsed").textContent = mmss(s.elapsed);   // solo presets: solo.js shows time left
  $("#audioNote").textContent = s.audio === "live" ? "" : s.audio === "loading" ? "Loading speech models…" : "Mic pipeline off: run scripts/setup.sh, or use the typed input / --sim.";
  if (!live) return;

  // problem header
  const key = (s.cart.id || s.phase) + s.unverified + "|" + (s.stage ?? 0);
  if ($("#problem").dataset.k !== key) {
    $("#problem").dataset.k = key; $("#problem").innerHTML = "";
    const t = document.createElement("div"); t.className = "ttl";
    letters(t, s.phase === "personal" ? "Personal statement" : plainMath(s.cart.title || ""));
    $("#problem").append(t);
    if (s.unverified) { const b = document.createElement("span"); b.className = "badge no"; b.textContent = "unverified cartridge"; $("#problem").append(b); }
    if (s.cart.statement) { const st = document.createElement("div"); st.className = "stmt"; mathText(st, partOnly(s.cart, s.stage || 0)); $("#problem").append(st); }
  }
  // stages
  const ol = $("#stages"), sk = s.phase + s.stage + (s.cart.goals || []).length;
  if (ol.dataset.k !== sk) {
    ol.dataset.k = sk; ol.innerHTML = `<li class="ph">${s.phase}</li>`;
    (s.cart.goals || []).forEach((g, i) => { const li = document.createElement("li"); mathText(li, g); li.className = i < s.stage ? "done" : i === s.stage ? "now" : ""; ol.append(li); });
  }
  // silence gauge
  const sil = s.silence || 0, g = $("#silGauge");
  $(".val", g).style.strokeDashoffset = 326.7 * (1 - Math.min(sil, 90) / 90);
  g.className = "gauge " + (sil >= 90 ? "t3" : sil >= 45 ? "t2" : sil >= 20 ? "t1" : "");
  odo($("#silNum"), sil + "");
  // hint rung
  $$("#rung i").forEach((el, i) => el.classList.toggle("on", i < s.rung));
  // errors
  const list = $("#errList");
  list.innerHTML = s.pending_errors.length || s.flag ? "" : `<p class="empty">none pending</p>`;
  s.pending_errors.forEach(e => {
    const d = document.createElement("div"); d.className = "err";
    d.innerHTML = `<svg viewBox="0 0 38 38"><circle class="bg" cx="19" cy="19" r="16"/><circle class="fg" cx="19" cy="19" r="16" style="stroke-dashoffset:${100.5 * (1 - e.left / s.hold_s)}"/></svg><div><b>${e.left}s</b> ${esc(e.note)}</div>`;
    list.append(d);
  });
  if (s.flag) { const f = document.createElement("div"); f.className = "flag"; f.innerHTML = ic("flag") + " " + esc(s.flag); list.append(f); }
  const alarm = !!s.flag; if (alarm && !(prev.flag === s.flag)) { $("#errs").classList.remove("alarm"); void $("#errs").offsetWidth; } $("#errs").classList.toggle("alarm", alarm);
  // difficulty
  const sl = $("#slider"); if (document.activeElement !== sl) sl.value = s.slider; rangeFill(sl);
  $("#ghost").style.setProperty("--lv", s.level); $("#ghost").textContent = s.adapt ? `auto ${s.level}` : "auto";
  $("#ghost").style.opacity = s.adapt ? 1 : .35;
  $("#adaptNote").innerHTML = s.adapt ? `${ic(s.adapt > 0 ? "trending-up" : "trending-down")} ${esc(s.adapt_note)}` : "";
  $("#personalLive").checked = s.personal_on;
  $("#gateNote").textContent = s.last_note ? "“" + s.last_note + "”" : "";
  $(".act.move").classList.toggle("ready", !!s.stage_ready);
}
function odo(el, v) {
  if (el.dataset.v === v) return; el.dataset.v = v;
  el.innerHTML = [...v].map(d => `<span>${d}</span>`).join("");
}
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ---------------- cards ---------------- */
function writingPlaceholder() {
  const box = $("#cards"); box.className = "cards empty busy"; box.innerHTML = "";
  const w = document.createElement("div"); w.className = "writing"; words(w, "Reading the room…"); box.append(w);
}
function renderCards(cards, animate) {
  const box = $("#cards");
  const old = $$(".card", box);
  const draw = () => {
    CARDS = cards; box.className = "cards" + (S.writer_busy ? " busy" : ""); box.innerHTML = "";
    cards.forEach((c, i) => {
      const el = document.createElement("article");
      el.className = `card ${c.type || "PROBE"}${c.local ? " local" : ""}`; el.style.setProperty("--i", i);
      el.innerHTML = `<div class="lab"><span class="type">${esc(c.type)}</span><kbd>${i + 1}</kbd></div>`;
      const tx = document.createElement("div"); tx.className = "txt"; words(tx, c.text, 250 + i * 140); el.append(tx);
      if (c.latex) { el.append(tex(c.latex)); }
      if (c.spoken) { const sp = document.createElement("div"); sp.className = "spk"; sp.textContent = c.spoken; el.append(sp); }
      if (c.type === "EXTEND") { const v = document.createElement("div"); v.className = "ver" + (c.verified ? "" : " unk"); v.innerHTML = c.verified ? ic("check") + " sympy-checked" : "not machine-checkable"; el.append(v); }
      el.onclick = () => el.classList.toggle("used");
      box.append(el);
    });
  };
  if (animate && old.length && !calmNow()) { old.forEach(o => o.classList.add("out")); setTimeout(draw, 480); } else draw();
}

/* ---------------- transcript ---------------- */
const CYCLE = { I: "S", S: "?", "?": "I" };
function lineEl(s) {
  const d = document.createElement("div");
  d.className = `ln ${s.speaker}${s.conf < .55 ? " low" : ""}`; d.dataset.id = s.id;
  d.innerHTML = `<time>${mmss(s.t)}</time><span class="who" title="click to change speaker">${esc(s.speaker)}</span><span class="tx"></span>`;
  words($(".tx", d), s.text);
  $(".who", d).onclick = () => api(`seg/${s.id}/speaker`, { speaker: CYCLE[s.speaker] });
  return d;
}
function addLine(s) { const L = $("#lines"); L.append(lineEl(s)); L.scrollTop = L.scrollHeight; }

/* ---------------- setup ---------------- */
function fillCarts() {
  const opt = (c) => `<option value="${c.id}">${esc(plainMath(c.title))}${c.verified ? "" : " · unverified"}</option>`;
  $("#warmSel").innerHTML = `<option value="">none</option>` + CARTS.filter(c => c.warmup).map(opt).join("");
  $("#mainSel").innerHTML = CARTS.filter(c => !c.warmup).map(opt).join("") || `<option value="">no cartridges: run import_pdf.py</option>`;
  const pb = $("#pbSel").value;
  $("#pbSel").innerHTML = CARTS.map(opt).join("");
  if (pb) $("#pbSel").value = pb;
  if (S.warmup_id) $("#warmSel").value = S.warmup_id;
  if (S.main_id) $("#mainSel").value = S.main_id;
  cartInfo();
}
function cartInfo() {
  const show = id => { const c = CARTS.find(x => x.id === id); return c ? `<div><b>${esc(c.title)}</b> · ${esc(c.stages)} stages · ${(c.topics || []).map(esc).join(", ")} <span class="badge ${c.verified ? "yes" : "no"}">${c.verified ? "verified" : "unverified"}</span></div>` : ""; };
  $("#cartInfo").innerHTML = show($("#warmSel").value) + show($("#mainSel").value); renderMath($("#cartInfo"));
  pushSetup();
}
function pushSetup() {
  api("setup", { warmup_id: $("#warmSel").value || null, main_id: $("#mainSel").value || null, personal_on: $("#personalOn").checked, slider: +$("#slider0").value, hold_s: +$("#hold").value, solo: !!document.querySelector("#soloOn")?.checked, ...(window.__presetCfg || { limit_s: 0, drill: null }) }).catch(() => { });
}
["#warmSel", "#mainSel"].forEach(s => $(s).onchange = cartInfo);
["#personalOn"].forEach(s => $(s).onchange = pushSetup);
function rangeFill(r) { r.style.setProperty("--p", ((r.value - r.min) / (r.max - r.min)) * 100 + "%"); }
$$("input[type=range]").forEach(r => { rangeFill(r); r.addEventListener("input", () => rangeFill(r)); });
$("#hold").oninput = e => { $("#holdV").textContent = e.target.value + " s"; rangeFill(e.target); };
$("#hold").onchange = pushSetup;
$("#slider0").oninput = e => { $("#slV").textContent = e.target.value; rangeFill(e.target); };
$("#slider0").onchange = pushSetup;
$("#stmtFile").onchange = e => {
  const f = e.target.files[0]; if (!f) return;
  const r = new FileReader();
  r.onload = () => api("statement", { name: f.name, b64: r.result }).then(j => toast(`Statement loaded (${j.chars} chars)`, "good")).catch(err => toast(err.message, "error"));
  r.readAsDataURL(f);
};

/* enrolment */
function enrolProg(p) { const b = $(".voice.rec"); if (b) $(".prog", b).style.strokeDashoffset = 276.5 * (1 - Math.min(1, p)); }
$$(".voice").forEach(b => b.onclick = e => {
  ripple(b, e);
  api("enrol", { role: b.dataset.role }).then(() => { $$(".voice").forEach(x => x.classList.remove("rec")); b.classList.remove("done"); b.classList.add("rec"); $("small", b).textContent = "listening…"; })
    .catch(err => toast(err.message === "audio is off" ? "Mic pipeline is off: enrolment needs it." : err.message, "warn"));
});

/* pre-brief chat */
function chatMsg(role, text) { const p = document.createElement("div"); p.className = "msg " + role; p.textContent = text; renderMath(p); const L = $("#chatlog"); $(".empty", L)?.remove(); L.append(p); L.scrollTop = L.scrollHeight; return p; }
$("#pbSel").onchange = () => { CHAT = []; $("#chatlog").innerHTML = `<p class="empty">New thread for ${esc($("#pbSel").value)}.</p>`; };
$("#chatForm").onsubmit = async e => {
  e.preventDefault();
  const q = $("#chatIn").value.trim(); if (!q || !$("#pbSel").value) return;
  $("#chatIn").value = ""; CHAT.push({ role: "user", text: q }); chatMsg("user", q);
  const wait = chatMsg("assistant", ""); words(wait, "thinking…");
  try { const j = await api("prebrief/chat", { cartridge_id: $("#pbSel").value, messages: CHAT }); CHAT.push({ role: "assistant", text: j.reply }); wait.textContent = j.reply; renderMath(wait); }
  catch (err) { wait.innerHTML = ic("triangle-alert") + " " + esc(err.message); }
};
$("#commitBtn").onclick = async e => {
  ripple(e.currentTarget, e);
  if (!CHAT.length) return toast("Talk it through first.", "warn");
  toast("Writing changes back to the cartridge…");
  try {
    const j = await api("prebrief/commit", { cartridge_id: $("#pbSel").value, messages: CHAT });
    const bad = j.checks.filter(c => c.ok === false);
    toast(j.verified ? `Saved · verified (${j.checks.length} sympy checks)` : `Saved, but sympy check failed on stage ${bad.map(b => b.stage).join(", ")}`, j.verified ? "good" : "error");
    CARTS = await api("cartridges"); fillCarts();
  } catch (err) { toast(err.message, "error"); }
};
$("#verifyBtn").onclick = async e => { ripple(e.currentTarget, e); await api("verify/" + $("#pbSel").value, {}); CARTS = await api("cartridges"); fillCarts(); toast("Marked verified", "good"); };

$("#startBtn").onclick = async e => {
  ripple(e.currentTarget, e);
  pushSetup();
  try { await api("start", {}); } catch (err) { toast(err.message, "error"); }
};

/* ---------------- live controls ---------------- */
function act(a, e) {
  const b = $(`.act[data-a="${a}"]`); if (!b) return;
  ripple(b, e); b.classList.add("hit"); setTimeout(() => b.classList.remove("hit"), 180);
  api("action/" + a, {});
}
$$(".act[data-a]").forEach(b => b.onclick = e => act(b.dataset.a, e));
addEventListener("keydown", e => {
  if (document.body.dataset.view !== "live" || e.target.matches("input,select,textarea") || e.metaKey || e.ctrlKey) return;
  const k = { "1": "probe", "2": "hint", "3": "extend", m: "moveon", r: "refresh" }[e.key.toLowerCase()];
  if (k) { e.preventDefault(); act(k); }
});
$("#slider").onchange = e => api("slider", { value: +e.target.value });
$("#personalLive").onchange = e => api("personal", { on: e.target.checked });
$("#typeForm").onsubmit = e => { e.preventDefault(); const t = $("#typeIn").value.trim(); if (!t) return; $("#typeIn").value = ""; api("inject", { speaker: $("#typeSpk").value, text: t }); };
$("#endBtn").onclick = e => {
  ripple(e.currentTarget, e);
  if (!confirm("End the session and write the full report?")) return;
  api("end", {});
};
$("#saveBtn").onclick = () => api("save", {});
$("#retryBtn").onclick = () => api("report/retry", {});
$("#newBtn").onclick = () => (!REP_BUSY || confirm("The report is still being written. Discard it and start over?")) &&
  (REP_BUSY || S.phase !== "done" || $("#saveBtn").hidden || confirm("This report is not saved. Discard it?")) && api("reset", {}).then(() => { CARDS = []; $("#cards").innerHTML = ""; $("#lines").innerHTML = ""; $("#problem").dataset.k = ""; $("#stages").dataset.k = ""; });

/* ---------------- report ---------------- */
function md(src) {
  const inl = s => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|[\s(])\*([^*\s](?:[^*]*?[^*\s])?)\*(?=[\s.,;:)!?]|$)/g, "$1<i>$2</i>").replace(/`(.+?)`/g, "<code>$1</code>").replace(/_(.+?)_/g, "<i>$1</i>");
  const out = []; let tbl = null, ul = false;
  for (const line of src.split("\n")) {
    if (/^\|/.test(line)) { if (/^\|[-| ]+\|$/.test(line)) continue; const cells = line.split("|").slice(1, -1).map(c => inl(c.trim())); if (!tbl) { tbl = []; out.push("<table>"); tbl.h = 1; out.push("<tr>" + cells.map(c => `<th>${c}</th>`).join("") + "</tr>"); } else out.push("<tr>" + cells.map(c => `<td>${c}</td>`).join("") + "</tr>"); continue; }
    if (tbl) { out.push("</table>"); tbl = null; }
    if (/^\s*[-*] /.test(line)) { if (!ul) { out.push("<ul>"); ul = true; } out.push("<li>" + inl(line.replace(/^\s*[-*] /, "")) + "</li>"); continue; }
    if (ul) { out.push("</ul>"); ul = false; }
    const h = line.match(/^(#{1,3}) (.*)/);
    if (h) out.push(`<h${h[1].length}>${inl(h[2])}</h${h[1].length}>`); else if (line.trim()) out.push(`<p>${inl(line)}</p>`);
  }
  if (tbl) out.push("</table>"); if (ul) out.push("</ul>");
  return out.join("\n");
}
function showReport(m) {
  view("report");
  $("#repDir").textContent = m.dir ? `Saved to ${m.dir}/ (transcript.md, report.md). Audio was never written to disk.` : "Shown on screen only: not saved (no consent yet).";
  $("#repBody").innerHTML = md(m.md || "");
  $("#saveBtn").hidden = !!m.dir;
  const sid = (m.dir || "").split("/").pop(), wb = $("#wbBtn");
  if (wb) { wb.hidden = !sid; wb.onclick = () => window.open("/board?view=" + sid, "im-board-" + sid, "popup,width=1180,height=820"); }
  $("#retryBtn").hidden = !/Narrative failed/.test(m.md || ""); renderMath($("#repBody"));
  $$("#repBody > *").forEach((el, i) => el.style.animationDelay = i * 40 + "ms");
}

/* silence gauge threshold ticks (20 / 45 / 90 s) */
$("#silGauge .ticks").innerHTML = [20, 45].map(t => { const a = t / 90 * 2 * Math.PI; return `<line x1="${60 + 46 * Math.cos(a)}" y1="${60 + 46 * Math.sin(a)}" x2="${60 + 58 * Math.cos(a)}" y2="${60 + 58 * Math.sin(a)}"/>`; }).join("");

/* live cartridge list while the bank builds */
setInterval(async () => {
  if (document.body.dataset.view !== "setup") return;
  try {
    const c = await api("cartridges");
    if (c.length !== CARTS.length) { const n = c.length - CARTS.length; CARTS = c; fillCarts(); if (n > 0) toast(`+${n} cartridge${n > 1 ? "s" : ""} (${c.length} total)`, "good"); }
  } catch { }
}, 8000);

connect();
