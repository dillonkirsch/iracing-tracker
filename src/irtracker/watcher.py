"""Headless watcher (M4): filesystem events + debounce, startup/sim-exit/resume
scans, sim process detection, car/track enrichment, toasts.

Control is file-based so the CLI can talk to a watcher launched by the logon
scheduled task: state\\watcher.json is the heartbeat, state\\paused and
state\\stop are flags (FR-28).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from irtracker import notify
from irtracker.config import Config
from irtracker.repo import TRIGGER_LABELS, SnapshotMeta
from irtracker.simstate import ContextCache, sim_running
from irtracker.snapshot import Tracker

log = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 5.0


# -- control files -------------------------------------------------------------

def _state_file(cfg: Config) -> Path:
    return cfg.state_dir / "watcher.json"


def _paused_flag(cfg: Config) -> Path:
    return cfg.state_dir / "paused"


def _stop_flag(cfg: Config) -> Path:
    return cfg.state_dir / "stop"


def read_state(cfg: Config) -> dict | None:
    try:
        return json.loads(_state_file(cfg).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def watcher_alive(cfg: Config) -> bool:
    import psutil

    state = read_state(cfg)
    if not state:
        return False
    pid = state.get("pid")
    return bool(pid) and psutil.pid_exists(pid)


def request_pause(cfg: Config) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    _paused_flag(cfg).touch()


def request_resume(cfg: Config) -> None:
    _paused_flag(cfg).unlink(missing_ok=True)


def request_stop(cfg: Config) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    _stop_flag(cfg).touch()


# -- the watcher --------------------------------------------------------------

class Watcher:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.tracker = Tracker(cfg)
        self.context = ContextCache(cfg.state_dir)
        self._pending: set[str] = set()
        self._last_event = 0.0
        self._lock = threading.Lock()
        self._sim_was_running = False
        self._sim_exit_scan_due: float | None = None
        self._last_sim_poll = 0.0
        self._last_sdk_poll = 0.0
        self._last_rescan = time.monotonic()
        self._last_heartbeat = 0.0
        self._last_snapshot: str | None = None
        self._started = datetime.now().astimezone().isoformat(timespec="seconds")

    # -- filesystem events ----------------------------------------------------

    def _on_fs_event(self, path_str: str) -> None:
        name = Path(path_str).name
        tp = self.cfg.policy_for(name)
        if tp is None or tp.policy == "ignore":
            return
        with self._lock:
            self._pending.add(name)
            self._last_event = time.monotonic()

    def _make_handler(self):
        from watchdog.events import FileSystemEventHandler

        watcher = self

        class Handler(FileSystemEventHandler):
            def on_created(self, event):
                if not event.is_directory:
                    watcher._on_fs_event(event.src_path)

            def on_modified(self, event):
                if not event.is_directory:
                    watcher._on_fs_event(event.src_path)

            def on_deleted(self, event):
                if not event.is_directory:
                    watcher._on_fs_event(event.src_path)

            def on_moved(self, event):
                if not event.is_directory:
                    watcher._on_fs_event(event.src_path)
                    watcher._on_fs_event(event.dest_path)

        return Handler()

    # -- snapshot plumbing -------------------------------------------------------

    def _sim_running_now(self) -> bool:
        return sim_running(self.cfg.sim_processes)

    def _snapshot(self, trigger: str, names: set[str] | None = None) -> None:
        running = self._sim_was_running if trigger == "sim_exit" else self._sim_running_now()
        sim_involved = running or trigger == "sim_exit"
        car = self.context.context.car if sim_involved else None
        track = self.context.context.track if sim_involved else None
        try:
            result = self.tracker.take_snapshot(
                trigger, names=names, sim_running=running and trigger != "sim_exit",
                car=car, track=track)
        except Exception:
            log.exception("snapshot failed (trigger=%s)", trigger)
            return
        if result.committed:
            self._last_snapshot = datetime.now().astimezone().isoformat(timespec="seconds")
            if self.cfg.notifications:
                meta = SnapshotMeta(trigger=trigger, files=result.files,
                                    sim_running=running, car=car, track=track)
                notify.snapshot_toast(
                    result.files, TRIGGER_LABELS.get(trigger, trigger),
                    meta.context_label())

    def _heartbeat(self, paused: bool, running: bool) -> None:
        self.cfg.state_dir.mkdir(parents=True, exist_ok=True)
        _state_file(self.cfg).write_text(json.dumps({
            "pid": os.getpid(),
            "started": self._started,
            "paused": paused,
            "sim_running": running,
            "car": self.context.context.car,
            "track": self.context.context.track,
            "last_snapshot": self._last_snapshot,
            "updated": datetime.now().astimezone().isoformat(timespec="seconds"),
        }, indent=2), encoding="utf-8")

    # -- main loop ----------------------------------------------------------------

    def run(self) -> int:
        if watcher_alive(self.cfg):
            log.error("another watcher is already running (pid %s)",
                      (read_state(self.cfg) or {}).get("pid"))
            return 2
        _stop_flag(self.cfg).unlink(missing_ok=True)
        if not self.cfg.iracing_dir.is_dir():
            log.error("iRacing folder not found: %s", self.cfg.iracing_dir)
            return 2

        self.tracker.ensure_repo()
        self._sim_was_running = self._sim_running_now()
        self._heartbeat(paused=_paused_flag(self.cfg).exists(),
                        running=self._sim_was_running)

        from watchdog.observers import Observer

        observer = Observer()
        handler = self._make_handler()
        observer.schedule(handler, str(self.cfg.iracing_dir), recursive=False)
        # iRacing's control profiles store controls.cfg/joyCalib.yaml in a
        # subfolder; watch it (recursively, to cover every profile) so rebinds
        # are caught as events, not only by the periodic rescan.
        profiles = self.cfg.iracing_dir / "profiles" / "controls"
        if profiles.is_dir():
            observer.schedule(handler, str(profiles), recursive=True)
        observer.start()
        log.info("watching %s (debounce %.0fs, sim poll %.0fs)",
                 self.cfg.iracing_dir, self.cfg.debounce_seconds, self.cfg.sim_poll_seconds)

        # Startup scan covers changes made while the watcher was not running (FR-4).
        self._snapshot("startup_scan")

        was_paused = _paused_flag(self.cfg).exists()
        try:
            while True:
                if _stop_flag(self.cfg).exists():
                    _stop_flag(self.cfg).unlink(missing_ok=True)
                    log.info("stop requested; exiting")
                    break
                now = time.monotonic()
                paused = _paused_flag(self.cfg).exists()

                if was_paused and not paused:
                    log.info("resumed; running catch-up scan")
                    self._snapshot("resume_scan")
                was_paused = paused

                # Sim state polling (FR-5) and the exit-transition scan (FR-4).
                if now - self._last_sim_poll >= self.cfg.sim_poll_seconds:
                    self._last_sim_poll = now
                    running = self._sim_running_now()
                    if self._sim_was_running and not running:
                        log.info("sim exited; scheduling exit scan")
                        self.context.shutdown_sdk()
                        self._sim_exit_scan_due = now + self.cfg.debounce_seconds
                    self._sim_was_running = running

                # Car/track cache refresh while the sim is up (FR-6).
                if self._sim_was_running and now - self._last_sdk_poll >= self.cfg.sdk_poll_seconds:
                    self._last_sdk_poll = now
                    self.context.poll()

                if paused:
                    with self._lock:
                        self._pending.clear()
                    self._sim_exit_scan_due = None
                else:
                    if self._sim_exit_scan_due is not None and now >= self._sim_exit_scan_due:
                        self._sim_exit_scan_due = None
                        with self._lock:
                            self._pending.clear()  # superseded by the full scan
                        self._snapshot("sim_exit")

                    with self._lock:
                        due = (self._pending
                               and now - self._last_event >= self.cfg.debounce_seconds)
                        names = set(self._pending) if due else None
                        if due:
                            self._pending.clear()
                    if names:
                        self._snapshot("event", names=names)

                    if (self.cfg.poll_fallback_seconds > 0
                            and now - self._last_rescan >= self.cfg.poll_fallback_seconds):
                        self._last_rescan = now
                        self._snapshot("rescan")

                if now - self._last_heartbeat >= HEARTBEAT_SECONDS:
                    self._last_heartbeat = now
                    self._heartbeat(paused=paused, running=self._sim_was_running)

                time.sleep(1.0)
        except KeyboardInterrupt:
            log.info("interrupted; exiting")
        finally:
            observer.stop()
            observer.join(timeout=5)
            _state_file(self.cfg).unlink(missing_ok=True)
        return 0
