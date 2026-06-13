# iRacing Config Tracker - Requirements

**Version:** 0.2 | **Date:** 2026-06-11 | **Owner:** Dillon
**Status:** Decisions locked, ready to build against. Changes from v0.1 in section 12.

## 1. Purpose

Version control for iRacing configuration files: automatic change detection, human-readable history and diffs, version-based restore, and a decode/encode toolchain for the binary `controls.cfg`. Effectively "git for `Documents\iRacing`" plus a GFCC codec.

## 2. Goals

- Never lose a working config (FFB tuning, control bindings, graphics setup) to a sim update, auto-config wizard rerun, or iRacing "forgetting" controls.
- See exactly what changed, when, at the INI section/key level.
- Attribute changes to context: which car and track were active, not just a timestamp.
- Roll back any single file to a prior version, or the whole set to a named baseline.
- Make `controls.cfg` inspectable as JSON and patchable from a declarative keyboard-bindings JSON.

## 3. Non-goals (v1)

- Car setups (`.sto`), paints, replays, telemetry files.
- Third-party tool configs (SimHub, Crew Chief, OBS, Stream Deck). Candidate for v2.
- Cloud sync / multi-machine.
- Editing INI values inside the tool. Editing happens in the sim or a text editor; the tool tracks and restores.
- From-scratch `controls.cfg` authoring and wheel/pedal (axis/button) binding generation. v1 generation is keyboard-bind patching of an existing file (section 7); full device support is v2.
- Any write into the live iRacing folder while the sim is running. Hard block, no override flag.

## 4. Tracked files

| File | Format | Written by | Churn | Policy (default) |
|---|---|---|---|---|
| `app.ini` | INI (~37 sections) | UI/sim on settings change and exit | Medium | Track. Starter key ignore list for volatile window-geometry keys; expand after observing real diffs |
| `rendererDX11*.ini` | INI | Graphics auto-config wizard and sim | Low | Track via glob (covers `rendererDX11Monitor.ini` and per-display variants) |
| `controls.cfg` | Binary (GFCC) | Sim on control assignment save | Low | Track raw bytes + committed decoded JSON sidecar + git textconv for direct diffs |
| `joyCalib.yaml` | YAML | In-sim calibration wizard | Low | Track; paired-restore option with `controls.cfg` (axes reference calibration) |
| `core.ini` | INI | Sim | Low | Track |
| `fueldata.ini` | INI | Sim as fuel learning accrues | High | Track-collapsed (per car/track data, complements car tagging). One-line toggle to ignore |
| `camera.ini` | INI | Sim every session | Very high | Ignore. One-line toggle to track-collapsed |

The tracked set is config-driven; new text files can be added without code changes. Open item: per-car custom control files, if any (section 11, item 1).

## 5. Functional requirements

### Change detection

- **FR-1:** Watch the iRacing folder using filesystem events (`watchdog` / ReadDirectoryChangesW) with a configurable polling fallback. Handle create, modify, and delete events. Deletions (e.g. a wiped `joyCalib.yaml`) are recorded as versions and are restorable.
- **FR-2:** Debounce window (default 10 s, configurable) to coalesce multi-file write bursts (sim exit) into one snapshot.
- **FR-3:** Handle locked or partially written files: retry with backoff; snapshot only once file size/mtime are stable across reads.
- **FR-4:** Scan-and-commit runs at watcher start (logon) and on the sim-exit transition, covering changes made while the watcher was not running. Live events cover hand edits in between.
- **FR-5:** Detect sim state via process check (e.g. `iRacingSim64DX11.exe`, `iRacingUI.exe`), polled by the watcher; sim-running flag recorded on every snapshot.
- **FR-6:** Context enrichment: while the sim runs, poll the iRacing SDK (shared memory via `pyirsdk`) and cache the current car and track. Snapshots are stamped with the last-known car/track; the cache covers the exit write burst, when the SDK is already gone. Snapshots with no sim involvement are stamped "manual edit".

### Versioning

- **FR-7:** Snapshot metadata: timestamp, trigger (event / startup scan / sim exit / manual), files changed, sim-running flag, car/track context, optional message.
- **FR-8:** History browsing per file and across the set, filterable by file, tag, and car/track context (e.g. "show app.ini versions where car = Porsche 992 GT3").
- **FR-9:** Semantic diff for INI and YAML: added/removed/changed keys grouped by section, e.g. `[Force Feedback] steeringDampingFactor: 0.05 -> 0.10`. Raw line diff also available.
- **FR-10:** `controls.cfg` diffs render from decoded JSON, never raw bytes (sidecar + textconv, section 8).
- **FR-11:** Tags for named baselines ("good FFB baseline", "pre 2026 S3 build").
- **FR-12:** Manual snapshot command with a message.
- **FR-13:** Full retention by default; per-file collapse policy per section 4. Storage stays in low MB indefinitely.
- **FR-14:** Export any snapshot as a portable zip (new PC migration, sharing).

### Restore

- **FR-15:** Restore a single file to any selected version. Version/tag oriented; no time-based restore UI.
- **FR-16:** Restore a tagged baseline across the full tracked set.
- **FR-17:** Auto-snapshot the current state immediately before any restore.
- **FR-18:** Hard block on restore while the sim is running. No force flag.
- **FR-19:** Restores are byte-exact copies of stored blobs; the tool never re-serializes INI/YAML.

### controls.cfg codec

- **FR-20:** Decode full GFCC to a documented JSON representation: devices (GUIDs, names), action-to-input bindings, recognized metadata. Reverse engineering is largely complete; finish and document remaining fields.
- **FR-21:** Round-trip fidelity: unknown/unparsed regions carried as opaque blobs in the JSON; decode -> encode of an unmodified file is byte-identical. Enforced by golden-file tests against a corpus of real samples, including before/after captures of single-binding changes made in the sim.
- **FR-22:** v1 encode is patch mode: inputs are (a) a base `controls.cfg` and (b) a JSON keyboard-bindings file. Output preserves every non-keyboard binding byte-for-byte and adds/replaces only the specified keyboard binds. From-scratch authoring and axis/button generation are v2.
- **FR-23:** Device aliases resolve to instance GUIDs at encode time (primarily v2, when wheel/pedal generation lands). Devices must survive USB port changes without reconfiguration. A `devices` CLI command lists connected device names/GUIDs and what the base file references.
- **FR-24:** Write safety: `encode` writes to an output path by default. An explicit `--install` flag backs up the live file, then copies the new one into the iRacing folder, and is hard-blocked while the sim runs. Final validation is loading it in the sim.
- **FR-25:** Fail-safe: if a `controls.cfg` cannot be parsed (format change in a new build), raw byte versioning continues uninterrupted and the decoded view is marked unavailable for that version.

### Noise management

- **FR-26:** Per-file policy: `track`, `ignore`, or `track-collapsed` (consecutive changes squash into one entry). Defaults per section 4.
- **FR-27:** Per-file INI key/section ignore lists. Changes touching only ignored keys do not trigger a snapshot; values get picked up incidentally in the next real snapshot.

### Interface

- **FR-28:** Headless watcher process launched by a logon scheduled task. No tray in v1; `watcher pause|resume|status` via CLI covers the only thing a tray would add. Tray is a thin optional layer later.
- **FR-29:** CLI: `status`, `log`, `diff`, `show`, `restore`, `snapshot`, `tag`, `export`, `decode`, `encode`, `devices`, `watcher`.
- **FR-30:** Windows toast notifications (winotify) on detected change with a one-line summary. On by default, single config flag to disable.

## 6. Non-functional requirements

- Windows 10/11. Python 3.12+. No admin rights.
- Snapshot repo at `%LOCALAPPDATA%\iracing-config-tracker` by default, configurable (point it at a synced/backed-up folder if desired; it is just a git repo).
- Tolerates Documents redirected to OneDrive (locks, delayed writes; polling fallback covers missed notifications).
- Negligible idle footprint.
- Logging with rotation; explicit errors when a file cannot be read or parsed.
- Structured for future open-sourcing: codec and snapshot core as an importable library, CLI on top, zero hardcoded personal paths, TOML config file.

## 7. Bindings JSON (v1, keyboard patch mode)

```json
{
  "version": 1,
  "bindings": [
    { "action": "PitSpeedLimiter", "key": "p", "modifiers": ["alt"] },
    { "action": "ReplayPlayPause", "key": "space" },
    { "action": "BlackBoxFuel",    "key": "f4" }
  ]
}
```

CLI flow:

```
gfcc decode controls.cfg -o controls.json
gfcc encode --base controls.cfg --bindings my_binds.json -o controls.new.cfg
gfcc encode --base controls.cfg --bindings my_binds.json --install   # backup + install; refuses while sim runs
```

The `action` vocabulary is exactly the names the decoder emits, so `decode` output doubles as the reference for valid values, and decoder output is itself valid encoder input (edit and re-encode).

## 8. Architecture (locked)

- **Backend: git.** Repo working tree mirrors the tracked files; one commit per snapshot; metadata (trigger, car/track, sim state) as a JSON trailer in the commit message; tags for baselines. `git log -p -- app.ini` works out of the box. Implementation via git CLI or `pygit2`, whichever is less friction.
- **controls.cfg readability in git, both mechanisms:** (a) a `controls.decoded.json` sidecar regenerated and committed on every controls.cfg change, so history is readable anywhere; (b) `.gitattributes` marks `controls.cfg diff=gfcc` with `diff.gfcc.textconv` pointing at the decoder, so plain `git diff` renders the binary as JSON.
- **Watcher:** `watchdog` events + debounce; `psutil` poll (15-30 s) for sim state transitions; `pyirsdk` polling while the sim is up for the car/track cache.
- **INI semantic diff:** thin order-preserving parser (stdlib `configparser` is lossy on ordering/case). Parsing is for diffing and ignore lists only; the tool never writes INI (FR-19).
- **Packaging:** venv + scheduled task now. PyInstaller onefile later if it goes public.

## 9. Risks

- **GFCC residual work:** RE is mostly done; remaining risk is unfinished fields and seasonal format drift. FR-21 golden tests are the guardrail; FR-25 keeps the tracker fully useful if the codec breaks on a new build.
- **Exit-burst timing vs SDK availability:** the sim writes configs as it closes, after the SDK shared memory is gone. Mitigated by the cached car/track (FR-6).
- **Device GUID drift:** avoided in v1 (keyboard scope); handled in v2 by encode-time alias resolution (FR-23).
- **OneDrive interference** with `Documents\iRacing`.
- **Seasonal builds add/rename keys**, producing one large "build update" diff per season. Trigger and car/track metadata keep history legible; stamping snapshots with the iRacing build version is a nice-to-have if a reliable local source turns up.

## 10. Milestones

- **M1:** Finish GFCC codec + golden-file round-trip tests. Corpus: current file, single-binding before/after deltas captured in the sim, a keyboard-only sample.
- **M2:** Snapshot core: repo init, manual `snapshot` / `log` / `diff` / `restore` for the text files.
- **M3:** Codec integration: decoded sidecar + textconv; `controls.cfg` fully in history.
- **M4:** Watcher: events, debounce, startup and sim-exit scans, sim detection, car/track enrichment, toasts, scheduled-task install.
- **M5:** Encode patch mode + `--install` flow.
- **M6 (v2):** Wheel/pedal binding generation with alias resolution, `joyCalib.yaml` pairing, from-scratch authoring, per-car profiles, public packaging.

## 11. Remaining open items

1. **Per-car custom controls:** if you use iRacing's "use custom controls for this car," check whether per-car control files appear in `Documents\iRacing`. If so, they join the tracked glob and the codec test corpus.
2. **app.ini starter ignore list:** run a few sessions, review the diffs, then lock the volatile-key list.
3. **Action-name vocabulary:** finalize from decoder output; drives JSON validation in `encode`.

## 12. Changes from v0.1

- Restore reframed as version/tag based; time-based language removed.
- Hard block (no override) on all live-folder writes while the sim runs (restore and encode `--install`).
- New FR-6: car/track context enrichment via cached iRacing SDK reads; history filterable by car.
- Noise defaults locked: `camera.ini` ignored, `fueldata.ini` track-collapsed, `app.ini` fully tracked with ignore-list support.
- Offline coverage defined as startup scan + sim-exit scan, plus live events.
- v1 encode scope locked to keyboard patch mode against a base file, preventing generated files from wiping wheel bindings. Full-device generation moved to v2.
- Backend locked: git, with committed decoded sidecar plus textconv for `controls.cfg`.
- Runtime locked: headless watcher via logon scheduled task + CLI; tray deferred.
- GFCC risk downgraded: reverse engineering largely complete per owner.
