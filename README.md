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
```

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
