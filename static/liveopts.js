/* Options during ANY interview: ⋯ in the corner bar -> one panel with every live control. */
(() => {
  const post = (p, b = {}) => fetch("/api/" + p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) }).then(r => r.json()).catch(() => ({}));
  const get = p => fetch("/api/" + p).then(r => r.json()).catch(() => ({}));
  const B = document.body;
  const wait = setInterval(() => {
    const bar = document.querySelector(".livebar"); if (!bar) return; clearInterval(wait);
    const more = document.createElement("button"); more.type = "button"; more.className = "lb-more"; more.title = "Options"; more.innerHTML = ic("ellipsis");
    bar.prepend(more);
    const P = document.createElement("div"); P.className = "liveopts"; P.hidden = true;
    P.innerHTML = `
      <section><h4>Sound</h4>
        <label class="lo-row"><span>Speaker</span><button type="button" data-a="spk" class="lo-tg"></button></label>
        <label class="lo-row solo-only"><span>Mute interview <i>write + type, no talking</i></span><button type="button" data-a="mute" class="lo-tg"></button></label>
        <label class="lo-row"><span>Mic</span><select data-a="mic"></select></label>
        <label class="lo-row"><span>Output</span><select data-a="out"></select></label>
        <label class="lo-row solo-only"><span>Voice</span><select data-a="voice">
          <option value="bm_george">George</option><option value="bm_fable">Fable</option><option value="bm_lewis">Lewis</option>
          <option value="bm_daniel">Daniel</option><option value="bf_emma">Emma</option><option value="bf_isabella">Isabella</option></select></label>
        <label class="lo-row solo-only"><span>Speed <b data-v="speed"></b></span><input type="range" data-a="speed" min="0.9" max="1.35" step="0.05"></label>
        <button type="button" data-a="refresh" class="lo-btn">${ic("refresh-cw")} Find new mics / speakers</button></section>
      <section class="solo-only"><h4>Interviewer</h4>
        <div class="lo-grid">
          <button type="button" data-a="repeat" class="lo-btn">Repeat last line</button>
          <button type="button" data-a="hint" class="lo-btn">Hint please</button>
          <button type="button" data-a="minute" class="lo-btn">Give me 2 min</button>
          <button type="button" data-a="question" class="lo-btn">Show full question</button></div></section>
      <section><h4>Board</h4>
        <div class="lo-grid">
          <button type="button" data-a="qr" class="lo-btn">Show iPad QR</button>
          <button type="button" data-a="board" class="lo-btn">Open board on Mac ${ic("arrow-up-right")}</button>
          <button type="button" data-a="sidecar" class="lo-btn">Sidecar (cable)</button>
          <button type="button" data-a="fancy" class="lo-btn">Plain / fancy</button></div>
        <p class="lo-help" hidden data-v="schelp">Control Centre, Screen Mirroring, your iPad. Drag the board window onto it, then press the fullscreen button.</p></section>
      <section><div class="lo-grid">
          <button type="button" data-a="pause" class="lo-btn ghost">Pause <i>(not on the day)</i></button>
          <button type="button" data-a="end" class="lo-btn danger">End interview</button></div></section>`;
    B.append(P);
    const q = s => P.querySelector(`[data-a="${s}"]`);
    const ls = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch { } };
    let spkOn = true;
    const paint = () => {
      if (typeof S !== "undefined" && S && "speaker_on" in S && !paint.local) spkOn = S.speaker_on;
      q("spk").classList.toggle("on", spkOn); q("spk").textContent = spkOn ? "On" : "Off";
      const m = !!(typeof S !== "undefined" && S?.mute); q("mute").classList.toggle("on", m); q("mute").textContent = m ? "On" : "Off";
      P.classList.toggle("is-solo", B.classList.contains("solo"));
    };
    const fill = async (refresh) => {
      const j = refresh ? await post("mic/refresh") : await get("mic"), o = refresh ? j.speakers : await get("speaker");
      q("mic").innerHTML = (j.devices || []).map(d => `<option ${d === j.current ? "selected" : ""}>${d}</option>`).join("") || "<option>none</option>";
      q("out").innerHTML = ((o || {}).devices || []).map(d => `<option ${d === o.current ? "selected" : ""}>${d}</option>`).join("") || "<option>system default</option>";
    };
    const open = v => { paint.local = false; P.hidden = !v; more.classList.toggle("on", v); if (v) { fill(); paint();
      q("voice").value = ls("im-voice") || "bm_george"; q("speed").value = ls("im-speed") || "1.12"; P.querySelector('[data-v="speed"]').textContent = (+q("speed").value).toFixed(2) + "×"; } };
    more.onclick = () => open(P.hidden);
    const oldSync = window.syncSpk; window.syncSpk = v => { spkOn = v; oldSync?.(v); paint(); };
    q("spk").onclick = () => document.querySelector(".lb-spk")?.click();
    document.querySelector(".lb-spk")?.addEventListener("click", () => setTimeout(() => { spkOn = !document.querySelector(".lb-spk").classList.contains("off"); paint.local = true; paint(); }, 0));
    q("mute").onclick = async () => { const on = !(S?.mute); const r = await post("mute", { on }); if (typeof S !== "undefined") S.mute = r.mute;
      if (on) { window.syncSpk?.(false); post("spk", { on: false }); } else { window.syncSpk?.(true); post("spk", { on: true }); } paint(); };
    q("mic").onchange = e => post("mic", { name: e.target.value });
    q("out").onchange = e => post("speaker", { name: e.target.value });
    q("refresh").onclick = () => fill(true);
    q("voice").onchange = e => { ls("im-voice", e.target.value); post("voice", { voice: e.target.value, speed: +q("speed").value }); post("repeat"); };
    q("speed").oninput = e => { P.querySelector('[data-v="speed"]').textContent = (+e.target.value).toFixed(2) + "×"; };
    q("speed").onchange = e => { ls("im-speed", e.target.value); post("voice", { voice: q("voice").value, speed: +e.target.value }); };
    q("repeat").onclick = () => { post("repeat"); open(false); };
    q("hint").onclick = () => { post("action/hint"); open(false); };
    q("minute").onclick = () => { post("action/minute"); open(false); window.toast?.("He'll stay quiet for 2 minutes", "error"); };
    q("question").onclick = () => { const st = S?.cart?.statement || ""; const d = document.createElement("div"); d.className = "lo-q";
      d.innerHTML = `<div><h4>${esc(S?.cart?.title || "Question")}</h4><p></p><button type="button" class="lo-btn">Close</button></div>`;
      d.querySelector("p").textContent = st; window.renderMath?.(d); d.onclick = e => { if (e.target === d || e.target.closest("button")) d.remove(); };
      B.append(d); open(false); };
    q("qr").onclick = () => { window.__qrUntil = Date.now() + 60000; window.__qrForce = Date.now() + 60000; open(false); };
    q("board").onclick = () => window.open("/board", "im-board", "popup,width=1180,height=820");
    q("sidecar").onclick = () => { window.open("/board", "im-board", "popup,width=1180,height=820"); P.querySelector('[data-v="schelp"]').hidden = false; };
    q("fancy").onclick = () => document.querySelector(".modeBtn:not(.arBtn)")?.click();
    q("pause").onclick = () => { post("pause", { on: true }); open(false); };
    q("end").onclick = () => { open(false); document.querySelector("#endBtn")?.click(); };
    addEventListener("keydown", e => {
      if (B.dataset.view !== "live" || e.metaKey || e.ctrlKey || e.target.matches("input,textarea,select")) return;
      if (e.key === "Escape" && !P.hidden) open(false);
    });
    addEventListener("click", e => { if (!P.hidden && !P.contains(e.target) && e.target !== more) open(false); });
    setInterval(() => { if (B.dataset.view !== "live") P.hidden = true; }, 800);
  }, 300);
})();
