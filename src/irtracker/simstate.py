"""Sim process detection (FR-5) and car/track context enrichment (FR-6).

The car/track cache is persisted to state\\context.json so the exit write
burst -- which happens after the SDK shared memory is gone -- still gets
stamped with the last-known car and track.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


def sim_running(process_names: list[str]) -> bool:
    import psutil

    wanted = {n.lower() for n in process_names}
    for proc in psutil.process_iter(["name"]):
        try:
            if (proc.info["name"] or "").lower() in wanted:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


@dataclass
class SimContext:
    car: str | None = None
    track: str | None = None
    updated: str | None = None


class ContextCache:
    """Polls the iRacing SDK while the sim runs and caches car/track."""

    def __init__(self, state_dir: Path):
        self.path = state_dir / "context.json"
        self.context = self._load()
        self._ir = None
        self._sdk_failed = False

    def _load(self) -> SimContext:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return SimContext(car=d.get("car"), track=d.get("track"),
                              updated=d.get("updated"))
        except (OSError, json.JSONDecodeError):
            return SimContext()

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({
                "car": self.context.car, "track": self.context.track,
                "updated": self.context.updated,
            }), encoding="utf-8")
        except OSError as exc:
            log.warning("could not persist context cache: %s", exc)

    def poll(self) -> None:
        """Refresh car/track from the SDK; quietly keeps the old cache on failure."""
        if self._sdk_failed:
            return
        try:
            import irsdk
        except ImportError:
            log.warning("pyirsdk not installed; snapshots will lack car/track context")
            self._sdk_failed = True
            return
        try:
            if self._ir is None:
                self._ir = irsdk.IRSDK()
            ir = self._ir
            if not ir.is_initialized and not ir.startup():
                return
            if not ir.is_connected:
                return
            car = track = None
            driver_info = ir["DriverInfo"]
            if driver_info:
                idx = driver_info.get("DriverCarIdx")
                drivers = driver_info.get("Drivers") or []
                me = next((d for d in drivers if d.get("CarIdx") == idx), None)
                if me:
                    car = me.get("CarScreenName") or me.get("CarPath")
            weekend = ir["WeekendInfo"]
            if weekend:
                track = weekend.get("TrackName")
                config = weekend.get("TrackConfigName")
                if track and config and str(config).lower() not in ("", "none", "null"):
                    track = f"{track} ({config})"
            if car or track:
                changed = (car, track) != (self.context.car, self.context.track)
                self.context.car = car or self.context.car
                self.context.track = track or self.context.track
                self.context.updated = datetime.now().astimezone().isoformat(timespec="seconds")
                self._save()
                if changed:
                    log.info("sim context: %s @ %s", self.context.car, self.context.track)
        except Exception as exc:
            log.debug("SDK poll failed: %s", exc)

    def shutdown_sdk(self) -> None:
        """Drop the SDK handle when the sim exits so the next session reconnects."""
        if self._ir is not None:
            try:
                self._ir.shutdown()
            except Exception:
                pass
            self._ir = None
