"""In-sim overlay hook: emit a tiny status file that overlay tools (SimHub,
RaceLab, or anything that can read a file) can display while you drive.

Writes two files into the state dir, refreshed on backups / dashboard polls:
  - overlay.json : structured status (profile, pending, backups, build, ...)
  - overlay.txt  : a ready-to-display one-liner, e.g.
                   "iRacing Config: Baseline (backed up)"

Opt-in (Settings toggle, stored in state/ui.json) and entirely best-effort —
a write failure is logged and ignored, never breaking a snapshot.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

log = logging.getLogger(__name__)

JSON_NAME = "overlay.json"
TXT_NAME = "overlay.txt"


def paths(cfg):
    return cfg.state_dir / JSON_NAME, cfg.state_dir / TXT_NAME


def is_enabled(cfg) -> bool:
    try:
        prefs = json.loads((cfg.state_dir / "ui.json").read_text(encoding="utf-8"))
        return bool(prefs.get("overlay_enabled"))
    except (OSError, json.JSONDecodeError):
        return False


def render_text(status: dict) -> str:
    profile = status.get("profile") or "default"
    pending = status.get("pending") or 0
    state = f"{pending} unsaved" if pending else "backed up"
    return f"iRacing Config: {profile} ({state})"


def write(cfg, status: dict) -> None:
    """Write the overlay files from a status dict (best-effort, never raises)."""
    try:
        status = dict(status)
        status["app"] = "iRacing Config Tracker"
        status.setdefault("status", "unsaved" if status.get("pending") else "ok")
        status["text"] = render_text(status)
        status["updated"] = datetime.now().astimezone().isoformat(timespec="seconds")
        json_path, txt_path = paths(cfg)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        txt_path.write_text(status["text"] + "\n", encoding="utf-8")
    except OSError as exc:
        log.debug("overlay write failed: %s", exc)


def clear(cfg) -> None:
    for p in paths(cfg):
        try:
            p.unlink()
        except OSError:
            pass


def refresh(cfg) -> None:
    """Compute the status from the repo and write it (used by the watcher, where
    no dashboard data is at hand). No-op unless the overlay is enabled."""
    if not is_enabled(cfg):
        return
    from irtracker import build as build_mod
    from irtracker.config import active_control_profile
    from irtracker.snapshot import Tracker
    status = {"profile": active_control_profile(cfg.iracing_dir),
              "build": build_mod.current_build(), "pending": 0}
    try:
        tracker = Tracker(cfg)
        repo = tracker.repo
        if repo.initialized and repo.head():
            snaps = repo.log()
            status["backups"] = len(snaps)
            if snaps:
                status["lastBackup"] = snaps[0].author_date
        status["pending"] = len(tracker.live_changes())
    except Exception as exc:
        log.debug("overlay refresh failed: %s", exc)
    write(cfg, status)
