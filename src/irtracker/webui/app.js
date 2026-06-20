/* iRacing Config Tracker — front-end logic.
   Talks to the Python backend through one bridge that works both inside a
   pywebview window (window.pywebview.api) and in a plain browser (fetch /api). */

"use strict";

/* ------------------------------------------------------------------ bridge
   Two transports: pywebview (native window) and fetch (browser fallback).
   We decide ONCE at boot. Under pywebview we must never use a relative fetch
   — the page is loaded via html= with no real origin, so the URL can't be
   parsed. Instead we wait for the API method to finish injecting. */
let TRANSPORT = null; // "pywebview" | "browser"

function waitForApiMethod(method, timeoutMs = 10000) {
  return new Promise((resolve) => {
    const t0 = Date.now();
    (function check() {
      const a = window.pywebview && window.pywebview.api;
      if (a && typeof a[method] === "function") return resolve(true);
      if (Date.now() - t0 > timeoutMs) return resolve(false);
      setTimeout(check, 70);
    })();
  });
}

async function api(method, ...args) {
  try {
    if (TRANSPORT === "pywebview" || window.pywebview) {
      const ready = await waitForApiMethod(method);
      if (!ready) return { ok: false, error: "The app is still starting up — give it a moment and try again." };
      return await window.pywebview.api[method](...args);
    }
    const res = await fetch(new URL("/api/" + method, location.href).toString(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(args),
    });
    if (!res.ok) return { ok: false, error: "Server returned " + res.status };
    return await res.json();
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

/* --------------------------------------------------------- friendly labels */
const FILE_LABELS = {
  "app.ini": "Graphics & Display",
  "controls.cfg": "Controls & Force Feedback",
  "joyCalib.yaml": "Wheel & Pedal Calibration",
  "core.ini": "Core Game Settings",
  "fueldata.ini": "Fuel Data",
  "camera.ini": "Camera Settings",
};
// Tracked keys may be bare names (app.ini) or profile-relative paths
// (profiles/controls/Oval/controls.cfg).
function keyBase(name) {
  const s = String(name).replace(/\\/g, "/");
  return s.slice(s.lastIndexOf("/") + 1);
}
function keyProfile(name) {
  const m = String(name).replace(/\\/g, "/").match(/^profiles\/controls\/([^/]+)\//i);
  return m ? m[1] : null;
}
function fileLabel(name) {
  const base = keyBase(name);
  const label = FILE_LABELS[base]
    || (/^rendererDX11/i.test(base) ? "Monitor / Graphics Renderer" : base);
  const prof = keyProfile(name);
  return prof ? `${label} · ${prof} profile` : label;
}

const TRIGGER_LABELS = {
  event: "Saved automatically after an edit",
  startup_scan: "Saved when the app started up",
  rescan: "Saved during a routine check",
  sim_exit: "Saved after your session ended",
  manual: "You backed this up",
  pre_restore: "Safety backup before a restore",
  restore: "Restored an earlier version",
  resume_scan: "Saved when auto-backup resumed",
  unknown: "Backup",
};
function triggerLabel(t) { return TRIGGER_LABELS[t] || "Backup"; }

const POLICY_LABELS = {
  track: "Backed up on every change",
  "track-collapsed": "Backed up, repeated tweaks grouped together",
  ignore: "Not tracked",
};

/* ------------------------------------------------------------------- icons */
const ICONS = {
  doc: '<path d="M6 2h8l4 4v16H6z"/><path d="M14 2v4h4"/>',
  monitor: '<rect x="3" y="4" width="18" height="12" rx="1"/><path d="M8 20h8M12 16v4"/>',
  gamepad: '<rect x="2" y="7" width="20" height="11" rx="4"/><path d="M7 11v3M5.5 12.5h3"/><circle cx="16" cy="11.5" r="1"/><circle cx="18.5" cy="14" r="1"/>',
  wheel: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.5"/><path d="M12 3v6.5M5 18l4.5-4M19 18l-4.5-4"/>',
  droplet: '<path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z"/>',
  sliders: '<path d="M4 8h10M18 8h2M4 16h2M10 16h10"/><circle cx="16" cy="8" r="2"/><circle cx="8" cy="16" r="2"/>',
  camera: '<rect x="3" y="7" width="18" height="13" rx="2"/><circle cx="12" cy="13" r="3.5"/><path d="M8 7l1.5-3h5L16 7"/>',
  shield: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>',
  shieldCheck: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
  alert: '<path d="M12 3l9 16H3z"/><path d="M12 10v4M12 17v.5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  folder: '<path d="M3 6h6l2 2h10v11H3z"/>',
  zip: '<path d="M6 2h12v20H6z"/><path d="M10 2v3M10 7v2M10 11v2M10 15h2v3h-2z"/>',
  bookmark: '<path d="M6 3h12v18l-6-4-6 4z"/>',
  rotate: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4 12H2M22 12h-2M5 5l1.4 1.4M17.6 17.6L19 19M19 5l-1.4 1.4M6.4 17.6L5 19"/>',
  moon: '<path d="M21 12.8A8 8 0 1 1 11.2 3a6 6 0 0 0 9.8 9.8z"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01"/>',
  timeline: '<path d="M5 4v16"/><circle cx="5" cy="8" r="2"/><circle cx="5" cy="16" r="2"/><path d="M9 8h11M9 16h11"/>',
};
function fileIconName(name) {
  const base = keyBase(name);
  if (base === "controls.cfg") return "gamepad";
  if (base === "joyCalib.yaml") return "wheel";
  if (base === "fueldata.ini") return "droplet";
  if (base === "core.ini") return "sliders";
  if (base === "camera.ini") return "camera";
  if (/^app\.ini$|^renderer/i.test(base)) return "monitor";
  return "doc";
}
function icon(name, cls) {
  return `<svg class="${cls || "ico"}" viewBox="0 0 24 24">${ICONS[name] || ICONS.doc}</svg>`;
}

/* ----------------------------------------------------------------- helpers */
function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
function prettyAction(name) {
  return String(name)
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
    .replace(/([a-zA-Z])([0-9])/g, "$1 $2");
}
function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const now = new Date();
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return "Today, " + time;
  const y = new Date(now); y.setDate(now.getDate() - 1);
  if (d.toDateString() === y.toDateString()) return "Yesterday, " + time;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) + ", " + time;
}
function $(sel, root) { return (root || document).querySelector(sel); }

const MAX_TOASTS = 4;
function toast(msg, kind) {
  const wrap = $("#toastWrap");
  // Cap the stack so rapid actions can't overflow the screen.
  while (wrap.children.length >= MAX_TOASTS) wrap.removeChild(wrap.firstChild);
  const t = document.createElement("div");
  t.className = "toast " + (kind || "");
  t.textContent = msg;
  wrap.appendChild(t);
  setTimeout(() => { t.style.transition = "opacity .3s"; t.style.opacity = "0";
    setTimeout(() => t.remove(), 320); }, 4200);
}

function confirmModal({ title, body, confirmLabel = "Confirm", danger = false }) {
  return new Promise((resolve) => {
    const root = $("#modalRoot");
    root.innerHTML = `
      <div class="modal-bg">
        <div class="modal">
          <h3>${esc(title)}</h3>
          <p>${body}</p>
          <div class="modal-actions">
            <button class="btn btn-ghost" data-act="cancel">Cancel</button>
            <button class="btn ${danger ? "btn-danger" : "btn-primary"}" data-act="ok">${esc(confirmLabel)}</button>
          </div>
        </div>
      </div>`;
    const done = (v) => { root.innerHTML = ""; resolve(v); };
    root.querySelector('[data-act="cancel"]').onclick = () => done(false);
    root.querySelector('[data-act="ok"]').onclick = () => done(true);
    root.querySelector(".modal-bg").onclick = (e) => { if (e.target.classList.contains("modal-bg")) done(false); };
  });
}
function promptModal({ title, body = "", placeholder = "", confirmLabel = "Save" }) {
  return new Promise((resolve) => {
    const root = $("#modalRoot");
    root.innerHTML = `
      <div class="modal-bg">
        <div class="modal">
          <h3>${esc(title)}</h3>
          ${body ? `<p>${body}</p>` : ""}
          <input class="modal-input" placeholder="${esc(placeholder)}" />
          <div class="modal-actions">
            <button class="btn btn-ghost" data-act="cancel">Cancel</button>
            <button class="btn btn-primary" data-act="ok">${esc(confirmLabel)}</button>
          </div>
        </div>
      </div>`;
    const input = root.querySelector(".modal-input");
    input.focus();
    const done = (v) => { root.innerHTML = ""; resolve(v); };
    root.querySelector('[data-act="cancel"]').onclick = () => done(null);
    root.querySelector('[data-act="ok"]').onclick = () => done(input.value.trim());
    input.onkeydown = (e) => { if (e.key === "Enter") done(input.value.trim()); if (e.key === "Escape") done(null); };
    root.querySelector(".modal-bg").onclick = (e) => { if (e.target.classList.contains("modal-bg")) done(null); };
  });
}

function infoModal({ title, bodyHtml }) {
  const root = $("#modalRoot");
  root.innerHTML = `
    <div class="modal-bg">
      <div class="modal" style="max-width:460px">
        <h3>${esc(title)}</h3>
        <div style="max-height:52vh;overflow:auto;margin-top:4px">${bodyHtml}</div>
        <div class="modal-actions"><button class="btn btn-primary" data-act="ok">Close</button></div>
      </div>
    </div>`;
  const done = () => { root.innerHTML = ""; };
  root.querySelector('[data-act="ok"]').onclick = done;
  root.querySelector(".modal-bg").onclick = (e) => { if (e.target.classList.contains("modal-bg")) done(); };
}

function noteModal(current) {
  return new Promise((resolve) => {
    const root = $("#modalRoot");
    root.innerHTML = `
      <div class="modal-bg"><div class="modal">
        <h3>Note for this backup</h3>
        <p>Jot down what you changed or how it felt — your tuning journal. It's searchable in Backup History.</p>
        <textarea class="modal-input" id="noteText" rows="4" style="resize:vertical;min-height:92px;font-family:inherit">${esc(current || "")}</textarea>
        <div class="modal-actions">
          <button class="btn btn-ghost" data-act="cancel">Cancel</button>
          <button class="btn btn-primary" data-act="ok">Save note</button>
        </div>
      </div></div>`;
    const ta = root.querySelector("#noteText"); ta.focus();
    const done = (v) => { root.innerHTML = ""; resolve(v); };
    root.querySelector('[data-act="cancel"]').onclick = () => done(null);
    root.querySelector('[data-act="ok"]').onclick = () => done(ta.value);
    root.querySelector(".modal-bg").onclick = (e) => { if (e.target.classList.contains("modal-bg")) done(null); };
  });
}

async function doEditNote(rev) {
  const s = state.history.find((x) => x.rev === rev);
  const text = await noteModal(s ? s.note : "");
  if (text === null) return;  // cancelled
  const r = await api("set_note", rev, text);
  if (!r.ok) { toast(r.error, "bad"); return; }
  if (s) s.note = r.note;
  toast(r.note ? "Note saved." : "Note removed.", "good");
  showBackupDetail(rev);
  renderHistBody();
}

function colorizeDiff(text) {
  return esc(text).split("\n").map((line) => {
    const t = line.trimStart();
    if (t.startsWith("[") || t.startsWith("@@") || t.startsWith("===")) return `<span class="hd">${line}</span>`;
    if (t.startsWith("+++") || t.startsWith("---")) return `<span class="hd">${line}</span>`;
    if (t.startsWith("+") || /\(added\)/.test(line)) return `<span class="add">${line}</span>`;
    if (t.startsWith("-") || /\(removed/.test(line)) return `<span class="del">${line}</span>`;
    return line;
  }).join("\n");
}

function fileChips(files) {
  return Object.keys(files).sort().map((n) =>
    `<span class="chip ${files[n]}">${icon(fileIconName(n))}${esc(fileLabel(n))}</span>`
  ).join("");
}

/* -------------------------------------------------------------------- state */
const state = {
  overview: null,
  view: "home",
  history: null,
  selectedRev: null,
  controls: null,
  devices: null,
  controlsFilter: "",
  controlsProfile: null,  // which iRacing control profile the Controls view shows
  showUnbound: false,
  settings: null,
  settingsQuery: "",
  historyView: "list",  // "list" | "timeline"
};

/* --------------------------------------------------------------- data loads */
async function loadOverview() {
  state.overview = await api("get_overview");
  return state.overview;
}

/* --------------------------------------------------------------------- views */
function setView(view) {
  state.view = view;
  document.querySelectorAll(".nav-item").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === view));
  render();
}

function render() {
  const v = state.view;
  if (v === "home") return renderHome();
  if (v === "history") return renderHistory();
  if (v === "profiles") return renderProfiles();
  if (v === "controls") return renderControls();
  if (v === "gamesettings") return renderGameSettings();
  if (v === "settings") return renderSettings();
}

/* ---- topbar / sim chip ---- */
function renderSimChip() {
  const o = state.overview;
  const chip = $("#simChip");
  if (!o || !o.ok) { chip.className = "sim-chip"; chip.innerHTML = ""; return; }
  if (o.simRunning) {
    chip.className = "sim-chip is-on";
    chip.innerHTML = `<span class="dot"></span> iRacing is running`;
  } else {
    chip.className = "sim-chip is-off";
    chip.innerHTML = `<span class="dot"></span> iRacing is closed`;
  }
}

/* ============================================================= HOME / DASH */
function renderHome() {
  const o = state.overview;
  const content = $("#content");
  if (!o || !o.ok) {
    content.innerHTML = setupError(o);
    $("#aside").innerHTML = "";
    return;
  }

  let hero;
  if (o.simRunning) {
    hero = heroCard("info", "clock", "iRacing is running",
      "Your changes are still being watched. Restoring older versions is paused until you close the sim and the iRacing UI — that keeps your live files safe.");
  } else if (o.snapshotCount === 0) {
    hero = heroCard("info", "shield", "Let’s protect your setup",
      "No backups yet. Make your first one and the app will remember this exact configuration so you can always get it back.",
      `<button class="btn btn-primary" data-action="backup">Back up now</button>`);
  } else if (o.pending.length > 0) {
    hero = heroCard("warn", "alert",
      `You have ${o.pending.length} unsaved ${o.pending.length === 1 ? "change" : "changes"}`,
      "Some settings changed since your last backup. Back up now to save this state, or open Backup History to see what changed.",
      `<button class="btn btn-primary" data-action="backup">Back up now</button>
       <button class="btn" data-action="view-pending">See what changed</button>`);
  } else {
    const when = o.latest ? fmtDate(o.latest.date) : "";
    hero = heroCard("good", "shieldCheck", "Everything is backed up",
      `Your iRacing settings are safe. Last backup: ${esc(when)}.`);
  }

  let cards = "";
  if (o.pending.length > 0) {
    cards += `<div class="card"><p class="section-label">Unsaved changes</p>
      ${o.pending.map(pendingRow).join("")}</div>`;
  }
  if (o.latest) {
    cards += `<div class="card">
      <div class="spread"><p class="section-label mt-0">Most recent backup</p>
        <button class="btn btn-sm btn-ghost" data-action="goto-history">View all →</button></div>
      ${backupSummary(o.latest)}</div>`;
  }
  cards += knownGoodCard(o);

  content.innerHTML = `
    ${updateBanner()}
    <div class="page-head">
      <h1 class="page-title">Home</h1>
      <p class="page-sub">A safety net for your iRacing configuration.</p>
    </div>
    ${hero}
    ${cards}`;

  renderHomeAside();
}

function pendingRow(p) {
  const kindWord = { added: "New file", modified: "Changed", deleted: "Removed" }[p.kind] || p.kind;
  const kindClass = p.kind;
  return `<div class="file-row">
    <div class="file-ico">${icon(fileIconName(p.name))}</div>
    <div><div class="file-name">${esc(fileLabel(p.name))}</div>
      <div class="file-desc">${esc(p.name)}</div></div>
    <div class="file-meta"><span class="chip ${kindClass}">${esc(kindWord)}</span></div>
  </div>`;
}

function backupSummary(s) {
  const ctx = s.contextLabel && s.contextLabel !== "manual edit"
    ? `<div class="tl-ctx">${icon("clock")} ${esc(s.contextLabel)}</div>` : "";
  const msg = s.message ? `<div class="tl-msg">“${esc(s.message)}”</div>` : "";
  const note = s.note ? `<div class="tl-note">${icon("doc")} ${esc(s.note)}</div>` : "";
  const tags = knownGoodChip(s) + (s.tags || []).map((t) => `<span class="chip tag-chip">${icon("bookmark")}${esc(t)}</span>`).join("");
  return `<div class="tl-top"><span class="tl-reason">${esc(triggerLabel(s.trigger))}</span>
      <span class="tl-date">${esc(fmtDate(s.date))}</span></div>
    ${ctx}${msg}${note}
    <div class="tl-files">${fileChips(s.files)}${tags}</div>`;
}

function knownGoodChip(s) {
  return s && s.knownGood
    ? `<span class="chip tag-chip">${icon("shieldCheck")} Known-good</span>` : "";
}

function knownGoodCard(o) {
  const kg = o.lastKnownGood;
  if (kg) {
    const when = kg.date ? fmtDate(kg.date) : "";
    const ctx = kg.contextLabel && kg.contextLabel !== "manual edit" ? ` · ${esc(kg.contextLabel)}` : "";
    return `<div class="card">
      <div class="spread"><p class="section-label mt-0">${icon("shieldCheck")} Known-good restore point</p>
        <button class="btn btn-sm btn-ghost" data-action="mark-known-good">Mark current as known-good</button></div>
      <div class="file-row" style="border:0;padding-bottom:0">
        <div class="file-ico">${icon("shieldCheck")}</div>
        <div><div class="file-name">${esc(kg.label)}</div>
          <div class="file-desc">Verified ${esc(when)}${esc(ctx)}</div></div>
        <div class="row-gap">
          <button class="btn btn-sm btn-primary" data-action="revert-known-good" data-tag="${esc(kg.tag)}">${icon("rotate")} Revert to this</button>
          <button class="btn btn-sm btn-ghost" data-action="delete-known-good" data-tag="${esc(kg.tag)}" title="Remove this mark">✕</button>
        </div>
      </div>
    </div>`;
  }
  if (o.snapshotCount === 0) return "";  // nothing to mark yet
  return `<div class="card">
    <p class="section-label mt-0">${icon("shieldCheck")} Known-good restore point</p>
    <p class="muted" style="font-size:12.5px;margin:6px 0 12px">When a setup feels right after a real session, mark it “known-good.” You’ll get a one-click button to jump straight back to it if a later change makes things worse.</p>
    <button class="btn btn-primary" data-action="mark-known-good">Mark current setup as known-good</button>
  </div>`;
}

function renderHomeAside() {
  const o = state.overview;
  const aside = $("#aside");
  const wState = o.watcher
    ? (o.watcher.paused
        ? `<span class="pill warn"><span class="dot"></span>Paused</span>`
        : `<span class="pill good"><span class="dot"></span>On — watching</span>`)
    : `<span class="pill"><span class="dot"></span>Off</span>`;
  const simState = o.simRunning
    ? `<span class="pill warn"><span class="dot"></span>Running</span>`
    : `<span class="pill good"><span class="dot"></span>Closed</span>`;

  aside.innerHTML = `
    <p class="section-label">System status</p>
    <div class="card" style="padding:14px">
      <div class="kv"><span class="k">iRacing</span><span class="v">${simState}</span></div>
      <div class="kv"><span class="k">Auto-backup</span><span class="v">${wState}</span></div>
      <div class="kv"><span class="k">Backups saved</span><span class="v">${o.snapshotCount}</span></div>
      <div class="kv"><span class="k">Files protected</span><span class="v">${o.protected.length}</span></div>
    </div>
    <p class="section-label" style="margin-top:18px">Shortcuts</p>
    <div class="card" style="padding:12px">
      <button class="btn btn-ghost" style="width:100%;justify-content:flex-start;margin-bottom:6px" data-action="open-iracing">${icon("folder")} Open my iRacing folder</button>
      <button class="btn btn-ghost" style="width:100%;justify-content:flex-start" data-action="goto-settings">${icon("sliders")} Settings &amp; auto-backup</button>
    </div>
    <p class="sidebar-hint" style="margin-top:14px">iRacing folder:<br>${esc(o.iracingDir)}</p>`;
}

function heroCard(kind, ic, title, text, actions) {
  return `<div class="hero ${kind}">
    <div class="hero-icon">${icon(ic)}</div>
    <div><h2 class="hero-title">${esc(title)}</h2><p class="hero-text">${esc(text)}</p></div>
    ${actions ? `<div class="hero-actions">${actions}</div>` : ""}
  </div>`;
}

function setupError(o) {
  const msg = (o && o.error) || "Something went wrong loading your configuration.";
  return `<div class="empty">${icon("alert")}
    <h3>Couldn’t open your settings</h3>
    <p>${esc(msg)}</p>
    <p class="muted" style="margin-top:12px">Config file: ${esc((o && o.configPath) || "")}</p>
  </div>`;
}

/* =============================================================== HISTORY */
async function renderHistory() {
  const content = $("#content");
  content.innerHTML = `<div class="loading">Loading your backups…</div>`;
  if (!state.history) {
    const r = await api("get_history", {});
    state.history = r.ok ? r.items : [];
  }
  const o = state.overview;
  if (!state.history.length) {
    content.innerHTML = `
      <div class="page-head"><h1 class="page-title">Backup History</h1></div>
      <div class="empty">${icon("clock")}
        <h3>No backups yet</h3>
        <p>Once you back up (or turn on auto-backup), every saved version of your settings will appear here as a timeline.</p>
        <div style="margin-top:18px"><button class="btn btn-primary" data-action="backup">Back up now</button></div>
      </div>`;
    $("#aside").innerHTML = "";
    return;
  }

  const v = state.historyView;
  const seg = `<div class="seg">
    <button class="${v === "list" ? "on" : ""}" data-action="hview-list">${icon("list")} List</button>
    <button class="${v === "timeline" ? "on" : ""}" data-action="hview-timeline">${icon("timeline")} Timeline</button>
    <button class="${v === "sessions" ? "on" : ""}" data-action="hview-sessions">${icon("clock")} Sessions</button>
  </div>`;
  content.innerHTML = `
    <div class="page-head spread">
      <div><h1 class="page-title">Backup History</h1>
      <p class="page-sub">Every saved version of your settings. Click one to see what changed or to restore it.</p></div>
      <div class="row-gap">${seg}
        <button class="btn btn-sm" data-action="compare-now">${icon("rotate")} What’s changed?</button></div>
    </div>
    ${v === "list" ? compareCard() : ""}
    <div id="histBody"></div>`;
  renderHistBody();
  if (state.selectedRev) showBackupDetail(state.selectedRev);
  else asideHint("Select a backup to see what changed and restore it.");
}

function renderHistBody() {
  const box = $("#histBody");
  if (!box) return;
  if (state.historyView === "sessions") { renderSessions(); return; }
  if (state.historyView === "timeline") {
    box.innerHTML = timelineChartHtml();
    box.querySelectorAll(".tlc-evt").forEach((el) =>
      el.addEventListener("click", () => {
        state.selectedRev = el.dataset.rev; renderHistBody(); showBackupDetail(el.dataset.rev);
      }));
    return;
  }
  box.innerHTML = `<input class="search" id="histSearch" placeholder="Filter by car, track, file, or words in the note…">
    <div class="timeline" id="timeline"></div>`;
  $("#histSearch").addEventListener("input", (e) => renderTimeline(e.target.value));
  renderTimeline("");
}

/* ---- configuration timeline (chart) ---- */
function startOfDay(d) { const x = new Date(d); x.setHours(0, 0, 0, 0); return x; }
function timeOnly(iso) {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "" : d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}
function dayLabelShort(d) { return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }); }
function dayLabelLong(iso) {
  const d = new Date(iso), now = new Date();
  if (startOfDay(d).getTime() === startOfDay(now).getTime()) return "Today";
  const y = new Date(now); y.setDate(now.getDate() - 1);
  if (startOfDay(d).getTime() === startOfDay(y).getTime()) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}
function eventKind(s) {
  if (s.knownGood) return { key: "known", label: "Known-good", color: "var(--good)", icon: "shieldCheck" };
  if (s.tags && s.tags.length) return { key: "setup", label: "Saved Setup", color: "var(--bind-button)", icon: "bookmark" };
  if (s.trigger === "restore" || s.trigger === "pre_restore") return { key: "restore", label: "Restore", color: "var(--warn)", icon: "rotate" };
  if (s.trigger === "manual") return { key: "manual", label: "Manual backup", color: "var(--accent)", icon: "shield" };
  if (s.trigger === "sim_exit") return { key: "session", label: "After a session", color: "var(--bind-axis)", icon: "clock" };
  return { key: "auto", label: "Auto backup", color: "var(--text-faint)", icon: "clock" };
}
function tlcSummary(s) {
  if (s.message) return esc(s.message);
  const labels = [...new Set(Object.keys(s.files || {}).map(fileLabel))];
  if (!labels.length) return "no file changes";
  return esc(labels.slice(0, 2).join(", ")) + (labels.length > 2 ? ` +${labels.length - 2} more` : "");
}
function timelineEventHtml(s) {
  const k = eventKind(s);
  return `<div class="tlc-evt ${s.rev === state.selectedRev ? "selected" : ""}" data-rev="${esc(s.rev)}">
    <div class="tlc-evt-dot" style="background:${k.color}"></div>
    <div class="tlc-evt-time">${esc(timeOnly(s.date))}</div>
    <div style="flex:1">
      <div class="tlc-evt-type" style="color:${k.color}">${icon(k.icon)} ${esc(k.label)}</div>
      <div class="tlc-evt-sum">${tlcSummary(s)}</div>
    </div></div>`;
}
function timelineChartHtml() {
  const items = state.history;  // newest first
  if (!items.length) return `<p class="muted">No backups yet.</p>`;
  const byDay = new Map();
  items.forEach((s) => {
    const t = startOfDay(new Date(s.date)).getTime();
    if (!byDay.has(t)) byDay.set(t, []);
    byDay.get(t).push(s);
  });
  const dayMs = 86400000;
  const first = startOfDay(new Date(items[items.length - 1].date)).getTime();
  const today = startOfDay(new Date()).getTime();
  const spanDays = Math.max(1, Math.round((today - first) / dayMs) + 1);
  const N = Math.min(21, Math.max(7, spanDays));  // always show at least a week
  const counts = [];
  for (let i = N - 1; i >= 0; i--) {
    const t = today - i * dayMs;
    counts.push({ t, c: (byDay.get(t) || []).length });
  }
  const max = Math.max(1, ...counts.map((d) => d.c));
  const bars = counts.map((d) =>
    `<div class="tlc-bar ${d.c ? "" : "empty"}" title="${esc(dayLabelShort(new Date(d.t)))}: ${d.c} backup${d.c === 1 ? "" : "s"}"><i style="height:${d.c ? Math.round(d.c / max * 100) : 0}%"></i></div>`).join("");
  const axis = `<span>${esc(dayLabelShort(new Date(counts[0].t)))}</span><span>${esc(dayLabelShort(new Date(counts[counts.length - 1].t)))}</span>`;
  const kinds = {};
  items.forEach((s) => { const k = eventKind(s); kinds[k.key] = k; });
  const legend = Object.values(kinds).map((k) =>
    `<span class="tlc-leg"><i style="background:${k.color}"></i>${esc(k.label)}</span>`).join("");
  let events = "";
  for (const [, dayItems] of byDay) {
    events += `<div class="tlc-day"><span class="tlc-day-label">${esc(dayLabelLong(dayItems[0].date))}</span><span class="muted" style="font-size:12px">${dayItems.length} event${dayItems.length === 1 ? "" : "s"}</span></div>`;
    events += `<div class="tlc-events-day">${dayItems.map(timelineEventHtml).join("")}</div>`;
  }
  return `
    <p class="tlc-summary"><b>${items.length}</b> backup${items.length === 1 ? "" : "s"} over <b>${spanDays}</b> day${spanDays === 1 ? "" : "s"}.</p>
    <div class="card"><p class="section-label mt-0">Backup activity (last ${N} days)</p>
      <div class="tlc-bars">${bars}</div><div class="tlc-axis">${axis}</div></div>
    <div class="tlc-legend">${legend}</div>
    <div>${events}</div>`;
}

function renderTimeline(q) {
  q = (q || "").toLowerCase();
  const items = state.history.filter((s) => {
    if (!q) return true;
    const hay = [s.message, s.note, s.car, s.track, triggerLabel(s.trigger),
      ...Object.keys(s.files).map(fileLabel), ...Object.keys(s.files), ...s.tags].join(" ").toLowerCase();
    return hay.includes(q);
  });
  const tl = $("#timeline");
  if (!items.length) { tl.innerHTML = `<p class="muted" style="padding:14px">No backups match “${esc(q)}”.</p>`; return; }
  tl.innerHTML = items.map((s) => `
    <div class="tl-item ${s.rev === state.selectedRev ? "selected" : ""}" data-rev="${esc(s.rev)}">
      <div class="tl-dot"></div>
      <div class="tl-card">${backupSummary(s)}</div>
    </div>`).join("");
  tl.querySelectorAll(".tl-item").forEach((el) =>
    el.addEventListener("click", () => { state.selectedRev = el.dataset.rev; renderTimeline(q); showBackupDetail(el.dataset.rev); }));
}

function asideHint(text) {
  $("#aside").innerHTML = `<div class="empty" style="padding:40px 12px">${icon("clock")}<p>${esc(text)}</p></div>`;
}

async function showBackupDetail(rev) {
  const s = state.history.find((x) => x.rev === rev);
  if (!s) return;
  const aside = $("#aside");
  const tags = knownGoodChip(s) + (s.tags || []).map((t) => `<span class="chip tag-chip">${icon("bookmark")}${esc(t)}</span>`).join("");
  aside.innerHTML = `
    <p class="section-label">Backup details</p>
    <div class="card" style="padding:14px">
      <div style="font-weight:650;margin-bottom:4px">${esc(triggerLabel(s.trigger))}</div>
      <div class="muted" style="font-size:12.5px">${esc(fmtDate(s.date))}</div>
      ${s.contextLabel && s.contextLabel !== "manual edit" ? `<div class="tl-ctx" style="margin-top:8px">${icon("clock")} ${esc(s.contextLabel)}</div>` : ""}
      ${s.message ? `<div class="tl-msg">“${esc(s.message)}”</div>` : ""}
      <div class="tl-files" style="margin-top:10px">${fileChips(s.files)}${tags}</div>
    </div>
    <div class="row-gap" style="margin-top:12px">
      <button class="btn btn-sm" data-action="bookmark" data-rev="${esc(rev)}">${icon("bookmark")} Save as setup</button>
      <button class="btn btn-sm" data-action="export" data-rev="${esc(rev)}">${icon("zip")} Export…</button>
    </div>
    <p class="section-label" style="margin-top:18px">${icon("doc")} Your note</p>
    <div class="card" style="padding:12px 14px">
      ${s.note ? `<div style="white-space:pre-wrap;font-size:13px;line-height:1.5">${esc(s.note)}</div>`
               : `<span class="muted" style="font-size:12.5px">No note yet — jot down what you changed or how it felt.</span>`}
      <button class="btn btn-sm btn-ghost" style="margin-top:9px" data-action="edit-note" data-rev="${esc(rev)}">${icon("doc")} ${s.note ? "Edit note" : "Add a note"}</button>
    </div>
    <p class="section-label" style="margin-top:18px">What changed in this backup</p>
    <div id="changeBody"><div class="loading">Comparing…</div></div>
    <p class="section-label" style="margin-top:18px">Restore</p>
    <div class="card" style="padding:14px">
      <p class="muted mt-0" style="font-size:12.5px">Put your live iRacing files back to how they were in this backup. A safety backup is made first, so this is always reversible.</p>
      <div id="restoreButtons" style="margin-top:10px"></div>
    </div>`;

  // restore buttons (per file + all)
  const names = Object.keys(s.files).filter((n) => s.files[n] !== "deleted").sort();
  const rb = $("#restoreButtons", aside);
  rb.innerHTML =
    names.map((n) => `<button class="btn btn-sm btn-ghost" style="margin:0 6px 6px 0" data-action="restore-file" data-rev="${esc(rev)}" data-file="${esc(n)}">${icon("rotate")} ${esc(fileLabel(n))}</button>`).join("") ||
    `<span class="muted">This backup only recorded deletions.</span>`;

  // diff
  const r = await api("get_changes", rev);
  const body = $("#changeBody", aside);
  if (!r.ok) { body.innerHTML = `<p class="muted">Couldn’t compare: ${esc(r.error)}</p>`; return; }
  if (!r.hasParent) { body.innerHTML = `<p class="muted">This is your very first backup — everything here was saved for the first time.</p>`; return; }
  if (!r.files.length) { body.innerHTML = `<p class="muted">No readable differences (only internal metadata changed).</p>`; return; }
  body.innerHTML = r.files.map((f) => `
    <div class="diff-file"><h4>${esc(fileLabel(f.name))}</h4>
      <div class="diff-body">${colorizeDiff(f.body)}</div></div>`).join("");
}

async function compareNow() {
  const aside = $("#aside");
  state.selectedRev = null;
  renderTimeline($("#histSearch") ? $("#histSearch").value : "");
  aside.innerHTML = `<p class="section-label">Changes since your last backup</p><div id="changeBody"><div class="loading">Comparing…</div></div>`;
  const r = await api("get_pending_diff");
  const body = $("#changeBody", aside);
  if (!r.ok) { body.innerHTML = `<p class="muted">${esc(r.error)}</p>`; return; }
  if (!r.files.length) { body.innerHTML = `<div class="empty" style="padding:30px 10px">${icon("shieldCheck")}<p>Nothing has changed since your last backup.</p></div>`; return; }
  body.innerHTML = `<div class="card" style="padding:14px;margin-bottom:12px"><div class="spread"><span class="muted" style="font-size:12.5px">${r.files.length} file(s) differ from your last backup</span><button class="btn btn-sm btn-primary" data-action="backup">Back up now</button></div></div>` +
    r.files.map((f) => `<div class="diff-file"><h4>${esc(fileLabel(f.name))}</h4><div class="diff-body">${colorizeDiff(f.body)}</div></div>`).join("");
}

function compareCard() {
  const optList = (sel) => state.history.map((s, i) =>
    `<option value="${esc(s.rev)}"${i === sel ? " selected" : ""}>${esc(triggerLabel(s.trigger))} — ${esc(fmtDate(s.date))}${s.tags.length ? " [" + s.tags.map(esc).join(", ") + "]" : ""}</option>`).join("");
  const aSel = state.history.length > 1 ? 1 : 0;
  return `<div class="card" style="margin-bottom:16px">
    <p class="section-label mt-0">Compare two backups</p>
    <div class="row-gap" style="align-items:flex-end">
      <label style="flex:1;min-width:200px"><div class="file-desc" style="margin-bottom:4px">This backup</div>
        <select class="search" id="cmpA" style="margin-bottom:0">${optList(aSel)}</select></label>
      <label style="flex:1;min-width:200px"><div class="file-desc" style="margin-bottom:4px">…compared with</div>
        <select class="search" id="cmpB" style="margin-bottom:0"><option value="__live__">Now (live folder)</option>${optList(0)}</select></label>
      <button class="btn btn-primary" data-action="run-compare">Compare</button>
    </div>
    <div id="cmpResult"></div>
  </div>`;
}

async function doRunCompare() {
  const aEl = $("#cmpA"), bEl = $("#cmpB");
  if (!aEl || !bEl) return;
  const a = aEl.value, b = bEl.value;
  const la = aEl.selectedOptions[0].textContent.trim();
  const lb = bEl.selectedOptions[0].textContent.trim();
  const box = $("#cmpResult");
  box.innerHTML = `<div class="loading" style="padding:16px">Comparing…</div>`;
  const r = await api("get_comparison", a, b, la, lb);
  if (!r.ok) { box.innerHTML = `<p class="muted" style="margin-top:10px">${esc(r.error)}</p>`; return; }
  state.lastCompare = { a, b, la, lb };
  if (!r.files.length) {
    box.innerHTML = `<div class="empty" style="padding:24px 10px">${icon("shieldCheck")}<p>No differences between these two.</p></div>`;
    return;
  }
  box.innerHTML = `
    <div class="spread" style="margin:16px 0 8px">
      <div class="section-label mt-0">${r.files.length} file${r.files.length > 1 ? "s" : ""} changed</div>
      <button class="btn btn-sm btn-primary" data-action="export-compare">${icon("doc")} Export PDF</button>
    </div>
    ${r.files.map((f) => `<div class="diff-file"><h4>${esc(fileLabel(f.name))}</h4><div class="diff-body">${colorizeDiff(f.body)}</div></div>`).join("")}`;
}

async function doExportCompare() {
  const c = state.lastCompare;
  if (!c) return;
  toast("Building PDF…");
  const r = await api("export_comparison_pdf", c.a, c.b, c.la, c.lb);
  if (!r.ok) { toast(r.error, "bad"); return; }
  if (r.cancelled) return;
  toast(r.message || "Saved PDF.", "good");
}

/* ====================================================== CONTROLS & DEVICES */
async function renderControls() {
  const content = $("#content");
  content.innerHTML = `<div class="loading">Reading your controls…</div>`;
  // Always re-read the live controls.cfg: it changes outside the app (you rebind
  // in iRacing), so a cached copy would show stale bindings/conflicts.
  state.controls = await api("get_controls", null, state.controlsProfile);
  const c = state.controls;
  // Sync the selection to whatever profile the backend actually showed.
  state.controlsProfile = (c && c.profile) || null;
  state.devices = await api("get_devices", state.controlsProfile);

  if (!c.ok || !c.available) {
    content.innerHTML = `
      <div class="page-head"><h1 class="page-title">Controls &amp; Devices</h1></div>
      <div class="empty">${icon("gamepad")}<h3>Controls not available</h3>
        <p>${esc((c && c.error) || "Couldn’t read your controls file.")}</p></div>`;
    renderDevicesAside();
    return;
  }

  // A picker appears once iRacing has more than one control profile, so you can
  // browse any profile's bindings here without switching the active one in-sim.
  const profileSelect = (c.profiles && c.profiles.length > 1)
    ? `<select class="search" id="ctlProfile" style="max-width:200px;margin:0" title="View a control profile">
        ${c.profiles.map((p) => `<option value="${esc(p)}"${p === c.profile ? " selected" : ""}>${esc(p)}${p === c.activeProfile ? " (active)" : ""}</option>`).join("")}
      </select>` : "";
  content.innerHTML = `
    <div class="page-head spread"><div><h1 class="page-title">Controls &amp; Devices</h1>
      <p class="page-sub">How your wheel, pedals, and keyboard are mapped in iRacing. This view is read-only.</p></div>
      <div class="row-gap">${profileSelect}
        <button class="btn btn-sm" data-action="binding-inventory">${icon("doc")} Inventory</button>
        <button class="btn btn-sm" data-action="refresh-controls">${icon("rotate")} Refresh</button></div></div>
    ${savedStateBanner(c)}
    <div class="card" style="padding:14px;margin-bottom:16px;display:flex;gap:12px;align-items:flex-start">
      ${icon("alert", "ico")}
      <p class="muted mt-0" style="font-size:12.5px">${esc(c.ffbNote)}</p>
    </div>
    ${conflictBanner(c.conflicts)}
    <div class="card" style="margin-bottom:16px">
      <p class="section-label mt-0">Identify a control — what does this do?</p>
      <div class="row-gap">
        <div id="keycap" class="keycap" tabindex="0">Click here, then press a key…</div>
        <span class="muted" style="font-size:12.5px">or type</span>
        <input class="search" id="identifyInput" style="margin-bottom:0;max-width:180px" placeholder="Btn 5, Axis 3, Alt+P">
        <button class="btn btn-sm" data-action="identify">Identify</button>
      </div>
      <div id="identifyResult"></div>
    </div>
    <input class="search" id="ctlSearch" placeholder="Search controls (e.g. throttle, pit, shift)…" value="${esc(state.controlsFilter)}">
    <div class="card" style="padding:14px">
      <div class="spread" style="margin-bottom:10px">
        <span class="muted" style="font-size:12.5px">${c.boundCount} of ${c.bindings.length} controls are assigned · click any control to see when it last changed</span>
        <label class="row-gap" style="font-size:12.5px;cursor:pointer"><input type="checkbox" id="showUnbound" ${state.showUnbound ? "checked" : ""}> Show unassigned</label>
      </div>
      <table class="ctl-table"><thead><tr><th>Control</th><th>Assigned to</th><th>Device</th></tr></thead>
      <tbody id="ctlBody"></tbody></table>
    </div>`;

  $("#ctlSearch").addEventListener("input", (e) => { state.controlsFilter = e.target.value; renderCtlRows(); });
  $("#showUnbound").addEventListener("change", (e) => { state.showUnbound = e.target.checked; renderCtlRows(); });
  if ($("#ctlProfile")) $("#ctlProfile").addEventListener("change", (e) => { state.controlsProfile = e.target.value; renderControls(); });
  const cap = $("#keycap");
  cap.addEventListener("keydown", (ev) => { ev.preventDefault(); const q = eventToQuery(ev); if (q) doIdentify(q); });
  cap.addEventListener("focus", () => { cap.textContent = "Press any key…"; });
  cap.addEventListener("blur", () => { cap.textContent = "Click here, then press a key…"; });
  $("#identifyInput").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); doIdentify(e.target.value); } });
  renderCtlRows();
  renderDevicesAside();
}

function jsKeyName(ev) {
  if (ev.key === "CapsLock") return "capslock";
  if (["Control", "Shift", "Alt", "Meta", "OS"].includes(ev.key)) return null;  // modifier alone
  const code = ev.code || "";
  if (code.startsWith("Numpad")) {
    const np = { NumpadAdd: "numpad+", NumpadSubtract: "numpad-", NumpadMultiply: "numpad*",
                 NumpadDivide: "numpad/", NumpadDecimal: "numpad." };
    if (np[code]) return np[code];
    const d = code.slice(6);
    if (/^\d$/.test(d)) return "numpad" + d;
  }
  const named = {
    " ": "space", Enter: "enter", Escape: "esc", Tab: "tab", Backspace: "backspace",
    Delete: "delete", Insert: "insert", Home: "home", End: "end", PageUp: "pageup",
    PageDown: "pagedown", ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up",
    ArrowDown: "down", NumLock: "numlock", Pause: "pause", PrintScreen: "printscreen",
  };
  if (named[ev.key]) return named[ev.key];
  if (/^F\d{1,2}$/.test(ev.key)) return ev.key.toLowerCase();
  return ev.key.toLowerCase();   // letters, digits, punctuation; backend rejects unknowns
}

function eventToQuery(ev) {
  const key = jsKeyName(ev);
  if (!key) return null;
  const mods = [];
  if (ev.ctrlKey) mods.push("ctrl");
  if (ev.shiftKey) mods.push("shift");
  if (ev.altKey) mods.push("alt");
  return [...mods, key].join("+");
}

function blameEventsHtml(events) {
  return events.map((ev, i) => {
    const ctx = ev.contextLabel && ev.contextLabel !== "manual edit" ? ` · ${esc(ev.contextLabel)}` : "";
    const note = ev.message ? `<div class="tl-msg" style="margin-top:3px">“${esc(ev.message)}”</div>` : "";
    const now = i === 0 ? `<span class="chip tag-chip" style="margin-left:6px">now</span>` : "";
    const val = ev.value == null
      ? `<span class="bind unbound">(not set)</span>`
      : `<span class="bind key">${esc(ev.value)}</span>`;
    return `<div class="file-row" style="align-items:flex-start">
      <div class="file-ico">${icon("clock")}</div>
      <div style="flex:1">
        <div class="file-name">${val}${now}</div>
        <div class="file-desc">${esc(fmtDate(ev.date))} · ${esc(triggerLabel(ev.trigger))}${ctx}</div>${note}
      </div></div>`;
  }).join("");
}

function showBlame(title, r) {
  if (!r.ok) { toast(r.error || "Couldn't load this item's history.", "bad"); return; }
  if (!r.events.length) {
    infoModal({ title, bodyHtml: `<p class="muted">No saved history yet. Once you back up after a change, you'll see when it changed here.</p>` });
    return;
  }
  const lead = r.events.length === 1
    ? `<p class="muted" style="font-size:12.5px;margin:0 0 12px">Set once and unchanged since.</p>`
    : `<p class="muted" style="font-size:12.5px;margin:0 0 12px">${r.events.length} changes on record — newest first.</p>`;
  infoModal({ title, bodyHtml: lead + blameEventsHtml(r.events) });
}

async function doBlameControl(action) {
  showBlame(`${prettyAction(action)} — history`, await api("blame_control", action, state.controlsProfile));
}

function doBindingInventory() {
  const c = state.controls;
  if (!c || !c.bindings) { toast("Open Controls & Devices first.", "bad"); return; }
  const bound = c.bindings.filter((b) => b.kind !== "unbound");
  if (!bound.length) { infoModal({ title: "Binding inventory", bodyHtml: `<p class="muted">No controls are assigned yet.</p>` }); return; }
  const groups = {};
  bound.forEach((b) => { const d = b.device || "Other"; (groups[d] = groups[d] || []).push(b); });
  // devices first, keyboard last
  const order = Object.keys(groups).sort((a, b) => (a === "Keyboard") - (b === "Keyboard") || a.localeCompare(b));
  const sortRows = (arr) => arr.slice().sort((a, b) => prettyAction(a.action).localeCompare(prettyAction(b.action)));
  const profLabel = c.profile ? ` — ${c.profile} profile` : "";
  let text = `iRacing binding inventory${profLabel}\n${bound.length} assignments\n`;
  order.forEach((d) => { text += `\n[${d}]\n`; sortRows(groups[d]).forEach((b) => { text += `  ${prettyAction(b.action)}: ${b.display}\n`; }); });
  state.inventoryText = text;
  const html = `
    <div class="spread" style="margin-bottom:12px">
      <span class="muted" style="font-size:12.5px">${bound.length} assignments · ${order.length} device${order.length === 1 ? "" : "s"}${c.profile ? " · " + esc(c.profile) + " profile" : ""}</span>
      <button class="btn btn-sm" data-action="copy-inventory">${icon("doc")} Copy</button>
    </div>
    ${order.map((d) => `
      <div style="margin-bottom:14px">
        <div class="section-label" style="margin-bottom:6px">${icon(d === "Keyboard" ? "doc" : "gamepad")} ${esc(d)} <span class="muted" style="font-weight:500;letter-spacing:0;text-transform:none">· ${groups[d].length}</span></div>
        ${sortRows(groups[d]).map((b) => `
          <div class="file-row" style="padding:6px 2px"><div style="flex:1">${esc(prettyAction(b.action))}</div>
            <span class="bind ${b.kind}">${esc(b.display)}</span></div>`).join("")}
      </div>`).join("")}`;
  infoModal({ title: "Binding inventory", bodyHtml: html });
}

async function copyInventory() {
  try { await navigator.clipboard.writeText(state.inventoryText || ""); toast("Inventory copied to clipboard.", "good"); }
  catch (e) { toast("Couldn't access the clipboard — select the text to copy it.", "bad"); }
}

async function doBlameSetting(file, section, key) {
  showBlame(`${key} — history`, await api("blame_setting", file, section, key));
}

async function doIdentify(query) {
  const out = $("#identifyResult");
  if (!out || !query || !String(query).trim()) return;
  const r = await api("identify_input", String(query).trim(), state.controlsProfile);
  if (!r.ok) { out.innerHTML = `<p class="muted" style="margin-top:10px">${esc(r.error)}</p>`; return; }
  if (r.free) {
    out.innerHTML = `<div style="margin-top:12px"><span class="bind ${r.kind}">${esc(r.label)}</span>
      <span class="pill good" style="margin-left:8px"><span class="dot"></span>Free — not bound to anything</span></div>`;
    return;
  }
  out.innerHTML = `<div style="margin-top:12px"><span class="bind ${r.kind}">${esc(r.label)}</span> <span class="muted">is bound to:</span>
    ${r.matches.map((m) => `<div class="file-row" style="padding:8px 0"><div class="file-name">${esc(prettyAction(m.action))}</div><div class="file-meta ctl-device">${esc(m.device)}</div></div>`).join("")}</div>`;
}

function savedStateBanner(c) {
  // Viewing a historical backup, not the live file.
  if (c.source && c.source !== "live") {
    return `<div class="card" style="padding:11px 14px;margin-bottom:16px;font-size:12.5px">
      <span class="muted">${icon("clock")} Viewing a saved backup (${esc(c.source)}), not your live controls.</span></div>`;
  }
  const when = c.lastSaved ? fmtDate(c.lastSaved) : "an unknown time";
  const isActive = !c.profile || c.profile === c.activeProfile;
  const prof = !c.profile ? ""
    : isActive
      ? `<span class="muted" style="font-size:12px">${icon("bookmark")} Active iRacing control profile: <strong>${esc(c.profile)}</strong></span><br>`
      : `<span style="font-size:12px;color:var(--warn)">${icon("bookmark")} Viewing the <strong>${esc(c.profile)}</strong> profile — not the one active in iRacing (active: <strong>${esc(c.activeProfile || "—")}</strong>)</span><br>`;
  // iRacing buffers binding changes and only writes controls.cfg when the sim
  // fully exits, so while it's running the file (and this view) can lag.
  if (c.simRunning) {
    return `<div class="card conflict-banner" style="margin-bottom:16px;padding:13px 15px">
      <p class="section-label mt-0" style="color:var(--warn);margin-bottom:6px">${icon("alert")} iRacing is running — recent changes may not be saved yet</p>
      <p class="muted mt-0" style="font-size:12.5px;line-height:1.5">${prof}This shows what iRacing last <strong>saved</strong> to your controls file (${esc(when)}). iRacing keeps new key &amp; button changes in memory and only writes them to the file when you <strong>fully exit the sim to the desktop</strong>. If you rebound something and don't see it here, close iRacing completely, then click Refresh.</p>
    </div>`;
  }
  return `<div class="card" style="padding:11px 14px;margin-bottom:16px;font-size:12px">
    <span class="muted">${prof}${icon("clock")} Showing iRacing's last saved controls — updated ${esc(when)}. After you rebind in iRacing, exit the sim so it saves the file, then click Refresh.</span></div>`;
}

function conflictBanner(conflicts) {
  if (!conflicts || !conflicts.length) return "";
  const rows = conflicts.map((x) =>
    `<div style="margin:5px 0;font-size:12.5px"><span class="bind ${x.kind}">${esc(x.label)}</span>
      <span class="muted">→</span> ${x.actions.map((a) => esc(prettyAction(a))).join(", ")}</div>`).join("");
  return `<div class="card conflict-banner" style="margin-bottom:16px">
    <p class="section-label" style="color:var(--warn);margin-bottom:8px">${icon("alert")} ${conflicts.length} binding conflict${conflicts.length > 1 ? "s" : ""} — one input is assigned to multiple actions</p>
    ${rows}</div>`;
}

function conflictActionSet() {
  return new Set(((state.controls && state.controls.conflicts) || []).flatMap((x) => x.actions));
}

function renderCtlRows() {
  const c = state.controls;
  const q = state.controlsFilter.toLowerCase();
  const conflicting = conflictActionSet();
  let rows = c.bindings.slice();
  if (!state.showUnbound) rows = rows.filter((b) => b.kind !== "unbound");
  if (q) rows = rows.filter((b) => prettyAction(b.action).toLowerCase().includes(q) || b.action.toLowerCase().includes(q) || (b.display || "").toLowerCase().includes(q));
  // bound first
  rows.sort((a, b) => (a.kind === "unbound") - (b.kind === "unbound"));
  const body = $("#ctlBody");
  if (!rows.length) { body.innerHTML = `<tr><td colspan="3" class="muted" style="padding:18px">No controls match your search.</td></tr>`; return; }
  body.innerHTML = rows.map((b) => {
    const bad = conflicting.has(b.action);
    return `<tr class="ctl-row ${bad ? "conflict" : ""}" data-action="blame-control" data-name="${esc(b.action)}" style="cursor:pointer" title="See when this control last changed">
      <td class="ctl-action">${esc(prettyAction(b.action))}${bad ? `<span class="conflict-badge">conflict</span>` : ""}</td>
      <td><span class="bind ${b.kind}">${esc(b.display)}</span></td>
      <td class="ctl-device">${esc(b.device || "—")} ${icon("clock", "ico ctl-hist")}</td></tr>`;
  }).join("");
}

function presencePill(p) {
  if (p === "connected") return `<span class="pill good"><span class="dot"></span>Connected</span>`;
  if (p === "moved-port") return `<span class="pill warn"><span class="dot"></span>Different USB port</span>`;
  return `<span class="pill bad"><span class="dot"></span>Not connected</span>`;
}
function deviceName(d) {
  if (d.name) return d.name;
  const n = d.note && d.note.includes(" - ") ? d.note.split(" - ")[1] : null;
  return n || "Game controller";
}

function renderDevicesAside() {
  const d = state.devices;
  const aside = $("#aside");
  if (!d || !d.ok) { aside.innerHTML = `<p class="muted">Devices unavailable.</p>`; return; }

  const connected = d.connected.length
    ? d.connected.map((x) => `<div class="dev"><div class="dev-name">${icon("gamepad")} ${esc(deviceName(x))}</div>
        ${x.note ? `<div class="dev-note">${esc(x.note)}</div>` : ""}
        <div class="dev-guid">${esc(x.instanceGuid)}</div></div>`).join("")
    : `<p class="muted" style="font-size:12.5px">${esc(d.enumError || "No game controllers detected.")}</p>`;

  const referenced = d.referenced.length
    ? d.referenced.map((x) => `<div class="dev"><div class="dev-name">${icon("wheel")} ${esc(deviceName(x))}</div>
        <div style="margin-top:6px">${presencePill(x.presence)}</div>
        ${x.presence === "moved-port" && x.suggestedNewGuid
          ? `<button class="btn btn-sm btn-primary" style="margin-top:9px;width:100%;justify-content:center"
               data-action="remap" data-old="${esc(x.instanceGuid)}" data-new="${esc(x.suggestedNewGuid)}">
               ${icon("rotate")} Re-map to connected device</button>`
          : ""}
        <div class="dev-guid">${esc(x.instanceGuid)}</div></div>`).join("")
    : `<p class="muted" style="font-size:12.5px">Your controls file is keyboard-only.</p>`;

  aside.innerHTML = `
    <p class="section-label">Connected now</p>${connected}
    <p class="section-label" style="margin-top:18px">Used in your controls</p>${referenced}`;
}

/* ---- driving sessions (session change report) ---- */
async function renderSessions() {
  const box = $("#histBody");
  if (!box) return;
  box.innerHTML = `<div class="loading">Grouping your driving sessions…</div>`;
  const r = await api("list_sessions");
  state.sessions = r.ok ? r.items : [];
  if (!state.sessions.length) {
    box.innerHTML = `<div class="empty">${icon("clock")}<h3>No driving sessions yet</h3>
      <p>When you drive with iRacing running, the app groups the changes you made during each session here — by car and track. Have a session, then check back.</p></div>`;
    return;
  }
  box.innerHTML = state.sessions.map((s, i) => sessionCard(s, i)).join("");
}

function sessionWhen(s) {
  const start = fmtDate(s.start), endT = timeOnly(s.end);
  if (new Date(s.start).toDateString() === new Date(s.end).toDateString() && timeOnly(s.start) !== endT)
    return `${start}–${endT}`;
  return start;
}

function sessionCard(s, i) {
  const ctx = (s.car || s.track) ? [s.car, s.track].filter(Boolean).join(" @ ") : "Sim session (car/track unknown)";
  const files = [...new Set((s.files || []).map(fileLabel))];
  const sub = `${esc(sessionWhen(s))} · ${s.count} backup${s.count === 1 ? "" : "s"}${files.length ? " · " + esc(files.slice(0, 3).join(", ")) : ""}`;
  return `<div class="card" style="margin-bottom:12px;padding:0">
    <div class="spread" data-action="toggle-session" data-i="${i}" style="cursor:pointer;padding:16px 18px">
      <div><div style="font-weight:650;font-size:15px">${icon("clock")} ${esc(ctx)}</div>
        <div class="muted" style="font-size:12.5px;margin-top:3px">${sub}</div></div>
      <span class="btn btn-sm btn-ghost">View changes</span>
    </div>
    <div id="sess-${i}" style="padding:0 18px"></div>
  </div>`;
}

async function doSessionDetail(i) {
  const s = (state.sessions || [])[i];
  const box = document.getElementById("sess-" + i);
  if (!s || !box) return;
  if (box.dataset.open) { box.innerHTML = ""; delete box.dataset.open; return; }
  box.dataset.open = "1";
  box.innerHTML = `<div class="loading" style="padding:12px">Comparing before vs after…</div>`;
  if (!s.baselineRev) {
    const files = [...new Set((s.files || []).map(fileLabel))];
    box.innerHTML = `<p class="muted" style="padding:0 0 16px">This is your earliest recorded session, so there's no earlier state to compare against. Files touched: ${files.length ? esc(files.join(", ")) : "none"}.</p>`;
    return;
  }
  const r = await api("get_comparison", s.baselineRev, s.endRev, "Before session", "After session");
  if (!r.ok) { box.innerHTML = `<p class="muted" style="padding:0 0 16px">${esc(r.error)}</p>`; return; }
  if (!r.files.length) {
    box.innerHTML = `<div class="empty" style="padding:14px 10px 22px">${icon("shieldCheck")}<p>No saved config changes during this session.</p></div>`;
    return;
  }
  box.innerHTML = `<div class="section-label" style="margin:4px 0 8px">What changed during this session</div>
    <div style="padding-bottom:14px">${r.files.map((f) => `<div class="diff-file"><h4>${esc(fileLabel(f.name))}</h4><div class="diff-body">${colorizeDiff(f.body)}</div></div>`).join("")}</div>`;
}

/* ========================================================== GAME SETTINGS */
async function renderGameSettings() {
  const content = $("#content");
  $("#aside").innerHTML = "";
  content.innerHTML = `<div class="loading">Loading your settings…</div>`;
  state.settings = await api("list_settings");
  const s = state.settings;
  if (!s || !s.ok) {
    content.innerHTML = `<div class="page-head"><h1 class="page-title">Game Settings</h1></div>
      <div class="empty">${icon("sliders")}<h3>Settings unavailable</h3>
      <p>${esc((s && s.error) || "Couldn’t read your settings files.")}</p></div>`;
    return;
  }
  const lint = await api("run_config_lint");
  content.innerHTML = `
    <div class="page-head"><h1 class="page-title">Game Settings</h1>
      <p class="page-sub">Your iRacing config values. Search a setting and click it to see when it last changed. Read-only — change these inside iRacing.</p></div>
    ${lintCard(lint)}
    <input class="search" id="setSearch" placeholder="Find a setting (e.g. memory, FOV, mirror, fps)…" value="${esc(state.settingsQuery || "")}">
    <div id="setResults"></div>`;
  const inp = $("#setSearch");
  inp.addEventListener("input", (e) => { state.settingsQuery = e.target.value; renderSettingsRows(); });
  renderSettingsRows();
  inp.focus();
}

function lintCard(lint) {
  if (!lint || !lint.ok) return "";
  const f = lint.findings || [];
  if (!f.length) {
    return `<div class="card" style="margin-bottom:16px;padding:13px 16px">
      <span class="muted" style="font-size:12.5px">${icon("shieldCheck")} Sanity check: no problems found in your settings.</span></div>`;
  }
  return `<div class="card conflict-banner" style="margin-bottom:16px">
    <p class="section-label mt-0" style="color:var(--warn)">${icon("alert")} ${f.length} thing${f.length > 1 ? "s" : ""} worth a look</p>
    ${f.map((x, i) => `<div style="padding:9px 0${i ? ";border-top:1px solid var(--line-soft)" : ""}">
      <div style="font-weight:600;font-size:13.5px;display:flex;align-items:center;gap:6px">${icon(x.severity === "warn" ? "alert" : "clock")} ${esc(x.title)}${x.where ? ` <span class="muted" style="font-weight:500">· ${esc(x.where)}</span>` : ""}</div>
      <div class="muted" style="font-size:12.5px;margin-top:3px;line-height:1.5">${esc(x.detail)}</div>
    </div>`).join("")}
  </div>`;
}

function settingRow(it, withDate) {
  const loc = it.section ? `${esc(it.section)} · ` : "";
  const when = (withDate && it.date) ? ` · changed ${esc(fmtDate(it.date))}` : "";
  const val = it.value == null ? "(not set)" : it.value;
  return `<div class="file-row" data-action="blame-setting" data-file="${esc(it.file)}" data-section="${esc(it.section)}" data-key="${esc(it.key)}" style="cursor:pointer" title="See when this setting last changed">
    <div class="file-ico">${icon(fileIconName(it.file))}</div>
    <div style="flex:1">
      <div class="file-name">${esc(it.key)} <span class="bind key" style="margin-left:6px">${esc(val)}</span></div>
      <div class="file-desc">${loc}${esc(fileLabel(it.file))}${when}</div>
    </div>
    <div class="file-meta">${icon("clock", "ico ctl-hist")}</div>
  </div>`;
}

function renderSettingsRows() {
  const s = state.settings;
  const box = $("#setResults");
  if (!s || !box) return;
  const q = (state.settingsQuery || "").trim().toLowerCase();
  if (!q) {
    if (!s.recent || !s.recent.length) {
      box.innerHTML = `<div class="card"><p class="muted mt-0">Type above to find any setting. Once you’ve backed up a few changes, the settings you’ve recently tweaked will show up here.</p></div>`;
      return;
    }
    box.innerHTML = `<div class="card"><p class="section-label mt-0">${icon("clock")} Recently changed settings</p>${s.recent.map((it) => settingRow(it, true)).join("")}</div>`;
    return;
  }
  const matches = (s.all || []).filter((it) =>
    `${it.section} ${it.key} ${it.value}`.toLowerCase().includes(q)).slice(0, 200);
  if (!matches.length) {
    box.innerHTML = `<div class="card"><p class="muted mt-0">No settings match “${esc(state.settingsQuery)}”.</p></div>`;
    return;
  }
  box.innerHTML = `<div class="card"><p class="section-label mt-0">${matches.length} match${matches.length > 1 ? "es" : ""}</p>${matches.map((it) => settingRow(it, false)).join("")}</div>`;
}

/* ============================================================== PROFILES */
async function renderProfiles() {
  const content = $("#content");
  $("#aside").innerHTML = "";
  content.innerHTML = `<div class="loading">Loading saved setups…</div>`;
  const r = await api("list_profiles");
  const items = r.ok ? r.items : [];
  content.innerHTML = `
    <div class="page-head spread">
      <div><h1 class="page-title">Saved Setups</h1>
        <p class="page-sub">Snapshots of your whole iRacing config you can restore in one click — e.g. “Oval”, “Road”, “VR”. (This is the app’s own backup feature — separate from iRacing’s built-in control profiles.)</p></div>
      <button class="btn btn-primary" data-action="save-profile">${icon("bookmark")} Save current setup…</button>
    </div>
    ${items.length ? items.map(profileCard).join("") : profilesEmpty()}`;
}

function profilesEmpty() {
  return `<div class="empty">${icon("bookmark")}
    <h3>No saved setups yet</h3>
    <p>Save your current iRacing config under a name, then restore it any time with one click — great for swapping between disciplines or rigs (oval vs road, VR vs triple-screen).</p>
    <div style="margin-top:18px"><button class="btn btn-primary" data-action="save-profile">Save current setup…</button></div>
  </div>`;
}

function profileCard(p) {
  const when = p.date ? fmtDate(p.date) : "";
  const ctx = p.contextLabel && p.contextLabel !== "manual edit" ? ` · ${esc(p.contextLabel)}` : "";
  return `<div class="card" style="margin-bottom:12px">
    <div class="spread">
      <div>
        <div style="font-weight:650;font-size:15px">${icon("bookmark")} ${esc(p.name)}</div>
        <div class="muted" style="font-size:12.5px;margin-top:3px">${esc(when)}${esc(ctx)}</div>
      </div>
      <div class="row-gap">
        <button class="btn btn-sm btn-primary" data-action="apply-profile" data-name="${esc(p.name)}">${icon("rotate")} Restore</button>
        <button class="btn btn-sm btn-danger" data-action="delete-profile" data-name="${esc(p.name)}">Delete</button>
      </div>
    </div>
    <div class="tl-files" style="margin-top:10px">${fileChips(p.files)}</div>
  </div>`;
}

async function doSaveProfile() {
  const name = await promptModal({
    title: "Save current setup",
    body: "Name it something memorable like “Oval”, “Road”, or “VR”. You can restore it any time.",
    placeholder: "e.g. Road setup", confirmLabel: "Save setup",
  });
  if (!name) return;
  const r = await api("save_current_as_profile", name);
  toast(r.ok ? r.message : r.error, r.ok ? "good" : "bad");
  if (r.ok) renderProfiles();
}

async function doApplyProfile(name) {
  const ok = await confirmModal({
    title: `Restore saved setup “${esc(name)}”?`,
    body: `This sets your live iRacing files back to the <b>${esc(name)}</b> saved setup. A safety backup of your current setup is made first, so it's reversible.`,
    confirmLabel: "Restore", danger: false,
  });
  if (!ok) return;
  const r = await api("restore_baseline", name);
  toast(r.ok ? r.message : r.error, r.ok ? "good" : "bad");
  if (r.ok) await refreshAll();
}

async function doDeleteProfile(name) {
  const ok = await confirmModal({
    title: `Delete saved setup “${esc(name)}”?`,
    body: "This removes the saved setup only. Your backups and live files are left untouched.",
    confirmLabel: "Delete", danger: true,
  });
  if (!ok) return;
  const r = await api("delete_profile", name);
  toast(r.ok ? r.message : r.error, r.ok ? "good" : "bad");
  if (r.ok) renderProfiles();
}

/* ============================================================== SETTINGS */
function renderSettings() {
  const o = state.overview;
  const aside = $("#aside");
  aside.innerHTML = "";
  const content = $("#content");
  if (!o || !o.ok) { content.innerHTML = setupError(o); return; }

  const wRunning = !!(o.watcher && o.watcher.running);
  const wPaused = !!(o.watcher && o.watcher.paused);

  content.innerHTML = `
    <div class="page-head spread"><div><h1 class="page-title">Settings</h1>
      <p class="page-sub">Control how your iRacing settings are protected.</p></div>
      <button class="btn btn-sm" data-action="run-wizard">Run setup wizard</button></div>

    <p class="section-label">Health check</p>
    <div class="card">
      <div class="spread">
        <p class="muted mt-0" style="font-size:12.5px">Confirm your backups, auto-backup, and the controls decoder are all working — before you ever need a restore.</p>
        <button class="btn btn-sm btn-primary" data-action="run-health">${icon("shieldCheck")} Run health check</button>
      </div>
      <div id="healthResults"></div>
    </div>

    ${updatesCard()}

    <p class="section-label" style="margin-top:22px">Automatic backups</p>
    <div class="card">
      <div class="toggle-row">
        <div><div class="label">Watch for changes right now</div>
          <div class="desc">Runs in the background and backs up whenever iRacing settings change.</div></div>
        <div class="spacer"></div>
        <label class="toggle"><input type="checkbox" id="tgWatch" ${wRunning && !wPaused ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="toggle-row">
        <div><div class="label">Start automatically when I log in</div>
          <div class="desc">No need to open this app — protection starts with Windows.</div></div>
        <div class="spacer"></div>
        <label class="toggle"><input type="checkbox" id="tgAutostart" ${o.autostartOn ? "checked" : ""}><span class="track"></span></label>
      </div>
    </div>

    <p class="section-label" style="margin-top:22px">App window</p>
    <div class="card">
      <div class="toggle-row">
        <div><div class="label">Keep in the system tray when I close the window</div>
          <div class="desc">Tucks the app into your tray (with quick “Back up now” + “Open”) instead of quitting. Turn off to quit on close. Applies next time you open the app.</div></div>
        <div class="spacer"></div>
        <label class="toggle"><input type="checkbox" id="tgTray" ${o.trayEnabled ? "checked" : ""}><span class="track"></span></label>
      </div>
    </div>

    <p class="section-label" style="margin-top:22px">Files being protected</p>
    <div class="card">
      ${o.protected.map((p) => `<div class="file-row">
        <div class="file-ico">${icon(fileIconName(p.pattern.replace("*", "")))}</div>
        <div><div class="file-name">${esc(fileLabel(p.pattern.replace("rendererDX11*.ini", "rendererDX11Monitor.ini")))}</div>
          <div class="file-desc">${esc(p.pattern)} — ${esc(POLICY_LABELS[p.policy] || p.policy)}</div></div>
      </div>`).join("")}
    </div>

    <p class="section-label" style="margin-top:22px">Folders</p>
    <div class="card">
      <div style="font-weight:600">iRacing folder</div>
      <div class="file-desc">Where your live iRacing config files live.</div>
      <div class="row-gap" style="margin-top:6px">
        <input class="search" id="setIracing" style="margin-bottom:0;flex:1;min-width:220px" value="${esc(o.iracingDir)}">
        <button class="btn btn-sm" data-action="browse-iracing">Browse…</button>
      </div>
      <div style="font-weight:600;margin-top:14px">Where backups are stored</div>
      <div class="file-desc">The folder that holds your backup history. Point it at a synced/cloud folder for offsite copies.</div>
      <div class="row-gap" style="margin-top:6px">
        <input class="search" id="setData" style="margin-bottom:0;flex:1;min-width:220px" value="${esc(o.dataDir)}">
        <button class="btn btn-sm" data-action="browse-data">Browse…</button>
      </div>
      <label class="row-gap" style="margin-top:12px;cursor:pointer;font-size:13px"><input type="checkbox" id="setMove" checked> Move my existing backups to the new folder</label>
      <div class="row-gap" style="margin-top:14px">
        <button class="btn btn-primary btn-sm" data-action="save-settings">${icon("shieldCheck")} Save settings</button>
        <button class="btn btn-sm" data-action="open-iracing">${icon("folder")} Open iRacing folder</button>
        <button class="btn btn-sm" data-action="open-data">${icon("folder")} Open backup folder</button>
        <button class="btn btn-sm" data-action="open-config">${icon("doc")} Open settings file</button>
      </div>
      <p class="sidebar-hint" style="margin-top:12px">Settings file: ${esc(o.configPath)}</p>
    </div>`;

  $("#tgWatch").addEventListener("change", onToggleWatch);
  $("#tgAutostart").addEventListener("change", onToggleAutostart);
  $("#tgTray").addEventListener("change", onToggleTray);
}

async function onToggleTray(e) {
  const on = e.target.checked;
  const r = await api("set_tray_enabled", on);
  if (!r.ok) { toast(r.error, "bad"); e.target.checked = !on; return; }
  if (state.overview) state.overview.trayEnabled = on;
  toast(`System tray ${on ? "on" : "off"} — applies next time you open the app.`, "good");
}

/* ----------------------------------------------------------------- actions */
async function doBackup() {
  toast("Backing up…");
  const r = await api("backup_now", null);
  if (!r.ok) { toast(r.error, "bad"); return; }
  toast(r.created ? "Backup saved." : (r.message || "Already up to date."), "good");
  await refreshAll();  // always re-sync so the "unsaved changes" warning clears
}

async function doMarkKnownGood() {
  const name = await promptModal({
    title: "Mark current setup as known-good",
    body: "Give it a label you’ll recognize — like “Road — Daytona” or “FFB I liked at Spa.” (Optional.)",
    placeholder: "e.g. Road — Daytona", confirmLabel: "Mark known-good",
  });
  if (name === null) return;  // cancelled
  toast("Marking known-good…");
  const r = await api("mark_known_good", name || null);
  toast(r.ok ? r.message : r.error, r.ok ? "good" : "bad");
  if (r.ok) await refreshAll();
}

async function doRevertKnownGood(tag) {
  const kg = state.overview && state.overview.lastKnownGood;
  const label = kg ? kg.label : "your last known-good setup";
  const ok = await confirmModal({
    title: "Revert to known-good?",
    body: `This sets your live iRacing files back to <b>${esc(label)}</b>. A safety backup of your current setup is made first, so it's reversible.`,
    confirmLabel: "Revert", danger: false,
  });
  if (!ok) return;
  const r = await api("revert_known_good", tag || null);
  if (!r.ok && r.simBlocked) { toast(r.error, "bad"); return; }
  toast(r.ok ? r.message : r.error, r.ok ? "good" : "bad");
  if (r.ok) await refreshAll();
}

async function doDeleteKnownGood(tag) {
  const ok = await confirmModal({
    title: "Remove this known-good mark?",
    body: "This only removes the “known-good” label. Your backups and live files are left untouched.",
    confirmLabel: "Remove", danger: true,
  });
  if (!ok) return;
  const r = await api("delete_known_good", tag);
  toast(r.ok ? r.message : r.error, r.ok ? "good" : "bad");
  if (r.ok) await refreshAll();
}

async function doRestoreFile(rev, file) {
  const ok = await confirmModal({
    title: `Restore ${fileLabel(file)}?`,
    body: `This replaces your live <b>${esc(fileLabel(file))}</b> with the version from this backup. A safety backup of your current file is made first, so you can undo it.`,
    confirmLabel: "Restore", danger: true,
  });
  if (!ok) return;
  const r = await api("restore_file", rev, file);
  toast(r.ok ? r.message : r.error, r.ok ? "good" : "bad");
  if (r.ok) await refreshAll();
}

async function doBookmark(rev) {
  const name = await promptModal({
    title: "Save this version as a setup",
    body: "Give it a memorable name like “Daytona known-good” or “Before FFB tweak”. You can restore the whole set by this name later.",
    placeholder: "e.g. good-baseline", confirmLabel: "Save",
  });
  if (!name) return;
  const r = await api("create_tag", name, rev, null);
  toast(r.ok ? r.message : r.error, r.ok ? "good" : "bad");
  if (r.ok) { state.history = null; await refreshAll(); if (state.view === "history") showBackupDetail(rev); }
}

async function doExport(rev) {
  toast("Preparing export…");
  const r = await api("export_backup", rev);
  if (!r.ok) { toast(r.error, "bad"); return; }
  if (r.cancelled) return;
  toast(r.message || "Exported.", "good");
}

async function doRemap(oldGuid, newGuid) {
  const ok = await confirmModal({
    title: "Re-map to your connected device?",
    body: "Your wheel/pedals are showing up with a new ID — this usually happens after a " +
      "USB-port change or a new PC. This points all your existing bindings (and pedal/wheel " +
      "calibration) at the connected device, so you don't have to rebind everything in iRacing. " +
      "A safety backup is made first, so it's reversible.",
    confirmLabel: "Re-map", danger: false,
  });
  if (!ok) return;
  const r = await api("remap_device", oldGuid, newGuid);
  toast(r.ok ? r.message : r.error, r.ok ? "good" : "bad");
  if (r.ok) { state.controls = null; state.devices = null; await refreshAll(); }
}

async function doHealthCheck() {
  const box = document.getElementById("healthResults");
  if (box) box.innerHTML = `<div class="loading" style="padding:16px">Running checks…</div>`;
  const r = await api("run_health_check");
  if (!box) return;
  if (!r.ok) { box.innerHTML = `<p class="muted" style="margin-top:10px">${esc(r.error)}</p>`; return; }
  const pill = (s) => s === "ok" ? `<span class="pill good"><span class="dot"></span>OK</span>`
    : s === "warn" ? `<span class="pill warn"><span class="dot"></span>Check</span>`
    : `<span class="pill bad"><span class="dot"></span>Problem</span>`;
  const summary = r.fails ? `${r.fails} problem(s)${r.warns ? `, ${r.warns} warning(s)` : ""}`
    : r.warns ? `All critical checks passed · ${r.warns} warning(s)`
    : "All checks passed 🎉";
  box.innerHTML = `<div style="margin-top:12px">
    <div class="muted" style="font-size:12.5px;margin-bottom:6px">${esc(summary)}</div>
    ${r.checks.map((c) => `<div class="kv"><span class="k">${esc(c.name)}<div class="file-desc">${esc(c.detail)}</div></span><span class="v">${pill(c.status)}</span></div>`).join("")}
  </div>`;
}

async function doBrowse(inputId) {
  const r = await api("pick_folder");
  if (!r.ok) { toast(r.error, "bad"); return; }
  if (r.cancelled || !r.path) return;
  const el = document.getElementById(inputId);
  if (el) el.value = r.path;
}

async function doSaveSettings() {
  const ira = $("#setIracing").value.trim();
  const data = $("#setData").value.trim();
  const move = $("#setMove").checked;
  const o = state.overview;
  if (o && data && data !== o.dataDir) {
    const ok = await confirmModal({
      title: "Change where backups are stored?",
      body: move
        ? `Backups will be stored in:<br><b>${esc(data)}</b><br><br>Your existing backups will be <b>moved</b> there.`
        : `New backups will be stored in:<br><b>${esc(data)}</b><br><br>(Existing backups stay where they are.)`,
      confirmLabel: "Save", danger: false,
    });
    if (!ok) return;
  }
  const r = await api("update_settings", ira || null, data || null, move);
  toast(r.ok ? r.message : r.error, r.ok ? "good" : "bad");
  if (r.ok) { await loadOverview(); renderSimChip(); render(); }
}

async function onToggleWatch(e) {
  const on = e.target.checked;
  const o = state.overview;
  let r;
  if (on) {
    r = (o.watcher && o.watcher.running) ? await api("pause_watcher", false) : await api("start_watcher");
  } else {
    r = (o.watcher && o.watcher.running) ? await api("pause_watcher", true) : await api("stop_watcher");
  }
  toast(r.ok ? r.message : r.error, r.ok ? "good" : "bad");
  setTimeout(refreshOverviewQuiet, 800);
}

async function onToggleAutostart(e) {
  const on = e.target.checked;
  const ok = await confirmModal({
    title: on ? "Start automatically when you log in?" : "Stop starting automatically?",
    body: on
      ? "iRacing Config Tracker will start with Windows and quietly back up your settings in the background."
      : "It won't start on its own anymore — you'll open it yourself when you want it.",
    confirmLabel: on ? "Yes, enable" : "Disable",
  });
  if (!ok) { e.target.checked = !on; return; }   // revert the toggle
  const r = await api("set_autostart", on);
  toast(r.ok ? r.message : r.error, r.ok ? "good" : "bad");
  if (!r.ok) e.target.checked = !on;
  await loadOverview();
}

async function refreshOverviewQuiet() {
  await loadOverview();
  renderSimChip();
  // Only the dashboard auto-refreshes; re-rendering Settings/Profiles/etc. would
  // wipe in-progress UI (health-check results, scroll position, etc.).
  if (state.view === "home") render();
}

async function refreshAll() {
  state.history = null;
  await loadOverview();
  renderSimChip();
  render();
}

/* ============================================================== FIRST-RUN WIZARD */
const WIZ_STEPS = 5;

function openWizard() {
  const o = state.overview || {};
  state.wizard = { step: 0, editFolder: false, backupDone: false, backupMsg: "", autoBackup: true };
  renderWizard();
}

function closeWizard(markDone) {
  state.wizard = null;
  document.getElementById("wizardRoot").innerHTML = "";
  if (markDone) api("mark_onboarded");
  loadOverview().then(() => { renderSimChip(); render(); });
}

function logoUri() {
  const link = document.querySelector('link[rel=icon]');
  return link ? link.href : "";
}

function wizDots(step) {
  let dots = "";
  for (let i = 0; i < WIZ_STEPS; i++) dots += `<i class="${i === step ? "on" : ""}"></i>`;
  return `<div class="wizard-dots">${dots}</div>`;
}

function renderWizard() {
  const w = state.wizard;
  if (!w) return;
  const o = state.overview || {};
  let body = "", actions = "";

  if (w.step === 0) {
    body = `<img class="wizard-logo" src="${logoUri()}" alt="">
      <h2>Welcome to iRacing Config Tracker</h2>
      <p class="lead">It quietly backs up your iRacing settings — controls, force feedback, graphics — so you can undo a bad change or recover your whole setup any time. No technical know-how needed.</p>`;
    actions = `<button class="btn btn-ghost" data-action="wiz-skip">Skip setup</button>
      <button class="btn btn-primary" data-action="wiz-next">Get started</button>`;
  } else if (w.step === 1) {
    const found = o.iracingDirExists;
    const showInput = !found || w.editFolder;
    body = `<h2>Your iRacing folder</h2>
      <p class="lead">This is where iRacing stores your settings. We ${found ? "found it automatically." : "couldn't find it — please choose it."}</p>
      ${showInput
        ? `<div class="row-gap"><input class="search" id="wizFolder" style="margin-bottom:0;flex:1;min-width:0" value="${esc(o.iracingDir || "")}" placeholder="C:\\Users\\you\\Documents\\iRacing"><button class="btn btn-sm" data-action="wiz-browse">Browse…</button></div>`
        : `<div class="card" style="display:flex;gap:10px;align-items:center"><span class="pill good"><span class="dot"></span>Found</span><span style="word-break:break-all;font-size:12.5px">${esc(o.iracingDir)}</span></div>
           <p class="muted" style="font-size:12px;margin-top:8px;text-align:center"><a href="#" data-action="wiz-editfolder" style="color:var(--accent)">Choose a different folder</a></p>`}`;
    actions = `<button class="btn btn-ghost" data-action="wiz-back">Back</button>
      <button class="btn btn-primary" data-action="wiz-folder-next">Next</button>`;
  } else if (w.step === 2) {
    body = `<h2>Make your first backup</h2>
      <p class="lead">We'll save a snapshot of your current settings — your safety net. You can come back to it any time.</p>
      ${w.backupDone ? `<div class="card" style="display:flex;gap:10px;align-items:center"><span class="pill good"><span class="dot"></span>Done</span><span style="font-size:13px">${esc(w.backupMsg)}</span></div>` : ""}`;
    actions = `<button class="btn btn-ghost" data-action="wiz-back">Back</button>` +
      (w.backupDone
        ? `<button class="btn btn-primary" data-action="wiz-next">Next</button>`
        : `<button class="btn btn-primary" data-action="wiz-backup">Make my first backup</button>`);
  } else if (w.step === 3) {
    body = `<h2>Keep it backed up automatically</h2>
      <p class="lead">Recommended: let it run quietly in the background and back up whenever your settings change — and start with Windows so you never have to think about it.</p>
      <label class="row-gap" style="cursor:pointer;font-size:13.5px"><input type="checkbox" id="wizAuto" ${w.autoBackup ? "checked" : ""}> Automatically back up in the background and at startup</label>`;
    actions = `<button class="btn btn-ghost" data-action="wiz-back">Back</button>
      <button class="btn btn-primary" data-action="wiz-finish">Finish</button>`;
  } else {
    body = `<img class="wizard-logo" src="${logoUri()}" alt="">
      <h2>You're all set! 🎉</h2>
      <p class="lead">Your settings are protected. Open <b>Backup History</b> any time to see saved versions or restore one, or <b>Saved Setups</b> to switch between whole configs.</p>`;
    actions = `<button class="btn btn-primary" data-action="wiz-done" style="margin:0 auto">Open the app</button>`;
  }

  document.getElementById("wizardRoot").innerHTML =
    `<div class="wizard-bg"><div class="wizard">${wizDots(w.step)}${body}<div class="wizard-actions">${actions}</div></div></div>`;
}

async function wizBrowse() {
  const r = await api("pick_folder");
  if (r.ok && r.path) { const el = $("#wizFolder"); if (el) el.value = r.path; }
  else if (!r.ok) toast(r.error, "bad");
}

async function wizFolderNext() {
  const o = state.overview || {};
  const input = $("#wizFolder");
  const chosen = input ? input.value.trim() : (o.iracingDir || "");
  if (input && chosen && chosen !== o.iracingDir) {
    const r = await api("update_settings", chosen, null, false);
    if (!r.ok) { toast(r.error, "bad"); return; }
    await loadOverview();
  }
  if (!(state.overview || {}).iracingDirExists) {
    toast("That folder doesn't exist — pick your iRacing folder.", "bad");
    return;
  }
  state.wizard.step = 2;
  renderWizard();
}

async function wizBackup() {
  toast("Backing up…");
  const r = await api("backup_now", "first setup");
  if (!r.ok) { toast(r.error, "bad"); return; }
  state.wizard.backupDone = true;
  state.wizard.backupMsg = r.created
    ? `Backed up ${Object.keys(r.files || {}).length} file(s).`
    : (r.message || "Already backed up.");
  await loadOverview();
  renderWizard();
}

async function wizFinish() {
  const auto = $("#wizAuto") ? $("#wizAuto").checked : false;
  if (auto) {
    const watch = await api("start_watcher");
    const login = await api("set_autostart", true);
    const ok = watch.ok || login.ok;
    toast(ok ? "Auto-backup is on." : (watch.error || login.error || "Couldn't enable auto-backup."),
          ok ? "good" : "bad");
  }
  state.wizard.step = 4;
  renderWizard();
}

/* --------------------------------------------------------- event wiring */
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-action]");
  if (!btn) return;
  const a = btn.dataset.action;
  const rev = btn.dataset.rev;
  const file = btn.dataset.file;
  if (a === "backup") return doBackup();
  if (a === "goto-history" || a === "view-pending") { setView("history"); if (a === "view-pending") setTimeout(compareNow, 50); return; }
  if (a === "compare-now") return compareNow();
  if (a === "hview-list") { state.historyView = "list"; return renderHistory(); }
  if (a === "hview-timeline") { state.historyView = "timeline"; return renderHistory(); }
  if (a === "hview-sessions") { state.historyView = "sessions"; return renderHistory(); }
  if (a === "toggle-session") return doSessionDetail(+btn.dataset.i);
  if (a === "goto-settings") return setView("settings");
  if (a === "open-iracing") return api("open_folder", "iracing");
  if (a === "open-data") return api("open_folder", "data");
  if (a === "open-config") return api("open_folder", "config");
  if (a === "restore-file") return doRestoreFile(rev, file);
  if (a === "bookmark") return doBookmark(rev);
  if (a === "edit-note") return doEditNote(btn.dataset.rev);
  if (a === "export") return doExport(rev);
  if (a === "remap") return doRemap(btn.dataset.old, btn.dataset.new);
  if (a === "mark-known-good") return doMarkKnownGood();
  if (a === "revert-known-good") return doRevertKnownGood(btn.dataset.tag);
  if (a === "delete-known-good") return doDeleteKnownGood(btn.dataset.tag);
  if (a === "save-profile") return doSaveProfile();
  if (a === "apply-profile") return doApplyProfile(btn.dataset.name);
  if (a === "delete-profile") return doDeleteProfile(btn.dataset.name);
  if (a === "run-health") return doHealthCheck();
  if (a === "run-compare") return doRunCompare();
  if (a === "export-compare") return doExportCompare();
  if (a === "browse-iracing") return doBrowse("setIracing");
  if (a === "browse-data") return doBrowse("setData");
  if (a === "save-settings") return doSaveSettings();
  if (a === "binding-inventory") return doBindingInventory();
  if (a === "copy-inventory") return copyInventory();
  if (a === "blame-control") return doBlameControl(btn.dataset.name);
  if (a === "blame-setting") return doBlameSetting(btn.dataset.file, btn.dataset.section, btn.dataset.key);
  if (a === "identify") return doIdentify(($("#identifyInput") || {}).value);
  if (a === "refresh-controls") return renderControls();
  if (a === "check-update") return doCheckUpdate(btn);
  if (a === "do-update") return doUpdate();
  if (a === "open-release") return api("open_url", (state.update || {}).url);
  if (a === "run-wizard") return openWizard();
  if (a === "wiz-next") { state.wizard.step++; return renderWizard(); }
  if (a === "wiz-back") { state.wizard.step--; return renderWizard(); }
  if (a === "wiz-skip" || a === "wiz-done") return closeWizard(true);
  if (a === "wiz-browse") return wizBrowse();
  if (a === "wiz-editfolder") { e.preventDefault(); state.wizard.editFolder = true; return renderWizard(); }
  if (a === "wiz-folder-next") return wizFolderNext();
  if (a === "wiz-backup") return wizBackup();
  if (a === "wiz-finish") return wizFinish();
});

document.querySelectorAll(".nav-item").forEach((b) =>
  b.addEventListener("click", () => setView(b.dataset.view)));

$("#backupBtn").addEventListener("click", doBackup);
$("#navToggle").addEventListener("click", () => $("#layout").classList.toggle("nav-collapsed"));

/* --------------------------------------------------------------- theme */
function applyTheme(theme) {
  const light = theme === "light";
  document.documentElement.setAttribute("data-theme", light ? "light" : "dark");
  try { localStorage.setItem("irtrack-theme", light ? "light" : "dark"); } catch (e) {}
  const btn = $("#themeToggle");
  if (btn) {
    btn.innerHTML = light ? icon("moon") : icon("sun");
    btn.title = light ? "Switch to dark mode" : "Switch to light mode";
  }
}
function toggleTheme() {
  applyTheme(document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light");
}
applyTheme(document.documentElement.getAttribute("data-theme") || "dark");
$("#themeToggle").addEventListener("click", toggleTheme);

/* --------------------------------------------------------------- bootstrap */
function hideBootScreen() {
  const b = document.getElementById("bootScreen");
  if (b) { b.classList.add("hide"); setTimeout(() => b.remove(), 400); }
}

async function init() {
  await loadOverview();
  hideBootScreen();          // first data is in — drop the loading screen
  renderSimChip();
  render();
  // First launch (no backups yet and never onboarded) -> show the setup wizard.
  const o = state.overview;
  if (o && o.ok && o.snapshotCount === 0 && !o.onboarded) openWizard();
  // keep the sim chip + dashboard fresh
  setInterval(refreshOverviewQuiet, 15000);
  checkForUpdate();          // background; surfaces a banner if there's a newer build
}

async function checkForUpdate() {
  const u = await api("check_for_update");
  if (!u || !u.ok) return;
  state.update = u;
  if (u.updateAvailable && u.canApply) {
    if (state.view === "home") renderHome();
    toast(`Update available: ${u.latest}`, "good");
  }
}

function updateBanner() {
  const u = state.update;
  if (!u || !u.ok || !u.updateAvailable || !u.canApply) return "";
  return `<div class="hero info" style="margin-bottom:14px">
    <div class="hero-icon">${icon("rotate")}</div>
    <div><h2 class="hero-title">Update available</h2>
      <p class="hero-text">You're on ${esc(u.current)} — ${esc(u.latest)} is ready to install.</p></div>
    <div class="hero-actions">
      <button class="btn btn-primary" data-action="do-update">Update now</button>
      <button class="btn" data-action="open-release">Release notes</button>
    </div>
  </div>`;
}

function updatesCard() {
  const u = state.update;
  const cur = (u && u.current) ? u.current : "—";
  let status = "";
  if (u && u.ok) {
    status = u.updateAvailable
      ? `<span class="pill warn"><span class="dot"></span>${esc(u.latest)} available</span>`
      : `<span class="pill good"><span class="dot"></span>Up to date</span>`;
  }
  const install = (u && u.updateAvailable && u.canApply)
    ? `<button class="btn btn-sm btn-primary" data-action="do-update">Install ${esc(u.latest)}</button>` : "";
  const gh = (u && u.updateAvailable && !u.canApply)
    ? `<button class="btn btn-sm" data-action="open-release">Get it on GitHub</button>` : "";
  return `<p class="section-label" style="margin-top:22px">Updates</p>
    <div class="card">
      <div class="spread">
        <div><div style="font-weight:600">App version ${esc(cur)}</div>
          <div class="file-desc">Checks GitHub for the newest release.</div></div>
        <div class="row-gap">${status}<button class="btn btn-sm" data-action="check-update">Check now</button>${install}${gh}</div>
      </div>
    </div>`;
}

async function doCheckUpdate(btn) {
  if (state.checkingUpdate) return;        // ignore spam-clicks while one is in flight
  state.checkingUpdate = true;
  if (btn) btn.disabled = true;
  toast("Checking for updates…");
  try {
    const u = await api("check_for_update");
    state.update = u;
    if (!u.ok) toast(u.error || "Couldn't check for updates.", "bad");
    else if (u.updateAvailable) toast(`Update available: ${u.latest}`, "good");
    else toast("You're on the latest version.", "good");
    if (state.view === "settings" || state.view === "home") render();
  } finally {
    state.checkingUpdate = false;
    if (btn && btn.isConnected) btn.disabled = false;
  }
}

async function doUpdate() {
  const u = state.update;
  if (!u || !u.exeUrl) { toast("No update info yet — try Check now.", "bad"); return; }
  if (!u.canApply) { if (u.url) api("open_url", u.url); return; }
  const ok = await confirmModal({
    title: `Install ${esc(u.latest)}?`,
    body: "This downloads the new version, replaces the current app, and reopens it automatically. Takes a few seconds."
      + (u.needsAdmin ? "<br><br><b>Windows will ask for administrator permission</b> because the app is in a protected folder." : ""),
    confirmLabel: "Update now",
  });
  if (!ok) return;
  toast("Downloading update…");
  const r = await api("apply_update", u.exeUrl, u.shaUrl || null);
  if (!r.ok) { toast(r.error, "bad"); return; }
  document.getElementById("modalRoot").innerHTML =
    `<div class="modal-bg"><div class="modal"><h3>Updating…</h3><p>${esc(r.message || "The app will close and reopen.")}</p><div class="boot-spinner" style="margin:6px auto 0"></div></div></div>`;
}

(function boot() {
  let started = false;
  const go = (transport) => { if (!started) { started = true; TRANSPORT = transport; init(); } };
  // pywebview injects window.pywebview early; its api methods follow a beat
  // later (api() waits for them). Its ready event is the most reliable signal.
  if (window.pywebview) return go("pywebview");
  window.addEventListener("pywebviewready", () => go("pywebview"), { once: true });
  // The browser fallback serves this page over http(s) with a real origin;
  // a pywebview html= page does not. So if nothing pywebview-ish has shown up
  // shortly and we're on http, it's a real browser.
  let tries = 0;
  const iv = setInterval(() => {
    if (started) { clearInterval(iv); return; }
    if (window.pywebview) { clearInterval(iv); return go("pywebview"); }
    if (++tries > 5) {
      clearInterval(iv);
      go(location.protocol.startsWith("http") ? "browser" : "pywebview");
    }
  }, 100);
})();
