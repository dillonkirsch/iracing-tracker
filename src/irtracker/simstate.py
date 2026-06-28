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
    best_lap: float | None = None   # session best lap in seconds
    incidents: int | None = None    # driver incident count this session
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
                              best_lap=d.get("best_lap"), incidents=d.get("incidents"),
                              updated=d.get("updated"))
        except (OSError, json.JSONDecodeError):
            return SimContext()

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({
                "car": self.context.car, "track": self.context.track,
                "best_lap": self.context.best_lap, "incidents": self.context.incidents,
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
            # Session result so far: best lap (seconds) and incident count.
            raw_best = ir["LapBestLapTime"]
            best_lap = float(raw_best) if isinstance(raw_best, (int, float)) and raw_best > 0 else None
            raw_inc = ir["PlayerCarMyIncidentCount"]
            incidents = int(raw_inc) if isinstance(raw_inc, (int, float)) and raw_inc >= 0 else None
            # New session? (different car/track, or the incident counter reset
            # downward) -> drop the carried-over result so it repopulates fresh.
            new_combo = (car and car != self.context.car) or (track and track != self.context.track)
            inc_reset = (incidents is not None and self.context.incidents is not None
                         and incidents < self.context.incidents)
            if new_combo or inc_reset:
                self.context.best_lap = None
                self.context.incidents = None
            prev = (self.context.car, self.context.track, self.context.best_lap, self.context.incidents)
            if car or track or best_lap is not None or incidents is not None:
                self.context.car = car or self.context.car
                self.context.track = track or self.context.track
                if best_lap is not None:
                    self.context.best_lap = best_lap
                if incidents is not None:
                    self.context.incidents = incidents
                now = (self.context.car, self.context.track,
                       self.context.best_lap, self.context.incidents)
                if now != prev:
                    self.context.updated = datetime.now().astimezone().isoformat(timespec="seconds")
                    self._save()
                    log.info("sim context: %s @ %s | best %.3fs · %sx",
                             self.context.car, self.context.track,
                             self.context.best_lap or 0.0,
                             self.context.incidents if self.context.incidents is not None else "?")
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
