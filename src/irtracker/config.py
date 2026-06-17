"""Configuration: TOML file at %LOCALAPPDATA%\\iracing-config-tracker\\config.toml.

Defaults are baked in; the config file overrides them. The tracked-file set is
config-driven (requirements section 4) so new text files can be added without
code changes.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import logging.handlers
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

APP_NAME = "iracing-config-tracker"

# Files git uses inside the snapshot repo working tree; never treated as tracked
# config files even if a glob would match them.
SIDECAR_NAME = "controls.decoded.json"

DEFAULT_SIM_PROCESSES = ["iRacingSim64DX11.exe", "iRacingSimAV2DX11.exe", "iRacingUI.exe"]

# iRacing's "control profiles" feature (rolled out 2025) moved the live controls
# out of the top-level folder and into profiles\controls\<name>\. The active
# profile is named in app.ini's [ControlProfiles] Global key. These two files
# follow the profile; everything else stays at the top of iracing_dir. Legacy
# installs (no profiles feature) keep them top-level, so resolution falls back.
PROFILE_RELATIVE_FILES = {"controls.cfg", "joycalib.yaml"}  # matched lowercase


CONTROLS_SUBDIR = "profiles/controls"  # iRacing's per-profile controls folder


def is_sidecar(name: str) -> bool:
    """True for the derived controls.decoded.json, at any depth (per-profile)."""
    from pathlib import PurePosixPath

    return PurePosixPath(name.replace("\\", "/")).name == SIDECAR_NAME


def control_profile_in_text(app_ini_text: str) -> str | None:
    """Active control-profile name from app.ini text ([ControlProfiles] Global)."""
    section: str | None = None
    for raw in app_ini_text.splitlines():
        line = raw.split(";", 1)[0].strip()  # iRacing .ini uses ';' inline comments
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if section == "controlprofiles" and "=" in line:
            key, _, val = line.partition("=")
            if key.strip().lower() == "global":
                return val.strip() or None
    return None


def active_control_profile(iracing_dir: Path) -> str | None:
    """Active control-profile name, or None on legacy installs that keep
    controls.cfg at the top level."""
    try:
        text = (iracing_dir / "app.ini").read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    return control_profile_in_text(text)

DEFAULT_CONFIG_TOML = """\
# iRacing Config Tracker configuration.
# Relative patterns are resolved against iracing_dir.

[paths]
# Leave empty to auto-detect Documents\\iRacing (OneDrive redirection handled).
iracing_dir = ""
# Snapshot repo + state + logs live here. It is just a git repo; point it at a
# synced/backed-up folder if desired.
data_dir = ""

[watcher]
debounce_seconds = 10.0
# Periodic full-rescan fallback for missed filesystem notifications (OneDrive
# etc.). 0 disables; otherwise seconds between rescans.
poll_fallback_seconds = 300.0
# How often to check whether the sim is running.
sim_poll_seconds = 20.0
# How often to refresh the car/track cache from the iRacing SDK while the sim runs.
sdk_poll_seconds = 10.0
notifications = true
sim_processes = ["iRacingSim64DX11.exe", "iRacingSimAV2DX11.exe", "iRacingUI.exe"]

# Per-file policy: "track", "ignore", or "track-collapsed" (consecutive changes
# squash into one history entry). "ignore_keys" entries are "Section/key" or
# "Section/*", case-insensitive; changes touching only ignored keys do not
# trigger a snapshot.

[[tracked]]
pattern = "app.ini"
policy = "track"
# Starter ignore list (requirements 11.2): review real diffs after a few
# sessions, then lock the volatile-key list. Example entries:
#   ignore_keys = ["Graphics/windowedXPos", "Replay/*"]
ignore_keys = []

[[tracked]]
pattern = "rendererDX11*.ini"
policy = "track"
# Window-geometry keys churn on every window move/resize.
ignore_keys = [
    "Display/windowedXPos",
    "Display/windowedYPos",
    "Display/windowedWidth",
    "Display/windowedHeight",
    "Display/windowedMaximized",
]

[[tracked]]
pattern = "controls.cfg"
policy = "track"

[[tracked]]
pattern = "joyCalib.yaml"
policy = "track"

[[tracked]]
pattern = "core.ini"
policy = "track"

[[tracked]]
pattern = "fueldata.ini"
policy = "track-collapsed"

[[tracked]]
pattern = "camera.ini"
policy = "ignore"
"""


@dataclass
class TrackedPattern:
    pattern: str
    policy: str = "track"  # track | ignore | track-collapsed
    ignore_keys: list[str] = field(default_factory=list)


@dataclass
class Config:
    iracing_dir: Path
    data_dir: Path
    debounce_seconds: float = 10.0
    poll_fallback_seconds: float = 300.0
    sim_poll_seconds: float = 20.0
    sdk_poll_seconds: float = 10.0
    notifications: bool = True
    sim_processes: list[str] = field(default_factory=lambda: list(DEFAULT_SIM_PROCESSES))
    tracked: list[TrackedPattern] = field(default_factory=list)

    @property
    def repo_dir(self) -> Path:
        return self.data_dir / "repo"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def state_dir(self) -> Path:
        return self.data_dir / "state"

    def live_path(self, name: str) -> Path:
        """On-disk path of a tracked key.

        A profile-relative key (``profiles/controls/<profile>/controls.cfg``)
        maps straight through. A bare ``controls.cfg`` / ``joyCalib.yaml``
        resolves to the *currently-active* control profile (used by the live
        Controls view and re-map), falling back to the top level on legacy
        installs. Every other name is a top-level file."""
        if "/" in name or "\\" in name:
            return self.iracing_dir / name
        if name.lower() in PROFILE_RELATIVE_FILES:
            profile = active_control_profile(self.iracing_dir)
            if profile:
                pdir = self.iracing_dir / "profiles" / "controls" / profile
                if pdir.is_dir():
                    return pdir / name
        return self.iracing_dir / name

    def policy_for(self, name: str) -> TrackedPattern | None:
        """Match a tracked key against tracked patterns by basename (first match
        wins). Keys may be bare names or profile-relative paths."""
        from fnmatch import fnmatch
        from pathlib import PurePosixPath

        base = PurePosixPath(name.replace("\\", "/")).name
        if base == SIDECAR_NAME:
            return None
        for tp in self.tracked:
            if fnmatch(base.lower(), tp.pattern.lower()):
                return tp
        return None

    def control_profiles_dir(self) -> Path:
        return self.iracing_dir / "profiles" / "controls"

    def control_profile_files(self) -> list[str]:
        """Profile-relative keys for every control profile's tracked files,
        e.g. ``profiles/controls/Oval/controls.cfg``."""
        out: list[str] = []
        pdir = self.control_profiles_dir()
        if not pdir.is_dir():
            return out
        for sub in sorted(p for p in pdir.iterdir() if p.is_dir()):
            for fname in ("controls.cfg", "joyCalib.yaml"):
                tp = self.policy_for(fname)
                if tp and tp.policy != "ignore" and (sub / fname).is_file():
                    out.append(f"{CONTROLS_SUBDIR}/{sub.name}/{fname}")
        return out

    def tracked_files_present(self) -> list[str]:
        """Tracked (non-ignore) keys present live: top-level files plus each
        control profile's files as profile-relative keys.

        Once iRacing's control-profiles layout is in use, the top-level
        controls.cfg/joyCalib.yaml are stale migration leftovers and are
        skipped in favour of the per-profile copies."""
        names: list[str] = []
        if not self.iracing_dir.is_dir():
            return names
        has_profiles = self.control_profiles_dir().is_dir()
        for entry in sorted(self.iracing_dir.iterdir()):
            if not entry.is_file():
                continue
            if has_profiles and entry.name.lower() in PROFILE_RELATIVE_FILES:
                continue  # superseded by the per-profile copy
            tp = self.policy_for(entry.name)
            if tp and tp.policy != "ignore":
                names.append(entry.name)
        names.extend(self.control_profile_files())
        return names


def _known_folder(folder_id: str) -> Path | None:
    """Resolve a Windows known folder (handles OneDrive Documents redirection)."""
    import uuid

    try:
        guid = (ctypes.c_byte * 16)(*uuid.UUID(folder_id).bytes_le)
        ppath = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(guid, 0, None, ctypes.byref(ppath)) == 0:
            path = Path(ppath.value)
            ctypes.windll.ole32.CoTaskMemFree(ppath)
            return path
    except Exception:
        pass
    return None


FOLDERID_DOCUMENTS = "fdd39ad0-238f-46af-adb4-6c85480369c7"


def detect_iracing_dir() -> Path | None:
    candidates = []
    docs = _known_folder(FOLDERID_DOCUMENTS)
    if docs:
        candidates.append(docs / "iRacing")
    home = Path.home()
    candidates += [
        home / "Documents" / "iRacing",
        home / "OneDrive" / "Documents" / "iRacing",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return None


def default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / APP_NAME


def config_path() -> Path:
    override = os.environ.get("IRTRACK_CONFIG")
    if override:
        return Path(override)
    return default_data_dir() / "config.toml"


def write_default_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")


def load_config(path: Path | None = None, create_default: bool = True) -> Config:
    """Load config, writing the commented default file on first run."""
    path = path or config_path()
    if not path.is_file():
        if not create_default:
            raise FileNotFoundError(f"config file not found: {path}")
        write_default_config(path)
        log.info("wrote default config to %s", path)

    # utf-8-sig: Notepad and PowerShell often save config edits with a BOM,
    # which tomllib's binary loader rejects.
    raw = tomllib.loads(path.read_text(encoding="utf-8-sig"))

    paths = raw.get("paths", {})
    iracing_dir = Path(paths["iracing_dir"]) if paths.get("iracing_dir") else None
    if iracing_dir is None:
        iracing_dir = detect_iracing_dir()
        if iracing_dir is None:
            raise SystemExit(
                "Could not find Documents\\iRacing. Set [paths] iracing_dir in " + str(path)
            )
    data_dir = Path(paths["data_dir"]) if paths.get("data_dir") else default_data_dir()

    w = raw.get("watcher", {})
    # A config without [[tracked]] sections gets the built-in defaults rather
    # than silently tracking nothing.
    tracked_raw = raw.get("tracked") or tomllib.loads(DEFAULT_CONFIG_TOML)["tracked"]
    tracked = [
        TrackedPattern(
            pattern=t["pattern"],
            policy=t.get("policy", "track"),
            ignore_keys=list(t.get("ignore_keys", [])),
        )
        for t in tracked_raw
    ]
    for t in tracked:
        if t.policy not in ("track", "ignore", "track-collapsed"):
            raise SystemExit(f"invalid policy {t.policy!r} for pattern {t.pattern!r}")

    return Config(
        iracing_dir=iracing_dir,
        data_dir=data_dir,
        debounce_seconds=float(w.get("debounce_seconds", 10.0)),
        poll_fallback_seconds=float(w.get("poll_fallback_seconds", 300.0)),
        sim_poll_seconds=float(w.get("sim_poll_seconds", 20.0)),
        sdk_poll_seconds=float(w.get("sdk_poll_seconds", 10.0)),
        notifications=bool(w.get("notifications", True)),
        sim_processes=list(w.get("sim_processes", DEFAULT_SIM_PROCESSES)),
        tracked=tracked,
    )


def setup_logging(cfg: Config, console: bool = False, level: int = logging.INFO) -> None:
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    fh = logging.handlers.RotatingFileHandler(
        cfg.logs_dir / "tracker.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(fh)
    if console:
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(ch)
