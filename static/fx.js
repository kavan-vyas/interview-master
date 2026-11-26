/* fx layer: Focus/Flashy toggle + extra motion. Loaded after app.js. */
(() => {
  const B = document.body;
  const store = { get() { try { return localStorage.getItem("im-motion"); } catch { return null; } }, set(v) { try { localStorage.setItem("im-motion", v); } catch { } } };
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  window.isCalm = () => B.dataset.motion === "calm";

  /* ---------------- mode toggle ---------------- */
  const btn = document.createElement("button");
  btn.className = "modeBtn"; btn.title = "Toggle focus mode (F)";
  btn.innerHTML = `<b></b><span></span>`;
  document.querySelector(".top .meta").prepend(btn);
  function setMode(m) {
    B.dataset.motion = m; store.set(m);
    btn.querySelector("span").innerHTML = m === "calm" ? ic("sparkles") + " Fancy mode" : "Plain mode";
    btn.title = m === "calm" ? "Switch back to fancy (F)" : "Switch to plain (F)";
  }
  setMode(store.get() || (reduced ? "calm" : "flashy"));
  btn.onclick = () => setMode(isCalm() ? "flashy" : "calm");
  addEventListener("keydown", e => {
    if (e.key.toLowerCase() === "f" && !e.target.matches("input,select,textarea") && !e.metaKey && !e.ctrlKey) { e.preventDefault(); btn.click(); }
  });

  /* ---------------- floating maths glyphs ---------------- */
  const G = "∑∫π∞√∂≡∀∃ℕℝℤℚ∈⊂∇λθφεδ±≠≈∮∏ζ".split("");
  const FONTS = ["var(--f-serif)", "var(--f-ital)", "var(--f-syne)", "var(--f-mono)", "var(--f-wide)"];
  const layer = document.createElement("div"); layer.className = "glyphs"; layer.setAttribute("aria-hidden", "true");
  for (let i = 0; i < 26; i++) {
    const s = document.createElement("span");
    s.textContent = G[i % G.length];
    s.style.cssText = `left:${Math.random() * 100}%;--s:${18 + Math.random() * 60}px;--t:${28 + Math.random() * 40}s;--d:${-Math.random() * 60}s;--r:${(Math.random() - .5) * 220}deg;font-family:${FONTS[i % 5]}`;
    layer.append(s);
  }
  B.prepend(layer);

  /* ---------------- marquee of topics ---------------- */
  const mq = document.createElement("div"); mq.className = "marquee"; mq.setAttribute("aria-hidden", "true");
  const hero = document.querySelector("#setup .hero"); hero.after(mq);
  const base = ["Probe", "think aloud", "Hint ladder", "∑ k³ = (∑ k)²", "rigour", "Extend", "what if?", "n³ − n", "induction", "smaller case"];
  function fillMarquee() {
    const topics = [...new Set((typeof CARTS !== "undefined" ? CARTS : []).flatMap(c => c.topics || []))].slice(0, 24);
    const items = topics.length ? base.flatMap((b, i) => [b, topics[i % topics.length]]) : base;
    mq.innerHTML = `<div>${[...items, ...items].map(t => `<span>${esc(t)}</span>`).join("")}</div>`;
  }
  fillMarquee(); setInterval(fillMarquee, 30000);

  /* ---------------- label roll on buttons ---------------- */
  document.querySelectorAll(".act, .btn:not(.huge)").forEach(b => {
    const tn = [...b.childNodes].find(n => n.nodeType === 3 && n.textContent.trim());
    if (!tn) return;
    const t = tn.textContent.trim(), r = document.createElement("span");
    r.className = "roll"; r.innerHTML = `<span>${t}</span><span aria-hidden="true">${t}</span>`;
    tn.replaceWith(r);
  });

  /* ---------------- scramble text in ---------------- */
  const CH = "∑∫πλ01234ABCDEFxyzn";
  function scramble(el) {
    if (isCalm() || el.dataset.scr) return;
    const final = el.textContent; el.dataset.scr = 1; el.classList.add("scr");
    let f = 0;
    const id = setInterval(() => {
      f++;
      el.textContent = [...final].map((c, i) => (c === " " || i < f / 2) ? c : CH[Math.random() * CH.length | 0]).join("");
      if (f / 2 >= final.length) { clearInterval(id); el.textContent = final; }
    }, 28);
  }
  const io = new IntersectionObserver(es => es.forEach(e => e.isIntersecting && scramble(e.target)), { threshold: .6 });
  document.querySelectorAll(".step em, .panel h3, .transcript h3").forEach(el => {
    // only scramble the h3's own first text node, keep child spans
    if (el.children.length) { const tn = el.firstChild; if (tn && tn.nodeType === 3) { const s = document.createElement("span"); s.textContent = tn.textContent; tn.replaceWith(s); io.observe(s); } }
    else io.observe(el);
  });

  /* ---------------- 3D tilt ---------------- */
  addEventListener("pointermove", e => {
    if (isCalm()) return;
    const el = e.target.closest && e.target.closest(".step:not(.full), .panel, .card");
    document.querySelectorAll(".tilt").forEach(t => t !== el && t.classList.remove("tilt"));
    if (!el) return;
    const r = el.getBoundingClientRect(), k = el.classList.contains("card") ? 7 : 4;
    el.style.setProperty("--ry", ((e.clientX - r.left) / r.width - .5) * k + "deg");
    el.style.setProperty("--rx", -((e.clientY - r.top) / r.height - .5) * k + "deg");
    el.classList.add("tilt");
  });
  document.addEventListener("pointerleave", () => document.querySelectorAll(".tilt").forEach(t => t.classList.remove("tilt")));

  /* ---------------- cursor ring (lags behind) ---------------- */
  const cur = document.createElement("div"); cur.className = "cursor"; B.append(cur);
  let cx = innerWidth / 2, cy = innerHeight / 2, tx = cx, ty = cy;
  addEventListener("pointermove", e => { tx = e.clientX; ty = e.clientY; cur.classList.toggle("big", !!(e.target.closest && e.target.closest("button, select, .card, .who, label.toggle"))); });
  (function loop() { cx += (tx - cx) * .18; cy += (ty - cy) * .18; cur.style.transform = `translate(${cx}px,${cy}px)`; requestAnimationFrame(loop); })();

  /* ---------------- stage list stagger ---------------- */
  new MutationObserver(() => document.querySelectorAll("#stages li").forEach((li, i) => li.style.setProperty("--i", i)))
    .observe(document.querySelector("#stages"), { childList: true });
})();

/* ---------------- living type: "Interview" words + Begin button ----------------
   Every letter in a group wears a different font variant; each tick the whole group rotates one step,
   so every letter changes and no two letters ever share a font. */
(() => {
  const V = [["Unbounded", 800, "normal"], ["Fraunces", 300, "italic"], ["Syne", 800, "normal"], ["Instrument Serif", 400, "italic"],
    ["JetBrains Mono", 600, "normal"], ["Bricolage Grotesque", 200, "normal"], ["Fraunces", 900, "normal"], ["Space Grotesk", 300, "normal"],
    ["Unbounded", 200, "normal"], ["Instrument Serif", 400, "normal"], ["Syne", 400, "normal"], ["Bricolage Grotesque", 800, "normal"],
    ["Fraunces", 600, "italic"], ["JetBrains Mono", 400, "normal"], ["Space Grotesk", 700, "normal"], ["Fraunces", 100, "normal"]];
  const GROUPS = ["#startBtn [data-split]", ".mega:not(.outline)"];
  const chars = sel => [...document.querySelectorAll(sel + " .ch")].filter(c => c.textContent.trim());
  const put = (el, v, anim, delay) => {
    el.style.fontFamily = `"${v[0]}"`; el.style.fontWeight = v[1]; el.style.fontStyle = v[2];
    anim && el.animate([{ transform: "translateY(-30%) rotateX(85deg) scale(.9)", opacity: .2 }, { transform: "none", opacity: 1 }],
      { duration: 480, delay, easing: "cubic-bezier(.2,.9,.3,1.3)", fill: "backwards" });
  };
  const off = GROUPS.map((_, g) => g * 5);
  const tick = (anim = true) => {
    if (isCalm()) return;
    if (document.body.dataset.view !== "setup") {   // interview/report: one clean font, no per-letter swapping
      GROUPS.forEach(sel => chars(sel).forEach(c => { c.style.fontFamily = c.style.fontWeight = c.style.fontStyle = ""; }));
      return;
    }
    GROUPS.forEach((sel, g) => {
      if (document.hidden) return;
      off[g] = (off[g] + 1) % V.length;
      chars(sel).forEach((c, i) => put(c, V[(i + off[g]) % V.length], anim, i * 35));
    });
  };
  tick(false);
  setInterval(tick, 1100);
  const b = document.querySelector(".brand");
  
  // calm mode: stylesheet fonts back, SVG (SMIL) motion paused
  new MutationObserver(() => {
    const calm = isCalm();
    document.querySelectorAll(".logo").forEach(s => calm ? s.pauseAnimations() : s.unpauseAnimations());
    if (calm) GROUPS.forEach(sel => chars(sel).forEach(c => { c.style.fontFamily = c.style.fontWeight = c.style.fontStyle = ""; }));
    else tick(false);
  }).observe(document.body, { attributes: true, attributeFilter: ["data-motion"] });
})();

/* ---------------- logo: spin builds up the longer you hover, coasts down after ---------------- */
(() => {
  const L = document.querySelector(".logo"); if (!L) return;
  let ang = 0, vel = 0, t0 = 0, on = false, last = 0, raf = 0;
  const step = t => {
    const dt = Math.min(.05, (t - (last || t)) / 1000); last = t;
    if (on) { const held = (t - t0) / 1000; vel = Math.min(5400, 90 + held * held * 520); }   // deg/s, quadratic ramp to ~15 rev/s
    else vel *= Math.pow(.12, dt);                                                            // friction coast
    ang = (ang + vel * dt) % 360;
    const s = 1 + Math.min(.22, vel / 24000);
    L.style.transform = `rotate(${ang}deg) scale(${s})`;
    L.style.filter = vel > 900 ? `drop-shadow(0 0 ${Math.min(10, vel / 500)}px rgba(182,255,59,.55))` : "";
    if (on || vel > 4) raf = requestAnimationFrame(step);
    else { raf = 0; last = 0; L.style.transition = "transform .6s cubic-bezier(.2,.9,.3,1.3)"; L.style.transform = `rotate(${Math.round(ang / 360) * 360}deg)`; L.style.filter = ""; ang = 0; }
  };
  L.addEventListener("mouseenter", () => { if (isCalm()) return; on = true; t0 = performance.now(); L.style.transition = "none"; if (!raf) raf = requestAnimationFrame(step); });
  L.addEventListener("mouseleave", () => { on = false; });
})();
