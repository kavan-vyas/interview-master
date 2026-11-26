import React from "react";
// iPad scribe pad: tldraw, nothing else. Snapshots of the current page go to the professor after each pause.
import { createRoot } from "react-dom/client";
import { Tldraw, getSnapshot, loadSnapshot } from "tldraw";
import "tldraw/tldraw.css";
import { getAssetUrlsByImport } from "@tldraw/assets/imports.vite";

const assetUrls = getAssetUrlsByImport();   // fonts/icons bundled locally: no CDN, works offline / on eduroam

const SID = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14);
let timer = null, sentAt = 0, dirty = false, busy = false;

const VIEW = new URLSearchParams(location.search).get("view");   // ?view=<session id>: read-only past board
const PKEY = VIEW ? undefined : "im-board";      // a past board never overwrites the live board's IndexedDB
async function snap(editor) {
  if (VIEW) return;
  if (!dirty || busy) return;
  if (editor.inputs.isPointing || editor.inputs.isDragging) { timer = setTimeout(() => snap(editor), 1500); return; }  // never export mid-stroke
  busy = true;
  const ids = [...editor.getCurrentPageShapeIds()];
  dirty = false;
  try {
    let blob;
    if (ids.length) ({ blob } = await editor.toImage(ids, { format: "png", background: true, darkMode: false, padding: 24, scale: 1 }));
    else {                                   // cleared page: tell the professor it's blank now
      const c = Object.assign(document.createElement("canvas"), { width: 400, height: 240 });
      const x = c.getContext("2d"); x.fillStyle = "#fff"; x.fillRect(0, 0, 400, 240);
      blob = await new Promise(r => c.toBlob(r, "image/png"));
    }
    const pages = editor.getPages(), pi = pages.findIndex(p => p.id === editor.getCurrentPageId());
    const r = await fetch(`/api/board/snap?sid=${SID}&page=${Math.max(0, pi)}&pages=${pages.length}`, { method: "POST", body: blob });
    if (!r.ok) throw new Error(r.status);
    sentAt = Date.now();
    if (SESS) fetch(`/api/board/doc?session=${SESS}`, { method: "POST", body: JSON.stringify(getSnapshot(editor.store).document) }).catch(() => {});
  } catch (e) { dirty = true; setTimeout(() => snap(editor), 5000); }
  finally { busy = false; }
}

// ---- one board per session: new session -> blank (or the saved board when resuming) ----
let SESS = null;
function wipe(editor) {
  const pages = editor.getPages();
  editor.run(() => {
    editor.setCurrentPage(pages[0].id);
    pages.slice(1).forEach(p => editor.deletePage(p.id));
    editor.deleteShapes([...editor.getCurrentPageShapeIds()]);
    editor.setCamera({ x: 0, y: 0, z: 1 });
  });
}
const idle = document.createElement("div");
idle.innerHTML = "<b>Waiting for the interview</b><span>A fresh board appears when the question starts.</span>";
idle.style.cssText = "position:fixed;inset:0;z-index:99998;display:none;flex-direction:column;align-items:center;justify-content:center;gap:10px;" +
  "background:#0b0f0c;color:#e8efe9;font:600 22px system-ui;text-align:center";
idle.lastChild.style.cssText = "font:400 15px system-ui;opacity:.6";
document.body.append(idle);
function sessions(editor) {
  const tick = async () => {
    try {
      const j = await (await fetch("/api/board/session")).json();
      idle.style.display = j.session ? "none" : "flex";       // no interview: nothing old on screen
      if (!j.session && SESS) { wipe(editor); localStorage.removeItem("im-board-sess"); }   // saved server-side already
      if (j.session && j.session !== localStorage.getItem("im-board-sess")) {
        if (j.mode === "restore") {
          const r = await fetch(`/api/board/doc?session=${j.session}`);
          if (r.ok) loadSnapshot(editor.store, { document: await r.json() });   // else keep what's on the iPad
        } else wipe(editor);
        localStorage.setItem("im-board-sess", j.session);
        dirty = true; clearTimeout(timer); timer = setTimeout(() => snap(editor), 800);
      }
      SESS = j.session;
    } catch { }
  };
  tick(); setInterval(tick, 3000);
}

function onMount(editor) {
  if (VIEW) fetch(`/api/board/doc?session=${VIEW}`).then(r => r.ok ? r.json() : null)
    .then(d => { if (d) { loadSnapshot(editor.store, { document: d }); editor.zoomToFit(); } });
  else sessions(editor);
  editor.user.updateUserPreferences({ colorScheme: "system" });   // follows iPad/Mac light/dark
  fingers(editor);
  editor.updateInstanceState({ isGridMode: false, isPenMode: false });
  // Sidecar sends Apple Pencil as a mix of pen + mouse events. tldraw's "pen mode" then ignores every
  // non-pen event -> board looks frozen. Keep pen mode permanently off.
  editor.sideEffects.registerAfterChangeHandler("instance", (prev, next) => {
    if (next.isPenMode) editor.updateInstanceState({ isPenMode: false });
  });
  editor.setCurrentTool("draw");
  editor.store.listen(() => { dirty = true; clearTimeout(timer); timer = setTimeout(() => snap(editor), 2500); },
    { source: "user", scope: "document" });
  setInterval(() => { if (dirty && Date.now() - sentAt > 20000) snap(editor); }, 5000);

  // Stuck-input watchdog: Sidecar can drop a pen-up (Pencil lifted at the screen edge / window loses
  // focus). tldraw then stays "mid-stroke" and ignores new input -> looks frozen. Reset it.
  let lastPtr = Date.now();
  for (const t of ["pointerdown", "pointermove", "pointerup"]) addEventListener(t, () => { lastPtr = Date.now(); }, true);
  const unstick = () => {
    const tool = editor.getCurrentToolId();
    if (editor.inputs.isPointing || editor.inputs.isDragging || editor.getPath().includes("drawing")) {
      editor.complete();
      editor.setCurrentTool(tool);             // complete() can drop to select; keep the pen in hand
    }
    editor.updateInstanceState({ isPenMode: false });
    if (!["draw", "eraser", "highlight", "select", "hand", "geo", "line", "arrow", "text", "laser"].includes(editor.getCurrentToolId())) editor.setCurrentTool("draw");
  };
  setInterval(() => { if (Date.now() - lastPtr > 2500 && (editor.inputs.isPointing || editor.getPath().includes("drawing"))) unstick(); }, 1000);
  addEventListener("contextmenu", e => e.preventDefault(), true);   // Pencil long-press must not open a menu
  addEventListener("blur", unstick);
  document.addEventListener("visibilitychange", unstick);
  addEventListener("pointerup", () => setTimeout(() => { if (editor.getPath().includes("drawing") && Date.now() - lastPtr > 300) unstick(); }, 400), true);
  window.__board = { editor, SID, snap: () => { dirty = true; return snap(editor); } };
}

// Safari + Sidecar: a Pencil drag arrives as a mouse drag, and Safari starts a native drag / text
// selection on it -> fires pointercancel ~5 ms in and the stroke dies. Never let Safari do that.
for (const t of ["dragstart", "selectstart"])
  addEventListener(t, e => { if (!e.target.closest?.("input,textarea,[contenteditable=true]")) e.preventDefault(); }, true);
// If Safari still cancels mid-stroke, keep the ink: finish the stroke instead of letting tldraw discard it.
addEventListener("pointercancel", e => {
  const ed = window.__board?.editor;
  if (ed && ed.getPath().includes("drawing")) { e.stopImmediatePropagation(); ed.complete(); ed.setCurrentTool(ed.getCurrentToolId()); }
}, true);
// Inactive window = Safari swallows Pencil input. Make that state impossible to miss.
const wake = document.createElement("div");
wake.textContent = "Board asleep (you used the Mac). Tap once anywhere to wake it.";
wake.style.cssText = "position:fixed;inset:auto 0 0 0;z-index:99999;padding:14px;text-align:center;font:600 18px system-ui;" +
  "background:#ffcf33;color:#222;pointer-events:none;display:none";
document.body.append(wake);
const showWake = () => { wake.style.display = document.hasFocus() ? "none" : "block"; };
addEventListener("blur", showWake); addEventListener("focus", showWake); setInterval(showWake, 1000);
const css = document.createElement("style");
css.textContent = `.tl-container, .tl-container * { -webkit-user-drag: none !important; -webkit-user-select: none; user-select: none; -webkit-touch-callout: none; touch-action: none; }
  .tl-container input, .tl-container textarea, .tl-container [contenteditable=true] { -webkit-user-select: text; user-select: text; }`;
document.head.append(css);

// ---- debug beacon: what input actually reaches this page (Sidecar/Pencil diagnosis) ----
const DBG = [];
for (const t of ["pointerdown", "pointermove", "pointerup", "pointercancel", "touchstart", "mousedown", "click"])
  addEventListener(t, e => { if (t !== "pointermove" || DBG.length < 400) DBG.push([Date.now() % 100000, t, e.pointerType || "", +(e.pressure || 0).toFixed(2), e.buttons, e.target?.className?.baseVal ?? String(e.target?.className || "").slice(0, 30)]); }, true);
addEventListener("error", e => DBG.push([Date.now() % 100000, "ERROR", String(e.message).slice(0, 200)]));
addEventListener("unhandledrejection", e => DBG.push([Date.now() % 100000, "REJECT", String(e.reason).slice(0, 200)]));
setInterval(() => {
  const ed = window.__board?.editor;
  const st = ed ? { tool: ed.getCurrentToolId(), path: ed.getPath(), pen: ed.getInstanceState().isPenMode, shapes: ed.getCurrentPageShapeIds().size,
    focus: document.hasFocus(), crashed: !!document.querySelector(".tl-error-boundary") } : { noEditor: true };
  fetch("/api/board/debug", { method: "POST", body: JSON.stringify({ st, ev: DBG.splice(0) }) }).catch(() => {});
}, 2000);

// ---- fullscreen: hides browser bars on iPad. Fallback hint: Add to Home Screen (launches fullscreen) ----
const standalone = navigator.standalone || matchMedia("(display-mode: fullscreen), (display-mode: standalone)").matches;
function FsButton() {
  const de = document.documentElement, can = !!(de.requestFullscreen || de.webkitRequestFullscreen);
  const isFs = () => !!(document.fullscreenElement || document.webkitFullscreenElement);
  const [fs, setFs] = React.useState(isFs()), [hint, setHint] = React.useState(false);
  React.useEffect(() => { const f = () => setFs(isFs()); document.addEventListener("fullscreenchange", f); document.addEventListener("webkitfullscreenchange", f);
    return () => { document.removeEventListener("fullscreenchange", f); document.removeEventListener("webkitfullscreenchange", f); }; }, []);
  if (standalone) return null;
  const go = async () => {
    try {
      if (isFs()) await (document.exitFullscreen || document.webkitExitFullscreen).call(document);
      else if (can) await (de.requestFullscreen || de.webkitRequestFullscreen).call(de);
      else throw 0;
    } catch { setHint(h => !h); }
  };
  const st = { position: "fixed", top: 8, left: "50%", transform: "translateX(-50%)", zIndex: 99999, height: 36, padding: "0 14px",
    borderRadius: 10, border: "1px solid #2a332c", background: "var(--color-panel, #111613)", color: "var(--color-text, #e8f3ea)", font: "600 13px system-ui", opacity: .85 };
  return <>
    <button style={st} onPointerUp={go}>{fs ? "Exit ⛶" : "⛶ Fullscreen"}</button>
    {hint && <div onPointerUp={() => setHint(false)} style={{ ...st, top: 50, height: "auto", padding: 10, maxWidth: 300, textAlign: "center" }}>
      This browser blocks fullscreen. Tap Share → Add to Home Screen, then open Board from the home screen: it runs fullscreen.</div>}
  </>;
}

// ---- Pencil-only ink (OneNote style): fingers pan (1) / pinch-zoom (2) the canvas, palm ignored while the Pencil writes ----
function fingers(editor) {
  const T = new Map(); let penAt = 0, g = null;
  const onCanvas = e => e.target?.closest?.(".tl-canvas");
  const pts = () => [...T.values()];
  const start = () => {
    const p = pts(), c = editor.getCamera();
    if (!p.length) { g = null; return; }
    const m = p.length > 1 ? { x: (p[0].x + p[1].x) / 2, y: (p[0].y + p[1].y) / 2 } : p[0];
    const d = p.length > 1 ? Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y) : 0;
    g = { z: c.z, d, P: { x: m.x / c.z - c.x, y: m.y / c.z - c.y } };     // page point under the fingers stays put
  };
  const kill = e => { e.stopPropagation(); e.stopImmediatePropagation(); if (e.cancelable) e.preventDefault(); };
  addEventListener("pointerdown", e => {
    if (e.pointerType === "pen") { penAt = Date.now(); T.clear(); g = null; return; }
    if (e.pointerType !== "touch" || !onCanvas(e)) return;
    kill(e);
    if (Date.now() - penAt < 600) return;                               // palm while writing
    const r = editor.getViewportScreenBounds();
    T.set(e.pointerId, { x: e.clientX - r.x, y: e.clientY - r.y }); start();
  }, true);
  addEventListener("pointermove", e => {
    if (e.pointerType === "pen") { penAt = Date.now(); return; }
    if (e.pointerType !== "touch") return;
    if (!T.has(e.pointerId)) { if (onCanvas(e)) kill(e); return; }
    kill(e);
    const r = editor.getViewportScreenBounds();
    T.set(e.pointerId, { x: e.clientX - r.x, y: e.clientY - r.y });
    const p = pts(); if (!g) return;
    const m = p.length > 1 ? { x: (p[0].x + p[1].x) / 2, y: (p[0].y + p[1].y) / 2 } : p[0];
    const z = p.length > 1 && g.d ? Math.min(8, Math.max(0.1, g.z * Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y) / g.d)) : g.z;
    editor.setCamera({ x: m.x / z - g.P.x, y: m.y / z - g.P.y, z });
  }, true);
  const up = e => { if (e.pointerType !== "touch") return; if (T.delete(e.pointerId)) { kill(e); start(); } else if (onCanvas(e)) kill(e); };
  addEventListener("pointerup", up, true); addEventListener("pointercancel", up, true);
  for (const t of ["touchstart", "touchmove", "touchend", "gesturestart", "gesturechange", "gestureend"])
    addEventListener(t, e => { if (onCanvas(e)) { e.stopPropagation(); e.stopImmediatePropagation(); if (e.cancelable && t !== "touchend") e.preventDefault(); } }, { capture: true, passive: false });
}

createRoot(document.getElementById("root")).render(
  <div style={{ position: "fixed", inset: 0 }}>
    <Tldraw persistenceKey={PKEY} onMount={onMount} assetUrls={assetUrls}
      components={{ ContextMenu: null }} />
    <FsButton />
  </div>
);
