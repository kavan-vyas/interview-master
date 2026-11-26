/* Reports archive: past sessions as tabs, notes + categories per report. Overlay, so it never fights setState. */
(() => {
  const $a = s => document.querySelector(s);
  const ls = { get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } }, set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { } } };
  const E = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const PALETTE = ["#b6ff3b", "#5cffaa", "#4de2ff", "#b58cff", "#ffb547", "#ff5470", "#e8f3ea"];
  let LIST = [], CATS = [], OPEN = ls.get("im-tabs", []), ACTIVE = ls.get("im-tab", null), FILTER = null, Q = "", CACHE = {}, VIEW = {};

  /* ---------- shell ---------- */
  const root = document.createElement("section");
  root.id = "archive"; root.className = "archive"; root.hidden = true;
  root.innerHTML = `
    <header class="ar-top">
      <h1 class="ar-h" data-letters>Reports</h1>
      <input class="ar-search" id="arSearch" placeholder="Search title, problem, notes…" autocomplete="off">
      <button class="ar-x" id="arClose" title="Close (Esc)">${ic("x")}</button>
    </header>
    <div class="ar-body">
      <aside class="ar-side">
        <div class="ar-filter" id="arFilter"></div>
        <ul class="ar-list" id="arList"></ul>
        <div class="ar-cats" id="arCatEd" hidden></div>
      </aside>
      <div class="ar-main">
        <nav class="ar-tabs" id="arTabs"></nav>
        <div class="ar-pane" id="arPane"></div>
      </div>
    </div>`;
  document.body.append(root);
  letters(root.querySelector(".ar-h"));

  const btn = document.createElement("button");
  btn.className = "modeBtn arBtn"; btn.innerHTML = `<span>Reports</span>`; btn.title = "Past reports";
  document.querySelector(".top .meta").prepend(btn);
  btn.onclick = () => root.hidden ? open() : close();
  $a("#arClose").onclick = close;
  addEventListener("keydown", e => { if (e.key === "Escape" && !root.hidden) close(); });

  async function open() {
    root.hidden = false; document.body.classList.add("ar-on");
    [LIST, CATS] = await Promise.all([api("reports"), api("categories")]);
    nice(LIST);
    OPEN = OPEN.filter(id => LIST.some(r => r.id === id));
    if (!ACTIVE || !OPEN.includes(ACTIVE)) ACTIVE = OPEN[0] || null;
    drawSide(); drawTabs(); drawPane();
  }
  function nice(L) {   // slug titles -> problem title; empty sessions labelled
    const cs = (typeof CARTS !== "undefined" && CARTS) || [];
    L.forEach(r => {
      if (!r.main || r.main === "None") { if (r.title === "None" || r.title === r.id) r.title = "Empty session"; return; }
      const c = cs.find(x => x.id === r.main);
      if (c && r.title === r.main.replace(/-/g, " ")) r.title = c.title;
    });
    return L;
  }
  function close() { root.hidden = true; document.body.classList.remove("ar-on"); }
  let lastView = document.body.dataset.view;          // entering an interview always closes Reports (it covered the live screen)
  setInterval(() => { const v = document.body.dataset.view; if (v === "live" && lastView !== "live" && !root.hidden) close(); lastView = v; }, 400);
  window.openReports = open;

  /* ---------- sidebar: filter + list ---------- */
  const catColor = n => (CATS.find(c => c.name === n) || {}).color || "#7f9585";
  const chip = (n, on, attr = "") => `<button class="ar-chip${on ? " on" : ""}" style="--cc:${catColor(n)}" ${attr}>${E(n)}</button>`;
  function drawSide() {
    $a("#arFilter").innerHTML = chip("All", !FILTER, 'data-f=""') + CATS.map(c => chip(c.name, FILTER === c.name, `data-f="${E(c.name)}"`)).join("")
      + `<button class="ar-chip ghost" id="arEditCats">${ic("pencil")} categories</button>`;
    $a("#arFilter").querySelectorAll("[data-f]").forEach(b => b.onclick = () => { FILTER = b.dataset.f || null; drawSide(); });
    $a("#arEditCats").onclick = editCats;
    const q = Q.toLowerCase();
    const rows = LIST.filter(r => (!FILTER || r.categories.includes(FILTER)) && (!q || `${r.title} ${r.main} ${r.notes} ${r.when}`.toLowerCase().includes(q)));
    $a("#arList").innerHTML = rows.map((r, i) => `
      <li class="ar-item${OPEN.includes(r.id) ? " open" : ""}${r.id === ACTIVE ? " act" : ""}" data-id="${r.id}" style="--i:${i}">
        <b>${E(r.title)}${r.board ? ' <span class="ar-bd" title="whiteboard saved">${ic("layout-grid")}</span>' : ""}</b>
        <span class="ar-when">${E(r.when)}${r.duration ? " · " + r.duration : ""}${r.level ? " · L" + r.level : ""}</span>
        <span class="ar-dots">${r.categories.map(c => `<i style="--cc:${catColor(c)}" title="${E(c)}"></i>`).join("")}</span>
        ${r.notes ? `<span class="ar-note">${E(r.notes)}</span>` : ""}
      </li>`).join("") || `<li class="ar-empty">${LIST.length ? "Nothing matches." : "No saved reports yet. Finish a session with consent and it lands here."}</li>`;
    $a("#arList").querySelectorAll("[data-id]").forEach(li => li.onclick = () => openTab(li.dataset.id));
  }
  $a("#arSearch").oninput = e => { Q = e.target.value; drawSide(); };

  /* ---------- tabs ---------- */
  function openTab(id) { if (!OPEN.includes(id)) OPEN.push(id); ACTIVE = id; save(); drawSide(); drawTabs(); drawPane(); }
  function closeTab(id) {
    const i = OPEN.indexOf(id); OPEN = OPEN.filter(x => x !== id);
    if (ACTIVE === id) ACTIVE = OPEN[Math.min(i, OPEN.length - 1)] || null;
    save(); drawSide(); drawTabs(); drawPane();
  }
  function save() { ls.set("im-tabs", OPEN); ls.set("im-tab", ACTIVE); }
  function drawTabs() {
    $a("#arTabs").innerHTML = OPEN.map(id => {
      const r = LIST.find(x => x.id === id) || { title: id };
      return `<div class="ar-tab${id === ACTIVE ? " act" : ""}" data-id="${id}"><span>${E(r.title)}</span><button data-x="${id}" title="Close tab">${ic("x")}</button></div>`;
    }).join("");
    $a("#arTabs").querySelectorAll(".ar-tab").forEach(t => t.onclick = e => { if (e.target.dataset.x) return closeTab(e.target.dataset.x); ACTIVE = t.dataset.id; save(); drawSide(); drawTabs(); drawPane(); });
  }

  /* ---------- active report ---------- */
  async function drawPane() {
    const P = $a("#arPane");
    if (!ACTIVE) { P.innerHTML = `<div class="ar-blank"><h2>Pick a report</h2><p>Open several: each one gets a tab.</p></div>`; return; }
    const id = ACTIVE;
    const d = CACHE[id] || (CACHE[id] = await api("reports/" + id));
    if (id !== ACTIVE) return;
    const m = d.meta || {}, cats = m.categories || [], v = VIEW[id] || "report";
    P.innerHTML = `
      <div class="ar-doc">
        <div class="ar-seg"><button data-v="report" class="${v === "report" ? "on" : ""}">Report</button><button data-v="transcript" class="${v === "transcript" ? "on" : ""}">Transcript</button>${d.summary.board ? `<button class="ar-wb" onclick="window.open('/board?view=${id}','im-board-${id}','popup,width=1180,height=820')">Whiteboard ${ic("arrow-up-right")}</button>` : ""}</div>
        <article class="md ar-md"></article>
      </div>
      <aside class="ar-meta">
        <label>Title<input id="arTitle" value="${E(m.title || (LIST.find(x => x.id === d.summary.id) || d.summary).title)}"></label>
        <div><small class="ar-lbl">Categories</small><div class="ar-catpick">${CATS.map(c => chip(c.name, cats.includes(c.name), `data-c="${E(c.name)}"`)).join("")}</div></div>
        <label class="ar-notes">Notes<textarea id="arNotes" placeholder="What to remember about this mock, what to drill next time…">${E(m.notes || "")}</textarea></label>
        <small class="ar-saved" id="arSaved">${m.updated ? "saved " + E(m.updated) : ""}</small>
        <small class="ar-path">sessions/${E(id)}/</small>
      </aside>`;
    const art = P.querySelector(".ar-md");
    art.innerHTML = md(v === "report" ? d.report : d.transcript); renderMath(art);
    art.querySelectorAll(":scope > *").forEach((el, i) => el.style.animationDelay = Math.min(i, 30) * 30 + "ms");
    P.querySelectorAll("[data-v]").forEach(b => b.onclick = () => { VIEW[id] = b.dataset.v; drawPane(); });
    P.querySelectorAll("[data-c]").forEach(b => b.onclick = () => {
      const n = b.dataset.c, cur = new Set(d.meta.categories || []);
      cur.has(n) ? cur.delete(n) : cur.add(n);
      d.meta.categories = [...cur]; b.classList.toggle("on"); persist(id, { categories: d.meta.categories });
    });
    let t;
    const debounce = (k, val) => { d.meta[k] = val; $a("#arSaved").textContent = "saving…"; clearTimeout(t); t = setTimeout(() => persist(id, { [k]: val }), 600); };
    $a("#arNotes").oninput = e => debounce("notes", e.target.value);
    $a("#arTitle").oninput = e => debounce("title", e.target.value);
  }
  async function persist(id, patch) {
    try {
      const j = await api(`reports/${id}/meta`, patch);
      const el = $a("#arSaved"); if (el && id === ACTIVE) el.textContent = "saved " + j.updated;
      const r = LIST.find(x => x.id === id);
      if (r) { if (patch.categories) r.categories = patch.categories; if ("notes" in patch) r.notes = patch.notes.slice(0, 140); if (patch.title) r.title = patch.title; }
      drawSide(); if (patch.title) drawTabs();
    } catch (e) { toast("Couldn't save: " + e.message, "error"); }
  }

  /* ---------- category editor ---------- */
  function editCats() {
    const ed = $a("#arCatEd"); ed.hidden = false;
    let rows = CATS.map(c => ({ ...c, was: c.name }));
    const draw = () => {
      ed.innerHTML = `<h3>Categories</h3>` + rows.map((c, i) => `
        <div class="ar-crow"><input type="color" value="${c.color}" data-i="${i}" data-k="color"><input value="${E(c.name)}" data-i="${i}" data-k="name"><button data-del="${i}" title="Delete">${ic("x")}</button></div>`).join("")
        + `<div class="row"><button class="btn sm ghost" id="arAddCat">+ add</button><button class="btn sm" id="arSaveCats">Save</button><button class="btn sm ghost" id="arCancelCats">Cancel</button></div>`;
      ed.querySelectorAll("input").forEach(inp => inp.oninput = () => rows[+inp.dataset.i][inp.dataset.k] = inp.value);
      ed.querySelectorAll("[data-del]").forEach(b => b.onclick = () => { rows.splice(+b.dataset.del, 1); draw(); });
      $a("#arAddCat").onclick = () => { rows.push({ name: "New category", color: PALETTE[rows.length % PALETTE.length] }); draw(); ed.querySelector(`[data-i="${rows.length - 1}"][data-k=name]`).select(); };
      $a("#arCancelCats").onclick = () => ed.hidden = true;
      $a("#arSaveCats").onclick = async () => {
        const rename = Object.fromEntries(rows.filter(r => r.was && r.was !== r.name.trim()).map(r => [r.was, r.name.trim()]));
        CATS = await api("categories", { categories: rows.map(({ name, color }) => ({ name: name.trim(), color })), rename });
        ed.hidden = true; CACHE = {}; LIST = nice(await api("reports"));
        if (FILTER && !CATS.some(c => c.name === FILTER)) FILTER = rename[FILTER] || null;
        drawSide(); drawPane(); toast("Categories saved", "good");
      };
    };
    draw();
  }

  /* jump straight to a freshly saved report */
  const origShow = window.showReport;
  if (origShow) window.showReport = m => { origShow(m); if (m.dir) { const id = m.dir.split("/").pop(); CACHE[id] = null; delete CACHE[id]; } };
})();
