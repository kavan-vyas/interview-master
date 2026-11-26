/* Problem deck: ranked, filterable cards. Drives the hidden #warmSel/#mainSel so the rest of app.js is untouched. */
(() => {
  const $p = s => document.querySelector(s);
  const ls = { get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } }, set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { } } };
  const F0 = () => ({ real: 7, diff: [], top: [], src: [], multi: true, warm: false, undone: false, sort: "best", q: "" });
  const F = Object.assign(F0(), ls.get("im-pick2", {}));
  F.warm = false;                      // warm-ups retired: one main problem per interview
  let PEEK = null, FULL = {};

  const srcOf = c => (c.source || "").split("/")[0] || "other";
  const SRC_NAME = { jaymin: "Jaymin", "past-interviews": "Past interviews", juan: "Juan", nextstepmaths: "NextStep", "oxbridge-random": "Oxbridge", jzmaths: "JZ", "jshah-workbook": "JShah", other: "Other", tbo: "TBO", kclms: "KCL Maths School", waleed: "Waleed curves", "step-workbook": "STEP workbook", "zhou-quant": "Zhou quant", "sample-qs": "Sample Qs", screenshots: "Screenshots", siklos: "Siklos (STEP)", step1: "STEP I", mat: "MAT" };
  const R = c => c.rating || {};
  /* weak spots: lowest average score on the problem's topics first; problems already done drop back */
  const weak = c => { const P = window.PROG || { topics: {}, done: new Set() }, ts = (c.topics || []).map(t => P.topics[t]?.avg ?? 6);
    return (ts.length ? Math.min(...ts) : 6) + (P.done.has(c.id) ? 4 : 0) - (R(c).quality || 5) * .15; };
  const parts = c => Math.max(R(c).parts || 1, c.stages || 1);
  const prio = c => R(c).priority ?? -1;

  /* ---------- left sidebar: every filter one click ---------- */
  const D = c => R(c).d10, RE = c => R(c).real;
  const DIFF = [["1–3", 1, 3], ["3–5", 3, 5], ["5–7", 5, 7], ["7–10", 7, 10.1]];
  const nice = t => t.replace(/_/g, " ").replace(/\band\b/g, "&");
  const tog = (arr, v) => arr.includes(v) ? arr.splice(arr.indexOf(v), 1) : arr.push(v);
  function list(el, items, key) {
    el.innerHTML = items.map(([v, t, n]) => `<button data-v="${v}" class="${F[key].includes(v) ? "on" : ""}"><span>${t}</span><i>${n}</i></button>`).join("");
    el.querySelectorAll("button").forEach(b => b.onclick = () => { tog(F[key], b.dataset.v); draw(); });
  }
  function bar() {
    const base = CARTS.filter(c => F.warm ? c.warmup : !c.warmup), cnt = f => { const o = {}; base.forEach(c => f(c).forEach(k => o[k] = (o[k] || 0) + 1)); return o; };
    const tc = cnt(c => c.tags || []), sc = cnt(c => [srcOf(c)]);
    $p("#pkReal").innerHTML = [0, 6, 7, 8, 9].map(v => `<button data-v="${v}" class="${F.real === v ? "on" : ""}">${v ? v + "+" : "Any"}</button>`).join("");
    $p("#pkReal").querySelectorAll("button").forEach(b => b.onclick = () => { F.real = +b.dataset.v; draw(); });
    $p("#pkDiff").innerHTML = DIFF.map(([t]) => `<button data-v="${t}" class="${F.diff.includes(t) ? "on" : ""}">${t}</button>`).join("");
    $p("#pkDiff").querySelectorAll("button").forEach(b => b.onclick = () => { tog(F.diff, b.dataset.v); draw(); });
    $p("#pkType").innerHTML = [["multi", "Multi-part"], ["undone", "Not done yet"]].map(([k, t]) => `<button data-k="${k}" class="${F[k] ? "on" : ""}"><span>${t}</span></button>`).join("");
    $p("#pkType").querySelectorAll("button").forEach(b => b.onclick = () => { F[b.dataset.k] = !F[b.dataset.k]; draw(); });
    list($p("#pkTop"), Object.keys(tc).sort((a, b) => tc[b] - tc[a]).map(k => [k, nice(k), tc[k]]), "top");
    list($p("#pkSrc"), Object.keys(sc).sort((a, b) => sc[b] - sc[a]).map(k => [k, SRC_NAME[k] || k, sc[k]]), "src");
    $p("#pkSort").value = F.sort; if (document.activeElement !== $p("#pkQ")) $p("#pkQ").value = F.q;
  }
  $p("#pkSort").onchange = e => { F.sort = e.target.value; draw(); };
  $p("#pkQ").oninput = e => { F.q = e.target.value; draw(); };
  $p("#pkClr").onclick = () => { Object.assign(F, F0(), { real: 0 }); draw(); };

  /* ---------- filtering: unrated problems pass until the rater reaches them, listed after rated ones ---------- */
  const best = c => RE(c) != null ? RE(c) * 1.2 + (R(c).quality || 5) * .4 : prio(c) / 20 - 5;
  function rows() {
    const q = F.q.toLowerCase(), P = window.PROG || { done: new Set() };
    const out = CARTS.filter(c =>
      (F.warm ? c.warmup : !c.warmup) &&
      (!F.multi || F.warm || parts(c) >= 2) &&
      (!F.undone || !P.done.has(c.id)) &&
      (!F.real || RE(c) == null || RE(c) >= F.real) &&
      (!F.diff.length || D(c) == null || DIFF.some(([t, lo, hi]) => F.diff.includes(t) && D(c) >= lo && D(c) < hi)) &&
      (!F.top.length || (c.tags || []).some(t => F.top.includes(t))) &&
      (!F.src.length || F.src.includes(srcOf(c))) &&
      (!q || `${c.title} ${(c.tags || []).join(" ")} ${(c.topics || []).join(" ")} ${c.statement} ${R(c).tagline || ""}`.toLowerCase().includes(q)));
    const by = {
      best: (a, b) => best(b) - best(a),
      real: (a, b) => (RE(b) ?? 0) - (RE(a) ?? 0) || best(b) - best(a),
      hard: (a, b) => (D(b) ?? 0) - (D(a) ?? 0),
      easy: (a, b) => (D(a) ?? 99) - (D(b) ?? 99),
      short: (a, b) => (R(a).minutes || 99) - (R(b).minutes || 99),
      parts: (a, b) => parts(b) - parts(a) || best(b) - best(a),
      weak: (a, b) => weak(a) - weak(b) || best(b) - best(a),
    }[F.sort] || ((a, b) => best(b) - best(a));
    return out.sort(by);
  }

  /* ---------- cards ---------- */
  const dots = (n, max = 5) => Array.from({ length: max }, (_, i) => `<i class="${i < n ? "on" : ""}"></i>`).join("");
  function card(c, i) {
    const r = R(c), sc = r.quality ?? r.score ?? null, sel = c.id === $p("#mainSel").value ? "main" : c.id === $p("#warmSel").value ? "warm" : "";
    return `<article class="pk-card t${r.tier || "x"}${sel ? " sel " + sel : ""}${c.id === PEEK ? " peek" : ""}" data-id="${c.id}" style="--i:${Math.min(i, 24)};--sc:${(sc || 0) / 10}">
      <header>
        <span class="pk-tier">${r.tier || "–"}</span>
        ${r.d10 != null ? `<span class="pk-dr" title="difficulty · realism (1–10)">D ${r.d10.toFixed(1)} · R ${r.real.toFixed(1)}</span>` : `<span class="pk-dr">rating…</span>`}
        ${c.refined ? `<span class="pk-flag ref" title="refined into clean parts">refined</span>` : ""}
        ${c.verified ? `<span class="pk-flag ok">verified</span>` : ""}
        ${(r.flags || []).length ? `<span class="pk-flag warn" title="${esc(r.flags.join(", "))}">!</span>` : ""}
      </header>
      <h4 class="pk-t">${esc(c.title)}</h4>
      ${r.tagline ? `<p class="pk-tag">${esc(r.tagline)}</p>` : ""}
      <footer>
        <span class="pk-parts" title="${parts(c)} parts">${Array.from({ length: Math.min(parts(c), 8) }, () => "<i></i>").join("")}<em>${parts(c)}</em></span>
        <span class="pk-min">${r.minutes ? r.minutes + "′" : ""}</span>
        <span class="pk-src">${SRC_NAME[srcOf(c)] || srcOf(c)}${r.rank ? " · #" + r.rank : ""}</span>
      </footer>
      ${sel === "main" ? `<span class="pk-badge">MAIN</span>` : ""}
      <button class="pk-info" type="button" title="Details">${ic("ellipsis")}</button>
    </article>`;
  }
  function draw() {
    ls.set("im-pick2", { ...F, q: "" });
    bar();
    const rs = rows();
    $p("#pkCount").textContent = `${rs.length} shown`;
    $p("#pkGrid").innerHTML = rs.slice(0, 120).map(card).join("") ||
      `<p class="pk-none">Nothing matches. ${F.multi ? "Try turning off Multi-part." : ""}</p>`;
    renderMath($p("#pkGrid"));
    /* one click = this is the main problem; the ⋯ button opens details */
    $p("#pkGrid").querySelectorAll(".pk-card").forEach(el => el.onclick = e => {
      if (e.target.closest(".pk-info")) return peek(el.dataset.id);
      if ($p("#mainSel").value !== el.dataset.id) set("main", el.dataset.id);
    });
    slots();
  }

  /* ---------- slots ---------- */
  function slot(el, id, kind) {
    const c = CARTS.find(x => x.id === id);
    el.innerHTML = c
      ? `<b>${esc(c.title)}</b><span>${R(c).d10 != null ? `D ${R(c).d10.toFixed(1)} · R ${R(c).real.toFixed(1)} · ` : ""}${parts(c)} parts${R(c).minutes ? " · ~" + R(c).minutes + " min" : ""}</span><button class="pk-clr" title="Clear">${ic("x")}</button>`
      : `<span class="pk-empty">${kind === "main" ? "Click a card to choose" : ""}</span>`;
    renderMath(el);
    const x = el.querySelector(".pk-clr"); if (x) x.onclick = ev => { ev.stopPropagation(); set(kind, ""); };
    el.parentElement.onclick = () => c && peek(c.id);
  }
  /* exposed for solo presets: next problem = weak topics first, unseen, multi-part, tier S-B */
  window.PICK = {
    set: (k, id, loud) => set(k, id, !loud),
    next: (opts = {}) => {
      const P = window.PROG || { done: new Set() }, tiers = opts.tiers || ["S", "A", "B"];
      const pool = (window.CARTS || CARTS || []).filter(c => !c.warmup && parts(c) >= (opts.minParts || 2) && tiers.includes(R(c).tier) && !P.done.has(c.id));
      const by = opts.best ? (a, b) => prio(b) - prio(a) : (a, b) => weak(a) - weak(b) || prio(b) - prio(a);
      return pool.sort(by)[0] || null;
    },
  };
  function slots() {
    if ($p("#warmSel")?.value) { $p("#warmSel").value = ""; $p("#warmSel").dispatchEvent(new Event("change")); }   // no warm-ups
    slot($p("#slotMain"), $p("#mainSel").value, "main");
  }
  function set(kind, id, quiet) {
    const s = $p(kind === "main" ? "#mainSel" : "#warmSel");
    if (id && ![...s.options].some(o => o.value === id)) s.add(new Option(id, id));
    s.value = id; s.dispatchEvent(new Event("change"));
    draw(); if (PEEK) peek(PEEK, true);
    if (id && !quiet) toast(kind === "main" ? "Main problem set" : "Warm-up set", "good");
  }

  /* ---------- peek drawer ---------- */
  async function peek(id, keep) {
    if (PEEK === id && !keep) { PEEK = null; $p("#pkPeek").hidden = true; return draw(); }
    PEEK = id; const P = $p("#pkPeek"); P.hidden = false;      // drawer floats over the page: grid never reflows
    $p("#pkGrid").querySelectorAll(".pk-card").forEach(el => el.classList.toggle("peek", el.dataset.id === id));
    const c = CARTS.find(x => x.id === id), r = R(c);
    if (!FULL[id]) { P.innerHTML = `<p class="pk-none">Loading…</p>`; FULL[id] = (await api("cartridge/" + id)).cart; }
    if (PEEK !== id) return;
    const f = FULL[id], isMain = $p("#mainSel").value === id, isWarm = $p("#warmSel").value === id;
    P.innerHTML = `
      <button class="pk-x" title="Close">${ic("x")}</button>
      <div class="pk-ph"><span class="pk-tier big t${r.tier || "x"}">${r.tier || "–"}</span><div><h3>${esc(f.title)}</h3>
        <small>difficulty ${r.d10 ?? "?"}/10 · realism ${r.real ?? "?"}/10 · ${(c.tags || []).map(t => t.replace(/_/g, " ")).join(", ")} · ${parts(c)} parts${r.minutes ? " · ~" + r.minutes + " min" : ""}${r.rank ? " · rank #" + r.rank : ""}</small></div></div>
      ${r.tagline ? `<p class="pk-why">${esc(r.tagline)}</p>` : ""}
      <div class="pk-stmt"></div>
      <h5>Stages</h5><ol class="pk-stages">${(f.stages || []).map(s => `<li></li>`).join("")}</ol>
      ${(r.flags || []).length ? `<p class="pk-warn">${ic("triangle-alert")} ${esc(r.flags.join(", ").replace(/_/g, " "))}</p>` : ""}
      <div class="pk-acts">
        <button class="btn sm" data-a="main">${isMain ? ic("check") + " Main problem" : "Set as main"}</button>
        <button class="btn sm ghost" data-a="pb">Pre-brief this</button>
      </div>
      <small class="pk-meta">${esc(f.source || "")} · ${(f.topics || []).map(esc).join(", ")}</small>`;
    mathText(P.querySelector(".pk-stmt"), f.statement);
    P.querySelectorAll(".pk-stages li").forEach((li, i) => mathText(li, f.stages[i].goal));
    renderMath(P.querySelector("h3"));
    P.querySelector(".pk-x").onclick = () => peek(id);
    P.querySelectorAll("[data-a]").forEach(b => b.onclick = e => {
      ripple(b, e);
      const a = b.dataset.a;
      if (a === "main") set("main", isMain ? "" : id);
      if (a === "warm") set("warm", isWarm ? "" : id);
      if (a === "pb") { const s = $p("#pbSel"); s.value = id; s.dispatchEvent(new Event("change")); s.closest(".step").scrollIntoView({ behavior: "smooth", block: "center" }); }
    });
  }
  addEventListener("keydown", e => { if (e.key === "Escape" && PEEK && document.querySelector("#archive")?.hidden !== false) peek(PEEK); });

  /* ---------- hook into app.js ---------- */
  const orig = window.fillCarts;
  let auto = false;
  window.fillCarts = function () {
    orig(); FULL = {};
    if (!auto && !S.main_id && CARTS.some(c => c.rating)) {   // default main = best-ranked Jaymin multi-part
      auto = true; const top = rows()[0]; if (top) return set("main", top.id, true);
    }
    draw();
  };
  if (CARTS.length) draw();

  /* refresh when ratings / refinements land (setup screen only) */
  const sig = cs => cs.map(c => c.id + (c.rating?.priority ?? "") + (c.refined ? "r" : "")).join();
  setInterval(async () => {
    if (document.body.dataset.view !== "setup" || document.hidden) return;   // never during an interview / report
    try { const c = await api("cartridges"); if (sig(c) !== sig(CARTS)) { CARTS = c; fillCarts(); } } catch { }
  }, 10000);
})();
