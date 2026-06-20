# Roadmap / Future ideas

A running wish-list for iRacing Config Tracker. Ratings are rough estimates:

- **Value** — how much a real iRacing user would care: **High / Med / Low**
- **Effort** — build cost given the *current* codebase: **Easy / Medium / Hard**
- **Foundation** — what already exists that this builds on (so "Easy" really is easy)

> Already shipped: the **local desktop/web UI** (dashboard + read-only Controls & Devices viewer), automatic versioned releases, semantic diffs, tags/baselines, the GFCC decode/encode codec, and the background watcher.

---

## ✅ Completed

| Feature | Notes |
|---|---|
| **Hardware re-map ("fix my wheel after a USB change")** | Repoints every binding **and** the pedal/wheel calibration from an old device instance GUID to the newly-connected one — across `controls.cfg` and `joyCalib.yaml`. One-click **"Re-map to connected device"** appears in the GUI's Devices panel whenever drift is detected, plus a `gfcc remap` CLI command (`--from/--to`, or `--auto` to detect it). A safety backup is taken first and the change is snapshotted. |
| **Duplicate / conflicting binding detection** | Flags one input bound to multiple actions. Context-aware: ignores iRacing's intentional cross-context key reuse (camera tool vs driving), so it only reports genuine same-context collisions. Shows a banner + highlights the rows in the GUI's Controls tab, and lists them under `gfcc devices`. |
| **Saved Setups** (named whole-config snapshots) | Named whole-setups ("Oval", "Road", "VR", "Triples") you restore with one click — a friendly layer over tags + `restore_baseline`. Dedicated **Saved Setups** tab (renamed from "Profiles" to avoid clashing with iRacing's native control profiles) to save the current config, restore one (safety backup + sim-running guard), or delete. |
| **Doctor / health check** | One-click validation of the whole setup — git, iRacing folder, writable backup store, backup history, tracked-file readability, controls decoder, sim/watcher/autostart state, optional deps — so you can confirm backups work *before* you need a restore. `irtrack doctor` CLI + a Health card in Settings. |
| **App icon** | Custom app/exe/window/taskbar icon + favicon + loading-screen logo (replaces the default Python icon). |
| **Compare any two backups + PDF export** | Pick any two backups (or "Now (live)") in History → one aggregated, colour-coded diff with the summary at the top; **Export PDF** produces a clean report (logo, summary first, per-file detail). |
| **Editable folders** | Change the iRacing folder and where backups are stored from Settings (with a native folder picker), optionally moving existing backups; saved back to `config.toml`. |
| **Auto-update** | The packaged app checks GitHub Releases on startup and from Settings; one click downloads the new `.exe`, checksum-verifies it, swaps it in place, and relaunches. Shows a banner on Home when a newer build is available. |
| **Reverse input lookup ("what does this do?")** | In Controls & Devices, press a key in the capture box (or type "Btn 5" / "Axis 3" / "Alt+P") to see which action(s) it's bound to — or "free". Also `gfcc whatis "<input>"` on the CLI. |
| **First-run wizard** | On first launch (no backups yet) a friendly multi-step overlay walks new users through: welcome → confirm the iRacing folder → make the first backup → optionally enable auto-backup → done. Re-openable from Settings ("Run setup wizard"); an `onboarded` flag stops it reappearing. |
| **Config linter / sanity check** | A "things worth a look" card in Game Settings that flags risky config — advisory and conservative, each finding explains *why*. Rules: `maxWorkingSetMB*` set above your installed RAM (psutil), plus consolidated binding conflicts and disconnected/moved-USB devices. Pure rule table in `lint.py` (easy to extend) + `GuiApi.run_config_lint`. |
| **Snapshot notes / annotations** | Attach a free-text note to any backup (a tuning journal) from its detail panel — shown inline in history and the note text is searchable. Stored in a `state/notes.json` sidecar keyed by commit; `GuiApi.set_note` / surfaced via `get_history`. |
| **Session change report** | A "Sessions" tab in Backup History groups backups into driving sessions (a run of sim-involved snapshots sharing a car/track, ended by sim exit) using the captured car/track/trigger. Each session card shows car @ track, time range, and backup count; expand it for a before-vs-after semantic diff of exactly what you changed that session (reuses the compare engine). `GuiApi.list_sessions`. |
| **Binding inventory** | A "Inventory" button in Controls & Devices opens a clean, human-readable list of every assignment grouped by device (each wheel/pedal, then Keyboard), profile-aware, with a one-click **Copy** (plain text) for pasting into Discord/forums. Built client-side from the decoded bindings. |
| **Configuration timeline (chart)** | A high-level chronological view in Backup History (List ⇄ Timeline toggle): a backup-activity bar chart (per day, last 1–3 weeks) + a color-coded, day-grouped event spine — Known-good / Saved Setup / Restore / Manual backup / After-a-session / Auto backup, each with a colored dot, time, and summary; click an event for its detail. Pure view over the existing history. |
| **Light / dark theme** | Dark and light modes via a top-bar sun/moon toggle, persisted in localStorage and applied before first paint (no flash). Light palette is a `:root[data-theme="light"]` override of the centralized CSS variables. |
| **Config blame (controls + settings)** | "When did this last change?" — click any control (Controls & Devices) **or** any INI setting (the new **Game Settings** view) to see its full change timeline: each value with when, the trigger (manual / sim-exit / auto), car/track context, and the backup note; current value tagged "now". Game Settings defaults to "recently changed settings" (value changes only, ignored keys filtered) and has a search across all INI keys. Walks the file's git history (controls rename-aware via `git log --follow`). `GuiApi.blame_control` / `blame_setting` / `list_settings`. |
| **Known-good restore points** | Mark the current setup as "verified good in a real session" (a reserved `known-good/<timestamp>` tag namespace, kept separate from Saved Setups), then one-click **Revert to last known-good** from the Home dashboard. A "✓ Known-good" badge appears on those backups in History. Built on tags + `restore_baseline`; revert is sim-running-guarded + takes a safety backup first. |
| **iRacing Control Profiles support** | iRacing's Control Profiles feature (May 2026) relocated controls.cfg/joyCalib.yaml into `profiles\controls\<name>\`, with the active profile named in `app.ini [ControlProfiles] Global`. The tracker resolves the live files through the active profile **and versions every profile independently** (one history per profile via `profiles/controls/<name>/…` repo keys), labels active-profile switches in history, and migrates pre-profiles history in place (rename, not delete+add). Per-profile labels in the UI (e.g. "Controls & Force Feedback · Oval profile"). The Controls & Devices view has a **profile picker** to browse any control profile's bindings/devices without switching the active one in-sim (default = active; a banner warns when viewing a non-active profile). The app's own tag-based feature was renamed **"Saved Setups"** to avoid the name clash. |

---

## ⭐ Flagship features (high value, foundation mostly in place)

| Feature | Value | Effort | Foundation / notes |
|---|---|---|---|
| **In-app controls editor — keyboard binds** — click an action, press a key, save. | High | Medium | Encoder already supports keyboard patch mode (`apply_bindings`); mostly frontend. |
| **In-app controls editor — FFB sliders** — strength / min-force / damping like the screenshot. | High | Hard | Blocked on reverse-engineering the opaque 147-byte `global_config_hex` blob. High payoff, real RE risk. |
| **Per-car / per-track configs** — separate config sets per car and/or track combo. | High | Hard | iRacing controls are largely global; needs careful modelling. Pairs with profiles. |
| **Car setup (`.sto`) tracking** — version per-car/track setups with the same engine. | High | Medium | New tracked-file patterns; out of v1 scope but probably the #1 user ask. |

---

## 🔍 History, diffing & insight

| Feature | Value | Effort | Foundation / notes |
|---|---|---|---|
| **Configuration history search** — find every snapshot where a key/section/action/value changed. | Med | Medium | Same history-walk machinery as blame. |
| **Automatic iRacing build-upgrade detection** — record the active build per snapshot; auto-annotate + summarise what the seasonal update changed. | High | Med–Hard | Need a reliable source for the build version (file/registry — TBD). Great for "did I break it or did iRacing?". |
| **Session outcome linking** — poll incidents / best lap at sim exit and store with the snapshot ("FFB I used for my Spa PB"). | Med | Medium | Already poll pyirsdk for car/track (FR-6); extend to results. |

---

## 🩺 Safety & diagnostics

| Feature | Value | Effort | Foundation / notes |
|---|---|---|---|
| **Orphaned-file cleanup** — detect files in `Documents\iRacing` no longer used by the current build / uninstalled content. | Low–Med | Medium | Hard to know "unused" reliably. |

---

## 📤 Sharing & community

| Feature | Value | Effort | Foundation / notes |
|---|---|---|---|
| **Config recipes / partial restore** — export/import a *subset* (e.g. "VR graphics optimization") that patches only the relevant keys, leaving FFB/controls untouched. | High | Medium | Builds on export + the INI/key model. |
| **Share / import controls profiles** — import a friend's setup; small library of known-good `controls.cfg` per wheelbase. | Med | Medium | Portable export zips already exist. |
| **Cloud paste integration** — `gfcc share` uploads a diff/patch and returns a link (handy for Reddit/forum help). | Med | Easy–Med | Wraps existing diff/JSON output. |
| **Configuration documentation export** — export current setup to Markdown/HTML/PDF (bindings, devices, key values) for archiving/teammates. | Med | Easy (MD) / Med (PDF) | Decoder + devices report already produce the data. |
| **Discord webhook on snapshot** — nice for streamers/leagues. | Low–Med | Easy | Toast/notify path already exists. |

---

## ☁️ Distribution & UX polish

| Feature | Value | Effort | Foundation / notes |
|---|---|---|---|
| **System-tray presence** — a tray icon with "Back up now" / status so it runs quietly in the background. (App icon itself: done.) | Med | Medium | App icon already shipped. |
| **Code-sign the exe** — kill the SmartScreen "unknown publisher" warning. | Med | Hard* | *Technically easy; needs a paid signing cert + process. |
| **Cloud sync / multi-PC** — "back up to a private remote" button (survives reinstall; syncs PCs). | Med | Medium | Store is already a plain git repo. |
| **In-sim overlay hook** — write a tiny JSON/text file (active config tag, e.g. `[FFB: Porsche GT3 Base]`) that SimHub/RaceLab can display. | Med | Easy | Just emit a status file. |

---

## 🔬 Long-term / research

| Feature | Value | Effort | Foundation / notes |
|---|---|---|---|
| **Telemetry-driven FFB suggestions** — parse `.ibt`, cross-reference clipping with FFB history to suggest strength tweaks. | High | Hard | Big new subsystem; depends on FFB decode landing first. |
| **Decode the FFB/calibration blob** — unlocks the FFB editor, linter checks on FFB, and the above. | High | Hard | The single biggest enabler; pure reverse-engineering effort. |