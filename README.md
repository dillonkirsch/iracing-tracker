# iRacing Config Tracker

Version control for iRacing configuration files: automatic change detection,
human-readable history and diffs, version-based restore, and a decode/encode
toolchain for the binary `controls.cfg`. Effectively "git for `Documents\iRacing`"
plus a GFCC codec. Full requirements in [requirements.md](requirements.md).

## What it protects

| File | Policy (default) |
|---|---|
| `app.ini` | tracked (with a configurable volatile-key ignore list) |
| `rendererDX11*.ini` | tracked; window-geometry keys ignored by default |
| `controls.cfg` | tracked as raw bytes **plus** a committed decoded-JSON sidecar |
| `joyCalib.yaml` | tracked |
| `core.ini` | tracked |
| `fueldata.ini` | tracked, consecutive changes collapsed into one entry |
| `camera.ini` | ignored (one-line config toggle to track) |

The tracked set is config-driven; add new text files without code changes.

## Install

Windows 10/11, Python 3.12+, git on PATH. No admin rights needed.

```powershell
cd iracing-tracker
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .[sim,toast]      # pyirsdk (car/track context) + winotify (toasts)
```

## Desktop app (no command line needed)

Prefer buttons to a terminal? Double-click **`start-gui.bat`** to open the
friendly window — a plain-language dashboard for backing up, browsing history,
restoring older versions, and viewing your controls and connected devices.
No git knowledge required: snapshots are "backups", tags are "saved setups",
diffs are "what changed".

For a real native app window (rather than a browser tab), install the optional
dependency once:

```powershell
pip install -e .[gui]      # adds pywebview; rides the built-in Edge WebView2
```

From a terminal it's `irtrack gui` (or the `irtrack-gui` script). Without
`pywebview` installed it opens in your default browser instead — same app, same
features (force browser mode any time with `IRTRACK_GUI_BROWSER=1`). All the
safety rules still apply: restores are blocked while the sim is running, and
every restore auto-backs-up first.

### Download or build the standalone .exe

Each version tag triggers a GitHub Actions workflow
([.github/workflows/release.yml](.github/workflows/release.yml)) that builds a
self-contained `iRacingConfigTracker.exe` (PyInstaller, one file) and attaches
it to a GitHub Release. End users just download and run it — no Python install
needed (Windows 10/11; the native window rides the built-in Edge WebView2 and
falls back to the browser otherwise). The exe is unsigned, so SmartScreen may
say "More info → Run anyway" the first time.

Cut a release:

```powershell
git tag v1.0.0
git push origin v1.0.0      # Actions builds the exe and publishes the release
```

You can also trigger the workflow by hand from the **Actions** tab to build
without releasing — the exe is saved as a downloadable build artifact. To build
locally:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build-exe.ps1   # -> dist\iRacingConfigTracker.exe
```

## Quick start

```powershell
irtrack snapshot -m "initial baseline"   # first snapshot of the live folder
irtrack tag good-baseline -m "known good"
irtrack watcher install                  # start automatically at logon
irtrack watcher run                      # ...or run it right now in this console
```

On first run a commented config file is written to
`%LOCALAPPDATA%\iracing-config-tracker\config.toml` (iRacing folder is
auto-detected, OneDrive-redirected Documents included). The snapshot repo
lives at `%LOCALAPPDATA%\iracing-config-tracker\repo` — it is a plain git
repo; point `data_dir` at a synced folder if you want offsite copies, and
`git log -p` works in it directly (`controls.cfg` renders as JSON via
textconv).

## Day-to-day

```powershell
irtrack status                      # pending changes, watcher + sim state
irtrack log                         # history; filter with --file --car --track --tags
irtrack log --car porsche           # "show versions where car = Porsche..."
irtrack diff                        # latest snapshot vs live folder (semantic)
irtrack diff HEAD~3 HEAD --file app.ini
irtrack show <ver> app.ini          # file content at a version
irtrack restore <ver> app.ini       # restore one file (auto-snapshots first)
irtrack restore --tag good-baseline # restore a named baseline across the set
irtrack export <ver> -o backup.zip  # portable snapshot (new PC migration)
irtrack watcher pause|resume|status
```

Semantic diffs are per-key for INI/YAML and per-binding for `controls.cfg`:

```
=== controls.cfg (good-baseline -> HEAD) ===
  PitSpeedLimiter: key A -> key Alt+P
[Force Feedback]
  steeringDampingFactor: 0.05 -> 0.10
```

Restores are byte-exact copies of stored versions; the tool never rewrites
INI/YAML content. All writes into the live folder are hard-blocked while the
sim (or the iRacing UI) is running — no override flag.

## controls.cfg codec (gfcc)

```powershell
gfcc decode controls.cfg -o controls.json
gfcc encode --base controls.cfg --bindings my_binds.json -o controls.new.cfg
gfcc encode --base controls.cfg --bindings my_binds.json --install   # backup + install; refuses while sim runs
gfcc devices                        # connected controllers vs what the file references
gfcc remap --auto --install         # fix bindings after a USB-port change / new PC
gfcc remap --from OLD_GUID --to NEW_GUID -o controls.new.cfg
gfcc whatis "Alt+P"                 # reverse lookup: what action is this bound to?
```

`remap` repoints every binding (and the matching `joyCalib.yaml` calibration)
from an old device instance GUID to a new one — the fix for iRacing losing your
wheel/pedal binds when the device returns under a new instance GUID. `--auto`
detects a single drifted device from `gfcc devices`; the GUI exposes the same
thing as a one-click **"Re-map to connected device"** button.

`decode` emits documented JSON (round-trip verified byte-identical before
anything is written). v1 `encode` is keyboard patch mode: it adds/replaces
only the keyboard binds named in the bindings file and preserves every
wheel/pedal binding byte-for-byte — it refuses to touch an action currently
bound to an axis or button.

```json
{
  "version": 1,
  "bindings": [
    { "action": "PitSpeedLimiter", "key": "p", "modifiers": ["alt"] },
    { "action": "TearOffVisor",    "key": "f6" }
  ]
}
```

Action names are exactly what `decode` emits, so decode output doubles as the
reference vocabulary (and is itself valid encoder input). Keys: letters,
digits, `f1`-`f12`, `space`, `enter`, `esc`, arrows, `numpad0`-`numpad9`,
punctuation, etc. Modifiers: `shift`, `ctrl`, `alt`.

If a new iRacing build changes the format, raw-byte versioning continues
uninterrupted and the decoded view is marked unavailable for those versions.

## Configuration

`%LOCALAPPDATA%\iracing-config-tracker\config.toml` — paths, debounce,
notification toggle, sim process names, and per-file policies:

```toml
[[tracked]]
pattern = "app.ini"
policy = "track"            # track | ignore | track-collapsed
ignore_keys = ["Display/windowed*"]   # Section/key globs; changes touching
                                      # only these keys don't trigger snapshots
```

## Development

```powershell
pip install -e .[dev]
python -m pytest tests
```

Golden-file tests round-trip the real `controls.cfg` corpus in
`tests/corpus/` byte-identically; add captures of new sim builds (and
before/after single-binding changes) there.

## v1 scope notes

Car setups, paints, replays, third-party tool configs, cloud sync, and
from-scratch `controls.cfg` authoring (wheel/pedal binding generation with
device-alias resolution) are out of scope for v1 — see requirements.md
sections 3 and 10 (M6).
