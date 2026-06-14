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

---

## ⭐ Flagship features (high value, foundation mostly in place)

| Feature | Value | Effort | Foundation / notes |
|---|---|---|---|
| **In-app controls editor — keyboard binds** — click an action, press a key, save. | High | Medium | Encoder already supports keyboard patch mode (`apply_bindings`); mostly frontend. |
| **In-app controls editor — FFB sliders** — strength / min-force / damping like the screenshot. | High | Hard | Blocked on reverse-engineering the opaque 147-byte `global_config_hex` blob. High payoff, real RE risk. |
| **Config profiles** — named whole-setups ("Oval", "Road", "VR", "Triples") you swap with one click. | High | Easy–Med | Thin friendly layer over existing tags + `restore_baseline`. |
| **Per-car / per-track configs** — separate config sets per car and/or track combo. | High | Hard | iRacing controls are largely global; needs careful modelling. Pairs with profiles. |
| **Car setup (`.sto`) tracking** — version per-car/track setups with the same engine. | High | Medium | New tracked-file patterns; out of v1 scope but probably the #1 user ask. |

---

## 🔍 History, diffing & insight

| Feature | Value | Effort | Foundation / notes |
|---|---|---|---|
| **Compare any two snapshots/tags** in the GUI ("known-good vs today", "since season start") — one aggregated report. | High | Easy | `get_diff` backend already supports arbitrary pairs; just needs a picker UI. |
| **Session change report** — what changed during a given driving session (car/track context already captured). | High | Easy–Med | `SnapshotMeta` already stores car/track/trigger. |
| **Config blame** — "when did this setting last change?" with value, time, trigger, car/track, notes. | High | Medium | Walk git history per key. |
| **Configuration history search** — find every snapshot where a key/section/action/value changed. | Med | Medium | Same history-walk machinery as blame. |
| **Snapshot notes / annotations** — attach searchable notes to any snapshot after the fact (turns it into a tuning journal). | High | Medium | Store via git notes or a sidecar; surface in history. |
| **Automatic iRacing build-upgrade detection** — record the active build per snapshot; auto-annotate + summarise what the seasonal update changed. | High | Med–Hard | Need a reliable source for the build version (file/registry — TBD). Great for "did I break it or did iRacing?". |
| **Session outcome linking** — poll incidents / best lap at sim exit and store with the snapshot ("FFB I used for my Spa PB"). | Med | Medium | Already poll pyirsdk for car/track (FR-6); extend to results. |
| **Configuration timeline** — high-level chronological view (snapshots, tags, restores, build upgrades) instead of raw commits. | Med | Easy–Med | View over the existing log; fits the GUI history tab. |

---

## 🩺 Safety & diagnostics

| Feature | Value | Effort | Foundation / notes |
|---|---|---|---|
| **Doctor / health check** — validate repo, watcher, file access, decoder, config, deps in one command, before you need a restore. | High | Easy–Med | Touches modules that already exist; mostly orchestration. |
| **Config linter / sanity checker** — warn on out-of-bounds INI values (e.g. `maxWorkingSetMB_64` > physical RAM, stale VR keys) that cause stutter/crashes. | High | Medium | Needs a small rules table + system info (RAM). |
| **Duplicate / conflicting binding detection** — flag inputs bound to multiple actions or accidental double-binds. | High | Easy–Med | Analyze decoded `controls.cfg` entries. |
| **Binding inventory** — human-readable list of every keyboard/wheel/pedal/button assignment. | Med | Easy | Codec already decodes; just format it. |
| **Known-good restore points** — a dedicated "verified in use" designation separate from tags, with a one-click "revert to last known-good". | High | Easy | Special tag namespace + restore shortcut. |
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
| **Auto-update** — check GitHub Releases and offer to download the newer `.exe`. | Med | Medium | Releases are now automated per push — perfect fit. In-place exe swap on Windows is the fiddly part. |
| **App icon + system-tray** — proper icon (currently default) and a tray presence with "Back up now" / status. | Med | Easy (icon) / Med (tray) | — |
| **First-run wizard** — detect the iRacing folder, offer auto-backup, make the first baseline. | Med | Easy–Med | Detection + snapshot already exist. |
| **Code-sign the exe** — kill the SmartScreen "unknown publisher" warning. | Med | Hard* | *Technically easy; needs a paid signing cert + process. |
| **Cloud sync / multi-PC** — "back up to a private remote" button (survives reinstall; syncs PCs). | Med | Medium | Store is already a plain git repo. |
| **Light theme toggle** — currently dark-only. | Low | Easy–Med | CSS variables already centralize the palette. |
| **In-sim overlay hook** — write a tiny JSON/text file (active config tag, e.g. `[FFB: Porsche GT3 Base]`) that SimHub/RaceLab can display. | Med | Easy | Just emit a status file. |

---

## 🔬 Long-term / research

| Feature | Value | Effort | Foundation / notes |
|---|---|---|---|
| **Telemetry-driven FFB suggestions** — parse `.ibt`, cross-reference clipping with FFB history to suggest strength tweaks. | High | Hard | Big new subsystem; depends on FFB decode landing first. |
| **Decode the FFB/calibration blob** — unlocks the FFB editor, linter checks on FFB, and the above. | High | Hard | The single biggest enabler; pure reverse-engineering effort. |