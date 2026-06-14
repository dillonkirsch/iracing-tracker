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
function fileLabel(name) {
  if (FILE_LABELS[name]) return FILE_LABELS[name];
  if (/^rendererDX11/i.test(name)) return "Monitor / Graphics Renderer";
  return name;
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
};
function fileIconName(name) {
  if (name === "controls.cfg") return "gamepad";
  if (name === "joyCalib.yaml") return "wheel";
  if (name === "fueldata.ini") return "droplet";
  if (name === "core.ini") return "sliders";
  if (name === "camera.ini") return "camera";
  if (/^app\.ini$|^renderer/i.test(name)) return "monitor";
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

function toast(msg, kind) {
  const wrap = $("#toastWrap");
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
  showUnbound: false,
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
  if (v === "controls") return renderControls();
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

  content.innerHTML = `
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
  const tags = (s.tags || []).map((t) => `<span class="chip tag-chip">${icon("bookmark")}${esc(t)}</span>`).join("");
  return `<div class="tl-top"><span class="tl-reason">${esc(triggerLabel(s.trigger))}</span>
      <span class="tl-date">${esc(fmtDate(s.date))}</span></div>
    ${ctx}${msg}
    <div class="tl-files">${fileChips(s.files)}${tags}</div>`;
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

  content.innerHTML = `
    <div class="page-head spread">
      <div><h1 class="page-title">Backup History</h1>
      <p class="page-sub">Every saved version of your settings. Click one to see what changed or to restore it.</p></div>
      <button class="btn btn-sm" data-action="compare-now">${icon("rotate")} What’s changed since last backup?</button>
    </div>
    <input class="search" id="histSearch" placeholder="Filter by car, track, file, or words in the note…">
    <div class="timeline" id="timeline"></div>`;

  $("#histSearch").addEventListener("input", (e) => renderTimeline(e.target.value));
  renderTimeline("");
  if (state.selectedRev) showBackupDetail(state.selectedRev);
  else asideHint("Select a backup to see what changed and restore it.");
}

function renderTimeline(q) {
  q = (q || "").toLowerCase();
  const items = state.history.filter((s) => {
    if (!q) return true;
    const hay = [s.message, s.car, s.track, triggerLabel(s.trigger),
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
  const tags = (s.tags || []).map((t) => `<span class="chip tag-chip">${icon("bookmark")}${esc(t)}</span>`).join("");
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

/* ====================================================== CONTROLS & DEVICES */
async function renderControls() {
  const content = $("#content");
  content.innerHTML = `<div class="loading">Reading your controls…</div>`;
  if (!state.controls) state.controls = await api("get_controls");
  if (!state.devices) state.devices = await api("get_devices");
  const c = state.controls;

  if (!c.ok || !c.available) {
    content.innerHTML = `
      <div class="page-head"><h1 class="page-title">Controls &amp; Devices</h1></div>
      <div class="empty">${icon("gamepad")}<h3>Controls not available</h3>
        <p>${esc((c && c.error) || "Couldn’t read your controls file.")}</p></div>`;
    renderDevicesAside();
    return;
  }

  content.innerHTML = `
    <div class="page-head"><h1 class="page-title">Controls &amp; Devices</h1>
      <p class="page-sub">How your wheel, pedals, and keyboard are mapped in iRacing. This view is read-only.</p></div>
    <div class="card" style="padding:14px;margin-bottom:16px;display:flex;gap:12px;align-items:flex-start">
      ${icon("alert", "ico")}
      <p class="muted mt-0" style="font-size:12.5px">${esc(c.ffbNote)}</p>
    </div>
    <input class="search" id="ctlSearch" placeholder="Search controls (e.g. throttle, pit, shift)…" value="${esc(state.controlsFilter)}">
    <div class="card" style="padding:14px">
      <div class="spread" style="margin-bottom:10px">
        <span class="muted" style="font-size:12.5px">${c.boundCount} of ${c.bindings.length} controls are assigned</span>
        <label class="row-gap" style="font-size:12.5px;cursor:pointer"><input type="checkbox" id="showUnbound" ${state.showUnbound ? "checked" : ""}> Show unassigned</label>
      </div>
      <table class="ctl-table"><thead><tr><th>Control</th><th>Assigned to</th><th>Device</th></tr></thead>
      <tbody id="ctlBody"></tbody></table>
    </div>`;

  $("#ctlSearch").addEventListener("input", (e) => { state.controlsFilter = e.target.value; renderCtlRows(); });
  $("#showUnbound").addEventListener("change", (e) => { state.showUnbound = e.target.checked; renderCtlRows(); });
  renderCtlRows();
  renderDevicesAside();
}

function renderCtlRows() {
  const c = state.controls;
  const q = state.controlsFilter.toLowerCase();
  let rows = c.bindings.slice();
  if (!state.showUnbound) rows = rows.filter((b) => b.kind !== "unbound");
  if (q) rows = rows.filter((b) => prettyAction(b.action).toLowerCase().includes(q) || b.action.toLowerCase().includes(q) || (b.display || "").toLowerCase().includes(q));
  // bound first
  rows.sort((a, b) => (a.kind === "unbound") - (b.kind === "unbound"));
  const body = $("#ctlBody");
  if (!rows.length) { body.innerHTML = `<tr><td colspan="3" class="muted" style="padding:18px">No controls match your search.</td></tr>`; return; }
  body.innerHTML = rows.map((b) => `
    <tr><td class="ctl-action">${esc(prettyAction(b.action))}</td>
      <td><span class="bind ${b.kind}">${esc(b.display)}</span></td>
      <td class="ctl-device">${esc(b.device || "—")}</td></tr>`).join("");
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
    <div class="page-head"><h1 class="page-title">Settings</h1>
      <p class="page-sub">Control how your iRacing settings are protected.</p></div>

    <p class="section-label">Automatic backups</p>
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

    <p class="section-label" style="margin-top:22px">Files being protected</p>
    <div class="card">
      ${o.protected.map((p) => `<div class="file-row">
        <div class="file-ico">${icon(fileIconName(p.pattern.replace("*", "")))}</div>
        <div><div class="file-name">${esc(fileLabel(p.pattern.replace("rendererDX11*.ini", "rendererDX11Monitor.ini")))}</div>
          <div class="file-desc">${esc(p.pattern)} — ${esc(POLICY_LABELS[p.policy] || p.policy)}</div></div>
      </div>`).join("")}
    </div>

    <p class="section-label" style="margin-top:22px">Folders &amp; info</p>
    <div class="card">
      <div class="kv"><span class="k">iRacing folder</span><span class="v">${esc(o.iracingDir)}</span></div>
      <div class="kv"><span class="k">Where backups are stored</span><span class="v">${esc(o.dataDir)}</span></div>
      <div class="kv"><span class="k">Settings file</span><span class="v">${esc(o.configPath)}</span></div>
      <div class="row-gap" style="margin-top:12px">
        <button class="btn btn-sm" data-action="open-iracing">${icon("folder")} Open iRacing folder</button>
        <button class="btn btn-sm" data-action="open-data">${icon("folder")} Open backup folder</button>
        <button class="btn btn-sm" data-action="open-config">${icon("doc")} Open settings file</button>
      </div>
    </div>`;

  $("#tgWatch").addEventListener("change", onToggleWatch);
  $("#tgAutostart").addEventListener("change", onToggleAutostart);
}

/* ----------------------------------------------------------------- actions */
async function doBackup() {
  toast("Backing up…");
  const r = await api("backup_now", null);
  if (!r.ok) { toast(r.error, "bad"); return; }
  if (!r.created) { toast(r.message || "Already up to date.", "good"); return; }
  toast("Backup saved.", "good");
  await refreshAll();
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
  const r = await api("set_autostart", e.target.checked);
  toast(r.ok ? r.message : r.error, r.ok ? "good" : "bad");
  if (!r.ok) e.target.checked = !e.target.checked;
  await loadOverview();
}

async function refreshOverviewQuiet() {
  await loadOverview();
  renderSimChip();
  if (state.view === "home" || state.view === "settings") render();
}

async function refreshAll() {
  state.history = null;
  await loadOverview();
  renderSimChip();
  render();
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
  if (a === "goto-settings") return setView("settings");
  if (a === "open-iracing") return api("open_folder", "iracing");
  if (a === "open-data") return api("open_folder", "data");
  if (a === "open-config") return api("open_folder", "config");
  if (a === "restore-file") return doRestoreFile(rev, file);
  if (a === "bookmark") return doBookmark(rev);
  if (a === "export") return doExport(rev);
  if (a === "remap") return doRemap(btn.dataset.old, btn.dataset.new);
});

document.querySelectorAll(".nav-item").forEach((b) =>
  b.addEventListener("click", () => setView(b.dataset.view)));

$("#backupBtn").addEventListener("click", doBackup);
$("#navToggle").addEventListener("click", () => $("#layout").classList.toggle("nav-collapsed"));

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
  // keep the sim chip + dashboard fresh
  setInterval(refreshOverviewQuiet, 15000);
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
