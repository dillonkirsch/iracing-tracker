"""Health check ("doctor"): validate the whole tracking environment up front,
so you confirm backups actually work *before* you need a restore instead of
discovering a problem mid-recovery.
"""
from __future__ import annotations

import importlib.util
import shutil
from dataclasses import dataclass

from irtracker.config import Config
from irtracker.gfcc import codec
from irtracker.gfcc.codec import GfccError
from irtracker.simstate import sim_running
from irtracker.snapshot import Tracker

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Check:
    name: str
    status: str          # ok | warn | fail
    detail: str = ""


def _have(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def run_checks(cfg: Config) -> list[Check]:
    """Run every health check against a loaded config and return the results."""
    checks: list[Check] = []
    add = checks.append

    add(Check("Git available", OK, "found on PATH") if shutil.which("git")
        else Check("Git available", FAIL, "git is not on PATH — backups can't be created"))

    if cfg.iracing_dir.is_dir():
        add(Check("iRacing folder", OK, str(cfg.iracing_dir)))
    else:
        add(Check("iRacing folder", FAIL, f"not found: {cfg.iracing_dir}"))

    try:
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        probe = cfg.data_dir / ".doctor-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        add(Check("Backup folder writable", OK, str(cfg.data_dir)))
    except OSError as exc:
        add(Check("Backup folder writable", FAIL, f"{cfg.data_dir}: {exc}"))

    repo = Tracker(cfg).repo
    try:
        if repo.initialized and repo.head():
            add(Check("Backup history", OK, f"{len(repo.log())} backup(s) recorded"))
        else:
            add(Check("Backup history", WARN, "no backups yet — make your first one"))
    except Exception as exc:  # pragma: no cover - defensive
        add(Check("Backup history", FAIL, f"repo error: {exc}"))

    present = cfg.tracked_files_present()
    unreadable = []
    for name in present:
        try:
            (cfg.iracing_dir / name).read_bytes()
        except OSError:
            unreadable.append(name)
    if not present:
        add(Check("Tracked files", WARN, "no tracked files found in the iRacing folder yet"))
    elif unreadable:
        add(Check("Tracked files", FAIL, f"unreadable: {', '.join(unreadable)}"))
    else:
        add(Check("Tracked files", OK, f"{len(present)} file(s) present and readable"))

    controls = cfg.iracing_dir / "controls.cfg"
    if controls.exists():
        try:
            codec.decode_bytes(controls.read_bytes())
            add(Check("Controls decoder", OK, "controls.cfg decodes and round-trips"))
        except (OSError, GfccError) as exc:
            add(Check("Controls decoder", WARN,
                      f"controls.cfg isn't decodable ({exc}); raw byte backups still work"))
    else:
        add(Check("Controls decoder", WARN, "no controls.cfg in the iRacing folder"))

    if sim_running(cfg.sim_processes):
        add(Check("Sim state", WARN, "iRacing is running — restores are paused until it closes"))
    else:
        add(Check("Sim state", OK, "iRacing is closed; restores are allowed"))

    from irtracker import tasksched, watcher as watcher_mod
    if watcher_mod.watcher_alive(cfg):
        paused = bool((watcher_mod.read_state(cfg) or {}).get("paused"))
        add(Check("Auto-backup", WARN if paused else OK, "paused" if paused else "running"))
    else:
        add(Check("Auto-backup", WARN, "not running — changes are only saved when you back up manually"))
    autostart = tasksched.installed_status()
    add(Check("Start at login", OK if autostart else WARN,
              ", ".join(autostart) if autostart else "not set to start automatically"))

    for module, label, why in [
        ("irsdk", "Car/track context (pyirsdk)", "car/track tagging while you drive"),
        ("winotify", "Notifications (winotify)", "desktop toast notifications"),
        ("webview", "Native window (pywebview)", "the native app window"),
    ]:
        add(Check(label, OK, "installed") if _have(module)
            else Check(label, WARN, f"not installed — {why} unavailable (optional)"))

    return checks


def summarize(checks: list[Check]) -> tuple[int, int]:
    """Return (failures, warnings)."""
    return (sum(1 for c in checks if c.status == FAIL),
            sum(1 for c in checks if c.status == WARN))
