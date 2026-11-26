/* Signature motion per section + header cube word + Begin showpiece. All skipped in Focus (calm) mode. */
(() => {
  const calm = () => window.isCalm && isCalm();
  const FAM = [["Unbounded", 800, "normal"], ["Fraunces", 300, "italic"], ["Syne", 800, "normal"], ["Instrument Serif", 400, "italic"],
    ["JetBrains Mono", 600, "normal"], ["Bricolage Grotesque", 800, "normal"], ["Fraunces", 900, "normal"], ["Space Grotesk", 700, "normal"]];

  /* ---------- header "Interview": one font for the whole word, rolls to the next like a cube face ---------- */
  const word = document.querySelector(".brand .kin:not(.thin)");
  if (word) {
    const txt = word.textContent; word.textContent = txt; word.classList.add("cube");
    let k = 0;
    const roll = async () => {
      if (calm() || document.hidden || document.body.dataset.view !== "setup") return;
      const out = word.animate([{ transform: "rotateX(0)" }, { transform: "rotateX(90deg)" }], { duration: 260, easing: "cubic-bezier(.5,0,.9,.4)" });
      await out.finished;
      const [f, w, st] = FAM[k = (k + 1) % FAM.length];
      word.style.fontFamily = `"${f}"`; word.style.fontWeight = w; word.style.fontStyle = st;
      word.animate([{ transform: "rotateX(-90deg)" }, { transform: "rotateX(0)" }], { duration: 420, easing: "cubic-bezier(.2,1.4,.4,1)" });
    };
    setInterval(roll, 2400);
    new MutationObserver(() => { if (calm()) word.style.fontFamily = word.style.fontWeight = word.style.fontStyle = ""; })
      .observe(document.body, { attributes: true, attributeFilter: ["data-motion"] });
  }

  /* ---------- themed section entrances (on scroll into view, and on re-render) ---------- */
  const THEME = [
    [".pk-card", "m-deal"],          // problems: cards dealt like a hand
    [".pk-peek", "m-unfold"],        // detail drawer unfolds in 3D
    [".chatlog .msg", "m-type"],     // pre-brief: messages typed in
    [".voice", "m-pop"],             // voices: elastic pop
    [".step", "m-rise"],             // setup panels rise with a clip wipe
    [".card", "m-slice"],            // live suggestion cards: sliced reveal
    [".ln", "m-ink"],                // transcript: ink wipe
    [".md > *", "m-print"],          // report: printed line by line
    [".ar-item", "m-skew"],          // archive: skew slide
  ];
  const io = new IntersectionObserver(es => es.forEach(e => {
    if (e.isIntersecting) { e.target.classList.add("m-in"); io.unobserve(e.target); }
  }), { threshold: .12 });
  const tag = root => {
    if (calm()) return;
    THEME.forEach(([sel, cls]) => (root.matches?.(sel) ? [root] : []).concat([...(root.querySelectorAll?.(sel) || [])]).forEach((el, i) => {
      if (el.dataset.m) return;
      el.dataset.m = 1; el.classList.add("m", cls); el.style.setProperty("--mi", i % 20);
      el.classList.add("m-in");   // decorative only: element is visible with or without the animation
    }));
  };
  tag(document.body);
  new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(n => n.nodeType === 1 && tag(n))))
    .observe(document.body, { childList: true, subtree: true });

  /* ---------- Begin interview showpiece ---------- */
  const GL = ["∑", "∫", "π", "∞", "√", "∂", "λ", "θ", "φ", "≡", "∀", "∃", "ℕ", "ℤ", "ℝ", "e", "i", "Δ", "∇", "≤", "∈", "ζ", "Ω", "μ"];
  let busyUntil = 0;
  const origSweep = window.sweep;
  if (origSweep) window.sweep = t => { const w = busyUntil - performance.now(); w > 0 ? setTimeout(() => origSweep(t), w) : origSweep(t); };

  function showpiece(btn) {
    if (calm()) return;
    const R = btn.getBoundingClientRect(), cx = innerWidth / 2, cy = innerHeight / 2;
    const ov = document.createElement("div"); ov.className = "sp"; document.body.append(ov);
    busyUntil = performance.now() + 2900;
    // 1. button letters fly off the button and orbit into the centre
    [...btn.querySelectorAll(".ch")].forEach((c, i, all) => {
      const r = c.getBoundingClientRect(), s = document.createElement("b");
      s.textContent = c.textContent; s.style.cssText = `left:${r.left}px;top:${r.top}px;font:${getComputedStyle(c).font}`;
      ov.append(s);
      const a = i / all.length * Math.PI * 2, rad = Math.min(innerWidth, innerHeight) * .28;
      s.animate([
        { transform: "none" },
        { transform: `translate(${cx + Math.cos(a) * rad - r.left}px,${cy + Math.sin(a) * rad - r.top}px) rotate(${360 + i * 25}deg) scale(1.8)`, offset: .45 },
        { transform: `translate(${cx - r.left}px,${cy - r.top}px) rotate(${720}deg) scale(0)`, opacity: 0 },
      ], { duration: 1500, delay: i * 25, easing: "cubic-bezier(.6,0,.3,1)", fill: "forwards" });
    });
    // 2. glyph vortex
    for (let i = 0; i < 48; i++) {
      const g = document.createElement("i"); g.textContent = GL[i % GL.length];
      const a = Math.random() * Math.PI * 2, d = Math.max(innerWidth, innerHeight) * (.55 + Math.random() * .3);
      g.style.cssText = `left:${cx}px;top:${cy}px;font-size:${14 + Math.random() * 46}px;color:${["#b6ff3b", "#4de2ff", "#b58cff", "#e8f3ea"][i % 4]}`;
      ov.append(g);
      g.animate([
        { transform: `translate(${Math.cos(a) * d}px,${Math.sin(a) * d}px) rotate(0)`, opacity: 0 },
        { opacity: .9, offset: .3 },
        { transform: `translate(${Math.cos(a + 2.4) * 40}px,${Math.sin(a + 2.4) * 40}px) rotate(${540}deg) scale(.3)`, opacity: 1, offset: .62 },
        { transform: `translate(${Math.cos(a + 2.6) * d * 1.4}px,${Math.sin(a + 2.6) * d * 1.4}px) rotate(900deg) scale(1.4)`, opacity: 0 },
      ], { duration: 2300, delay: Math.random() * 300, easing: "cubic-bezier(.45,0,.2,1)", fill: "forwards" });
    }
    // 3. the orb: logo clone swells in the centre, rings blow out as shockwaves
    const logo = document.querySelector(".logo")?.cloneNode(true);
    if (logo) {
      logo.classList.add("sp-orb"); ov.append(logo);
      logo.animate([{ transform: "translate(-50%,-50%) scale(0) rotate(-180deg)" }, { transform: "translate(-50%,-50%) scale(1) rotate(0)", offset: .55 },
        { transform: "translate(-50%,-50%) scale(1.15) rotate(30deg)", offset: .75 }, { transform: "translate(-50%,-50%) scale(9) rotate(90deg)", opacity: 0 }],
        { duration: 2400, delay: 300, easing: "cubic-bezier(.3,1.3,.5,1)", fill: "both" });
    }
    [0, 1, 2].forEach(n => { const w = document.createElement("u"); ov.append(w);
      w.animate([{ transform: "translate(-50%,-50%) scale(0)", opacity: .9 }, { transform: "translate(-50%,-50%) scale(14)", opacity: 0 }],
        { duration: 1300, delay: 1500 + n * 160, easing: "cubic-bezier(.1,.7,.2,1)", fill: "both" }); });
    // 4. title stamp
    const main = (CARTS || []).find(c => c.id === document.querySelector("#mainSel")?.value);
    const st = document.createElement("h2"); st.innerHTML = `<small>LIVE</small><span></span>`; ov.append(st);
    st.querySelector("span").textContent = main ? plainMath(main.title) : "Interview";
    st.animate([{ transform: "translate(-50%,-50%) scale(3)", opacity: 0, letterSpacing: ".6em" }, { transform: "translate(-50%,-50%) scale(1)", opacity: 1, letterSpacing: "-.02em", offset: .3 },
      { transform: "translate(-50%,-50%) scale(1)", opacity: 1, offset: .75 }, { transform: "translate(-50%,-50%) scale(.9) translateY(-40px)", opacity: 0 }],
      { duration: 1500, delay: 1350, easing: "cubic-bezier(.2,.9,.3,1)", fill: "both" });
    // 5. curtain: overlay irises out to reveal the dashboard
    ov.animate([{ clipPath: "circle(150% at 50% 50%)" }, { clipPath: "circle(0% at 50% 50%)" }], { duration: 600, delay: 2700, easing: "cubic-bezier(.7,0,.3,1)", fill: "forwards" })
      .finished.then(() => ov.remove());
  }
  window.showpiece = () => showpiece(document.querySelector("#startBtn"));
  document.querySelector("#startBtn")?.addEventListener("click", e => showpiece(e.currentTarget));
})();
