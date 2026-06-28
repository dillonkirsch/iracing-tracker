"""Desktop GUI (friendly front-end for the tracker).

A small, plain-language window over the same engine the CLI drives: see what's
protected, browse the backup timeline, view your controls and connected
devices, and restore older versions -- all without ever saying "git",
"commit", or "snapshot" to the user.

Launch order:
  1. a native window via pywebview (preferred -- looks and feels like an app);
  2. if pywebview isn't installed, a local web server + the default browser
     (zero extra dependencies, so it always works).

Both paths talk to the same `GuiApi`. The web layer calls methods by name; the
JS bridge in app.js auto-detects which transport it's running under.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any

from irtracker.config import (
    SIDECAR_NAME, Config, TrackedPattern, active_control_profile, config_path,
    is_sidecar, load_config)
from irtracker.gfcc import codec
from irtracker.gfcc.codec import GfccError
from irtracker.gfcc.patch import remap_device, remap_joycalib
from irtracker.repo import GitError, Snapshot
from irtracker.simstate import ContextCache, sim_running
from irtracker.snapshot import SimRunningError, Tracker, backup_live_file

log = logging.getLogger(__name__)

def _webui_dir() -> Path:
    """Locate the bundled web assets, whether running from source or a
    PyInstaller onefile build (assets extract to _MEIPASS/irtracker/webui)."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return base / "irtracker" / "webui"
    return Path(__file__).resolve().parent / "webui"


WEBUI_DIR = _webui_dir()
WINDOW_TITLE = "iRacing Config Tracker"


# -- result helpers --------------------------------------------------------------

def _ok(**data: Any) -> dict:
    return {"ok": True, **data}


def _err(message: str, **data: Any) -> dict:
    return {"ok": False, "error": message, **data}


# Reserved tag namespace for "known-good" restore points, kept separate from
# Saved Setups (which are plain user tags). Names are timestamped so the most
# recent sorts last lexically.
KNOWN_GOOD_PREFIX = "known-good/"


def _is_known_good(tag: str) -> bool:
    return tag.startswith(KNOWN_GOOD_PREFIX)


def _tag_slug(name: str) -> str:
    """Turn a user-typed name into a valid git tag/ref (no spaces or special
    chars). 'VR setup' -> 'VR-setup'."""
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", (name or "").strip())
    return re.sub(r"-{2,}", "-", s).strip("-_")


def _update_config_paths(text: str, iracing_dir: str, data_dir: str) -> str:
    """Rewrite the iracing_dir/data_dir values in a config.toml's [paths] table,
    preserving the rest of the file (comments and other settings)."""
    if not text.strip():
        text = "[paths]\n"
    for key, val in (("iracing_dir", iracing_dir), ("data_dir", data_dir)):
        line = f'{key} = "{val.replace(chr(92), "/")}"'
        pat = re.compile(rf"^[ \t]*{key}[ \t]*=.*$", re.M)
        if pat.search(text):
            text = pat.sub(lambda m, l=line: l, text, count=1)
        elif re.search(r"^\[paths\]", text, re.M):
            text = re.sub(r"^(\[paths\][ \t]*\n)", lambda m, l=line: m.group(1) + l + "\n",
                          text, count=1, flags=re.M)
        else:
            text = f"[paths]\n{line}\n" + text
    return text


def _toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _render_tracked_block(tp) -> str:
    out = (f"[[tracked]]\npattern = {_toml_str(tp.pattern)}\n"
           f"policy = {_toml_str(tp.policy)}\n")
    if tp.ignore_keys:
        keys = ", ".join(_toml_str(k) for k in tp.ignore_keys)
        out += f"ignore_keys = [{keys}]\n"
    return out


def _set_tracked_in_text(text: str, tracked: list) -> str:
    """Replace all [[tracked]] blocks in a config.toml with regenerated ones,
    preserving everything before the first [[tracked]] (so [paths]/[watcher] and
    their comments survive). Each block keeps its ignore_keys."""
    head: list[str] = []
    for line in text.splitlines():
        if line.strip() == "[[tracked]]":
            break
        head.append(line)
    head_text = "\n".join(head).rstrip("\n")
    blocks = "\n".join(_render_tracked_block(tp) for tp in tracked)
    return f"{head_text}\n\n{blocks}"


def _fmt_lap(seconds) -> str | None:
    """Seconds -> '1:38.234' (or '38.234' for sub-minute), or None if no lap."""
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return None
    m, s = divmod(float(seconds), 60)
    return f"{int(m)}:{s:06.3f}" if m else f"{s:.3f}"


def _pretty_action(name: str) -> str:
    """CamelCase action name -> spaced (mirrors the JS prettyAction)."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    return re.sub(r"([a-zA-Z])([0-9])", r"\1 \2", s)


def _bits(value: int) -> list[int]:
    out, i = [], 0
    while value:
        if value & 1:
            out.append(i)
        value >>= 1
        i += 1
    return out


# -- the bridge ------------------------------------------------------------------

class GuiApi:
    """Every method here is callable from the web layer by name and returns a
    JSON-serializable dict shaped as {"ok": bool, ...}. Nothing raises across
    the bridge -- failures come back as {"ok": False, "error": "..."}.
    """

    def __init__(self, config_arg: str | None = None):
        self._config_arg = config_arg
        self._cfg: Config | None = None
        self._cfg_error: str | None = None
        # Underscore-prefixed on purpose: pywebview serializes the PUBLIC
        # attributes of a js_api object into the JS bridge. A public reference
        # to the native Window would make it walk the WinForms/WebView2 object
        # and recurse forever (AccessibilityObject.Bounds.Empty.Empty...).
        self._window = None  # set by launch() when running under pywebview

    # -- config / tracker access -------------------------------------------------

    def _config(self) -> Config | None:
        if self._cfg is not None:
            return self._cfg
        try:
            path = Path(self._config_arg) if self._config_arg else None
            self._cfg = load_config(path)
            self._cfg_error = None
        except SystemExit as exc:
            self._cfg_error = str(exc)
        except Exception as exc:  # pragma: no cover - defensive
            self._cfg_error = str(exc)
        return self._cfg

    def _tracker(self) -> Tracker | None:
        cfg = self._config()
        return Tracker(cfg) if cfg else None

    # -- serialization helpers ---------------------------------------------------

    @staticmethod
    def _files_clean(files: dict[str, str]) -> dict[str, str]:
        return {n: k for n, k in files.items() if not is_sidecar(n)}

    def _snap_dict(self, s: Snapshot) -> dict:
        from irtracker.repo import TRIGGER_LABELS
        return {
            "rev": s.commit,
            "shortRev": s.short,
            "date": s.author_date,
            "trigger": s.meta.trigger,
            "triggerRaw": TRIGGER_LABELS.get(s.meta.trigger, s.meta.trigger),
            "contextLabel": s.meta.context_label(),
            "car": s.meta.car,
            "track": s.meta.track,
            "message": s.meta.message,
            "files": self._files_clean(s.meta.files),
            "tags": [t for t in s.tags if not _is_known_good(t)],
            "knownGood": any(_is_known_good(t) for t in s.tags),
            "collapsed": s.meta.collapsed,
            "build": s.meta.build,
            "bestLap": s.meta.best_lap,
            "bestLapStr": _fmt_lap(s.meta.best_lap),
            "incidents": s.meta.incidents,
        }

    # -- overview / dashboard ----------------------------------------------------

    def get_overview(self) -> dict:
        cfg = self._config()
        if cfg is None:
            return _err(self._cfg_error or "could not load configuration",
                        setupNeeded=True, configPath=str(config_path()))
        from irtracker import tasksched, watcher as watcher_mod

        tracker = Tracker(cfg)
        repo = tracker.repo
        running = sim_running(cfg.sim_processes)

        watcher = None
        if watcher_mod.watcher_alive(cfg):
            st = watcher_mod.read_state(cfg) or {}
            watcher = {
                "running": True,
                "paused": bool(st.get("paused")),
                "pid": st.get("pid"),
                "started": st.get("started"),
                "lastSnapshot": st.get("last_snapshot"),
                "car": st.get("car"),
                "track": st.get("track"),
            }

        autostart = tasksched.installed_status()

        latest = None
        snapshot_count = 0
        snaps = []
        if repo.initialized and repo.head():
            snaps = repo.log()
            snapshot_count = len(snaps)
            if snaps:
                latest = self._snap_dict(snaps[0])

        # "Did I break it or did iRacing?" — the most recent snapshot boundary
        # where the iRacing build changed (sim updates rewrite configs on their
        # own), unless the user already dismissed that build.
        from irtracker import build as build_mod
        current_build = build_mod.current_build()
        build_update = None
        ack = self._ui_prefs(cfg).get("ackBuild")
        for i in range(len(snaps) - 1):
            new_b, old_b = snaps[i].meta.build, snaps[i + 1].meta.build
            if new_b and old_b and new_b != old_b:
                if new_b != ack:
                    build_update = {
                        "fromBuild": old_b, "toBuild": new_b,
                        "atRev": snaps[i].commit, "beforeRev": snaps[i + 1].commit,
                        "date": snaps[i].author_date,
                        "files": self._files_clean(snaps[i].meta.files),
                    }
                break  # only the most recent build change

        pending = [{"name": n, "kind": k} for n, k in sorted(tracker.live_changes().items())]

        # Most recent known-good restore point (timestamped names sort
        # chronologically, so the max name is the latest -- resolve only that one).
        last_known_good = None
        if repo.initialized and repo.head():
            kg = [t for t in repo.list_tags() if _is_known_good(t[0])]
            if kg:
                name, commit, message = max(kg, key=lambda t: t[0])
                last_known_good = {"tag": name, "rev": commit,
                                   "label": message or "Known-good",
                                   "date": None, "contextLabel": ""}
                try:
                    s = repo.snapshot_at(commit)
                    last_known_good["date"] = s.author_date
                    last_known_good["contextLabel"] = s.meta.context_label()
                except Exception:
                    pass

        protected = []
        for tp in cfg.tracked:
            if tp.policy != "ignore":
                protected.append({"pattern": tp.pattern, "policy": tp.policy})
        tracked = [{"pattern": tp.pattern, "policy": tp.policy} for tp in cfg.tracked]

        return _ok(
            configPath=str(self._config_arg or config_path()),
            iracingDir=str(cfg.iracing_dir),
            iracingDirExists=cfg.iracing_dir.is_dir(),
            dataDir=str(cfg.data_dir),
            repoDir=str(cfg.repo_dir),
            repoInitialized=repo.initialized,
            simRunning=running,
            simProcesses=list(cfg.sim_processes),
            watcher=watcher,
            autostart=autostart,
            autostartOn=bool(autostart),
            latest=latest,
            pending=pending,
            lastKnownGood=last_known_good,
            tracked=tracked,
            currentBuild=current_build,
            buildUpdate=build_update,
            discord=self._discord_prefs(cfg),
            snapshotCount=snapshot_count,
            protected=protected,
            trayEnabled=bool(self._ui_prefs(cfg).get("tray", True)),
            onboarded=(cfg.state_dir / "onboarded").exists(),
        )

    # -- history -----------------------------------------------------------------

    def get_history(self, filters: dict | None = None) -> dict:
        tracker = self._tracker()
        if tracker is None:
            return _err(self._cfg_error or "could not load configuration")
        if not tracker.repo.initialized or not tracker.repo.head():
            return _ok(items=[])
        f = filters or {}
        snaps = tracker.filtered_log(
            path=f.get("file") or None,
            car=f.get("car") or None,
            track=f.get("track") or None,
            trigger=f.get("trigger") or None,
            tag_only=bool(f.get("tagsOnly")),
            limit=int(f["limit"]) if f.get("limit") else None,
        )
        notes = self._load_notes(tracker.cfg)
        items = [self._snap_dict(s) for s in snaps]
        for it in items:
            it["note"] = notes.get(it["rev"], "")
        return _ok(items=items)

    def get_changes(self, rev: str) -> dict:
        """What a single backup changed, compared with the one before it."""
        tracker = self._tracker()
        if tracker is None:
            return _err(self._cfg_error or "could not load configuration")
        repo = tracker.repo
        try:
            snap = repo.snapshot_at(rev)
        except Exception as exc:
            return _err(str(exc))
        parent_proc = repo.git("rev-parse", "--verify", f"{rev}^", check=False)
        parent = parent_proc.stdout.strip() if parent_proc.returncode == 0 else None
        files = []
        for name in sorted(self._files_clean(snap.meta.files)):
            body = self._file_diff(repo, name, parent, snap.commit, "previous", "this version")
            files.append({"name": name, "body": body})
        return _ok(rev=snap.commit, hasParent=parent is not None, files=files,
                   snapshot=self._snap_dict(snap))

    def get_pending_diff(self) -> dict:
        """What's different right now between the latest backup and live files."""
        tracker = self._tracker()
        if tracker is None:
            return _err(self._cfg_error or "could not load configuration")
        cfg = tracker.cfg
        repo = tracker.repo
        if not repo.initialized or not repo.head():
            return _ok(files=[])
        names = {n for n in repo.files_at("HEAD")} | set(cfg.tracked_files_present())
        names = {n for n in names if not is_sidecar(n)}
        files = []
        for name in sorted(names):
            old = repo.show_file("HEAD", name) if repo.file_exists_at("HEAD", name) else None
            live = cfg.live_path(name)
            new = live.read_bytes() if live.exists() else None
            if old == new:
                continue
            body = self._semantic(name, old, new, "last backup", "now")
            if body.strip():
                files.append({"name": name, "body": body})
        return _ok(files=files)

    def _comparison_files(self, rev_a: str, rev_b: str | None,
                          label_a: str, label_b: str) -> list[dict]:
        """Semantic diff between any two backups (rev_b None/'live' = live folder)."""
        tracker = self._tracker()
        if tracker is None:
            raise RuntimeError(self._cfg_error or "could not load configuration")
        repo, cfg = tracker.repo, tracker.cfg
        live = rev_b in (None, "", "live", "__live__")
        names = set(repo.files_at(rev_a))
        if live:
            names |= set(cfg.tracked_files_present())
        else:
            names |= set(repo.files_at(rev_b))
        names = {n for n in names if not is_sidecar(n)}
        files = []
        for name in sorted(names):
            old = repo.show_file(rev_a, name) if repo.file_exists_at(rev_a, name) else None
            if live:
                p = cfg.live_path(name)
                new = p.read_bytes() if p.exists() else None
            else:
                new = repo.show_file(rev_b, name) if repo.file_exists_at(rev_b, name) else None
            if old == new:
                continue
            body = self._semantic(name, old, new, label_a, label_b)
            if body.strip():
                files.append({"name": name, "body": body})
        return files

    def get_comparison(self, rev_a: str, rev_b: str | None,
                       label_a: str = "A", label_b: str = "B") -> dict:
        try:
            files = self._comparison_files(rev_a, rev_b, label_a, label_b)
        except Exception as exc:
            return _err(str(exc))
        return _ok(files=files, changedCount=len(files), labelA=label_a, labelB=label_b)

    def export_comparison_pdf(self, rev_a: str, rev_b: str | None,
                              label_a: str = "A", label_b: str = "B") -> dict:
        try:
            files = self._comparison_files(rev_a, rev_b, label_a, label_b)
        except Exception as exc:
            return _err(str(exc))
        try:
            from irtracker import report
            pdf = report.build_comparison_pdf(label_a, label_b, files, WEBUI_DIR / "logo.png")
        except Exception as exc:
            return _err(f"Couldn't build the PDF: {exc}")
        default_name = "iracing-config-comparison.pdf"
        dest: Path | None = None
        if self._window is not None:
            try:
                import webview
                picked = self._window.create_file_dialog(
                    webview.SAVE_DIALOG, save_filename=default_name,
                    file_types=("PDF document (*.pdf)",))
                if not picked:
                    return _ok(cancelled=True)
                dest = Path(picked if isinstance(picked, str) else picked[0])
            except Exception as exc:  # pragma: no cover
                log.warning("save dialog failed (%s); falling back to Desktop", exc)
        if dest is None:
            desktop = Path.home() / "Desktop"
            dest = (desktop if desktop.is_dir() else Path.home()) / default_name
        try:
            dest.write_bytes(pdf)
        except OSError as exc:
            return _err(str(exc))
        return _ok(path=str(dest), message=f"Saved comparison PDF to {dest}")

    def _file_diff(self, repo, name, rev_a, rev_b, label_a, label_b) -> str:
        old = repo.show_file(rev_a, name) if rev_a and repo.file_exists_at(rev_a, name) else None
        new = repo.show_file(rev_b, name) if repo.file_exists_at(rev_b, name) else None
        return self._semantic(name, old, new, label_a, label_b)

    @staticmethod
    def _semantic(name, old, new, label_a, label_b) -> str:
        from irtracker.cli import _semantic_file_diff
        try:
            return _semantic_file_diff(name, old, new, label_a, label_b, raw=False)
        except Exception as exc:  # pragma: no cover - defensive
            return f"(could not compare this file: {exc})"

    # -- actions: backup / restore / tags ----------------------------------------

    def backup_now(self, message: str | None = None) -> dict:
        tracker = self._tracker()
        if tracker is None:
            return _err(self._cfg_error or "could not load configuration")
        running = sim_running(tracker.cfg.sim_processes)
        car = track = best_lap = incidents = None
        if running:
            ctx = ContextCache(tracker.cfg.state_dir).context
            car, track, best_lap, incidents = ctx.car, ctx.track, ctx.best_lap, ctx.incidents
        result = tracker.take_snapshot("manual", message=(message or None),
                                       sim_running=running, car=car, track=track,
                                       best_lap=best_lap, incidents=incidents)
        if not result.committed:
            return _ok(created=False,
                       skippedIgnored=result.skipped_ignored,
                       message="Everything is already backed up -- nothing has changed.")
        return _ok(created=True, rev=result.commit, shortRev=result.commit[:8],
                   files=self._files_clean(result.files))

    def restore_file(self, rev: str, name: str) -> dict:
        tracker = self._tracker()
        if tracker is None:
            return _err(self._cfg_error or "could not load configuration")
        running = sim_running(tracker.cfg.sim_processes)
        try:
            commit = tracker.restore_file(name, rev, running)
        except SimRunningError:
            return _err("Can't restore while iRacing is running. Close the sim "
                        "(and the iRacing UI) first, then try again.", simBlocked=True)
        except Exception as exc:
            return _err(str(exc))
        return _ok(message=f"Restored {name}. A safety backup of the previous "
                            f"state was made first.", commit=commit)

    def restore_baseline(self, tag: str) -> dict:
        tracker = self._tracker()
        if tracker is None:
            return _err(self._cfg_error or "could not load configuration")
        running = sim_running(tracker.cfg.sim_processes)
        try:
            restored, extras = tracker.restore_baseline(tag, running)
        except SimRunningError:
            return _err("Can't restore while iRacing is running. Close the sim "
                        "(and the iRacing UI) first, then try again.", simBlocked=True)
        except Exception as exc:
            return _err(str(exc))
        return _ok(restored=restored, extras=extras,
                   message=f"Restored {len(restored)} file(s) from \"{tag}\".")

    def list_tags(self) -> dict:
        tracker = self._tracker()
        if tracker is None:
            return _err(self._cfg_error or "could not load configuration")
        try:
            tags = tracker.repo.list_tags()
        except Exception as exc:
            return _err(str(exc))
        return _ok(items=[{"name": n, "rev": c, "message": m} for n, c, m in tags])

    def create_tag(self, name: str, rev: str = "HEAD", message: str | None = None) -> dict:
        tracker = self._tracker()
        if tracker is None:
            return _err(self._cfg_error or "could not load configuration")
        slug = _tag_slug(name)
        if not slug:
            return _err("Please use letters or numbers for the name.")
        if slug in {t[0] for t in tracker.repo.list_tags()}:
            return _err(f"A saved setup named \"{slug}\" already exists.")
        try:
            tracker.repo.create_tag(slug, rev or "HEAD", (message or (name or "").strip()))
        except Exception as exc:
            return _err(str(exc))
        return _ok(message=f"Saved this version as \"{slug}\".")

    def delete_tag(self, name: str) -> dict:
        tracker = self._tracker()
        if tracker is None:
            return _err(self._cfg_error or "could not load configuration")
        try:
            tracker.repo.delete_tag(name)
        except Exception as exc:
            return _err(str(exc))
        return _ok(message=f"Removed saved setup \"{name}\".")

    # -- profiles (named whole-folder setups; built on tags + restore_baseline) --

    def list_profiles(self) -> dict:
        tracker = self._tracker()
        if tracker is None:
            return _err(self._cfg_error or "could not load configuration")
        repo = tracker.repo
        if not repo.initialized or not repo.head():
            return _ok(items=[])
        items = []
        for name, commit, message in repo.list_tags():
            if _is_known_good(name):
                continue  # known-good restore points are their own thing
            entry = {"name": name, "rev": commit, "message": message,
                     "date": None, "files": {}, "contextLabel": ""}
            try:
                s = repo.snapshot_at(commit)
                entry["date"] = s.author_date
                entry["files"] = self._files_clean(s.meta.files)
                entry["contextLabel"] = s.meta.context_label()
            except Exception:
                pass
            items.append(entry)
        return _ok(items=items)

    def save_current_as_profile(self, name: str) -> dict:
        tracker = self._tracker()
        if tracker is None:
            return _err(self._cfg_error or "could not load configuration")
        raw = (name or "").strip()
        slug = _tag_slug(raw)
        if not slug:
            return _err("Please use letters or numbers for the setup name.")
        if slug in {t[0] for t in tracker.repo.list_tags()}:
            return _err(f"A saved setup named \"{slug}\" already exists.")
        running = sim_running(tracker.cfg.sim_processes)
        car = track = None
        if running:
            ctx = ContextCache(tracker.cfg.state_dir).context
            car, track = ctx.car, ctx.track
        result = tracker.take_snapshot("manual", message=f'saved setup "{raw}"',
                                       sim_running=running, car=car, track=track)
        rev = result.commit or tracker.repo.head()
        if not rev:
            return _err("There's nothing to save yet — your iRacing folder looks empty.")
        try:
            tracker.repo.create_tag(slug, rev, f'saved setup "{raw}"')
        except Exception as exc:
            return _err(str(exc))
        return _ok(message=f'Saved your current setup as "{slug}".')

    def apply_profile(self, name: str) -> dict:
        """Apply a saved profile to the live folder (= restore that baseline)."""
        return self.restore_baseline(name)

    def delete_profile(self, name: str) -> dict:
        return self.delete_tag(name)

    # -- known-good restore points (a "verified in a real session" safety net) --

    def list_known_good(self) -> dict:
        tracker = self._tracker()
        if tracker is None:
            return _err(self._cfg_error or "could not load configuration")
        repo = tracker.repo
        if not repo.initialized or not repo.head():
            return _ok(items=[])
        items = []
        for name, commit, message in repo.list_tags():
            if not _is_known_good(name):
                continue
            entry = {"tag": name, "label": message or "Known-good", "rev": commit,
                     "date": None, "files": {}, "contextLabel": ""}
            try:
                s = repo.snapshot_at(commit)
                entry["date"] = s.author_date
                entry["files"] = self._files_clean(s.meta.files)
                entry["contextLabel"] = s.meta.context_label()
            except Exception:
                pass
            items.append(entry)
        items.sort(key=lambda e: e["tag"], reverse=True)  # newest first
        return _ok(items=items)

    def mark_known_good(self, label: str | None = None) -> dict:
        """Capture the current setup and mark it as a known-good restore point."""
        tracker = self._tracker()
        if tracker is None:
            return _err(self._cfg_error or "could not load configuration")
        running = sim_running(tracker.cfg.sim_processes)
        car = track = None
        if running:
            ctx = ContextCache(tracker.cfg.state_dir).context
            car, track = ctx.car, ctx.track
        result = tracker.take_snapshot("manual", message="marked known-good",
                                       sim_running=running, car=car, track=track)
        rev = result.commit or tracker.repo.head()
        if not rev:
            return _err("There's nothing to mark yet — your iRacing folder looks empty.")
        from datetime import datetime
        now = datetime.now()
        clean = (label or "").strip() or " @ ".join(p for p in (car, track) if p) \
            or f"Known-good {now.strftime('%b %d')}"
        tag = KNOWN_GOOD_PREFIX + now.strftime("%Y%m%d-%H%M%S")
        try:
            tracker.repo.create_tag(tag, rev, clean)
        except Exception as exc:
            return _err(str(exc))
        return _ok(message=f'Marked your current setup as known-good ("{clean}").')

    def revert_known_good(self, tag: str | None = None) -> dict:
        """Restore the live folder to a known-good point (the latest by default)."""
        if not tag:
            items = self.list_known_good().get("items") or []
            if not items:
                return _err("You haven't marked a known-good setup yet.")
            tag = items[0]["tag"]
        return self.restore_baseline(tag)

    def delete_known_good(self, tag: str) -> dict:
        tracker = self._tracker()
        if tracker is None:
            return _err(self._cfg_error or "could not load configuration")
        try:
            tracker.repo.delete_tag(tag)
        except Exception as exc:
            return _err(str(exc))
        return _ok(message="Removed that known-good mark.")

    # -- driving sessions (group history by sim session, FR-6 context) ---------

    def list_sessions(self) -> dict:
        """Group backups into driving sessions (a run of sim-involved snapshots
        sharing a car/track, ended by sim exit). Each carries the revs needed to
        diff before-vs-after via get_comparison."""
        tracker = self._tracker()
        if tracker is None:
            return _err(self._cfg_error or "could not load configuration")
        repo = tracker.repo
        if not repo.initialized or not repo.head():
            return _ok(items=[])

        def in_session(s) -> bool:
            m = s.meta
            return bool(m.sim_running or m.trigger == "sim_exit" or m.car or m.track)

        chrono = list(reversed(repo.log()))  # oldest first
        sessions: list[dict] = []
        i, n = 0, len(chrono)
        while i < n:
            if not in_session(chrono[i]):
                i += 1
                continue
            car, track = chrono[i].meta.car, chrono[i].meta.track
            baseline = chrono[i - 1].commit if i > 0 else None
            group = []
            j = i
            while (j < n and in_session(chrono[j])
                   and chrono[j].meta.car == car and chrono[j].meta.track == track):
                group.append(chrono[j])
                ended = chrono[j].meta.trigger == "sim_exit"
                j += 1
                if ended:
                    break
            files = sorted({f for g in group for f in g.meta.files if not is_sidecar(f)})
            laps = [g.meta.best_lap for g in group
                    if isinstance(g.meta.best_lap, (int, float)) and g.meta.best_lap > 0]
            incs = [g.meta.incidents for g in group
                    if isinstance(g.meta.incidents, (int, float))]
            best_lap = min(laps) if laps else None
            sessions.append({
                "car": car, "track": track,
                "start": group[0].author_date, "end": group[-1].author_date,
                "baselineRev": baseline, "endRev": group[-1].commit,
                "count": len(group), "files": files,
                "bestLap": best_lap, "bestLapStr": _fmt_lap(best_lap),
                "incidents": max(incs) if incs else None,
            })
            i = j
        # Personal best: fastest session lap per car+track gets a PB flag.
        fastest: dict = {}
        for s in sessions:
            if s["bestLap"] is None:
                continue
            key = (s["car"], s["track"])
            if key not in fastest or s["bestLap"] < fastest[key]:
                fastest[key] = s["bestLap"]
        for s in sessions:
            s["isPB"] = (s["bestLap"] is not None
                         and s["bestLap"] == fastest.get((s["car"], s["track"])))
        sessions.reverse()  # newest first
        return _ok(items=sessions)

    def export_backup(self, rev: str) -> dict:
        tracker = self._tracker()
        if tracker is None:
            return _err(self._cfg_error or "could not load configuration")
        default_name = f"iracing-config-{rev[:8]}.zip"
        dest: Path | None = None
        if self._window is not None:
            try:
                import webview
                picked = self._window.create_file_dialog(
                    webview.SAVE_DIALOG, save_filename=default_name,
                    file_types=("Zip archive (*.zip)",))
                if not picked:
                    return _ok(cancelled=True)
                dest = Path(picked if isinstance(picked, str) else picked[0])
            except Exception as exc:  # pragma: no cover - dialog edge cases
                log.warning("save dialog failed (%s); falling back to Desktop", exc)
        if dest is None:
            desktop = Path.home() / "Desktop"
            dest = (desktop if desktop.is_dir() else Path.home()) / default_name
        try:
            tracker.export(rev, dest)
        except Exception as exc:
            return _err(str(exc))
        return _ok(path=str(dest), message=f"Saved a copy to {dest}")

    # -- controls & devices ------------------------------------------------------

    @staticmethod
    def _profile_file(cfg, name: str, profile: str | None) -> Path:
        """On-disk path of controls.cfg/joyCalib.yaml for a specific control
        profile, or the active/legacy location when profile is None."""
        if profile:
            return cfg.iracing_dir / "profiles" / "controls" / profile / name
        return cfg.live_path(name)

    def get_controls(self, rev: str | None = None, profile: str | None = None) -> dict:
        cfg = self._config()
        if cfg is None:
            return _err(self._cfg_error or "could not load configuration")
        last_saved = None
        is_live = not rev
        active = active_control_profile(cfg.iracing_dir)
        profiles = cfg.control_profile_names()
        # Which profile's controls to show (live only; default = the active one).
        view = (profile if profile in profiles else active) if is_live else None
        meta = dict(profiles=profiles, profile=view, activeProfile=active)
        try:
            if rev:
                data = Tracker(cfg).repo.show_file(rev, "controls.cfg")
                source = rev[:8]
            else:
                path = self._profile_file(cfg, "controls.cfg", view)
                if not path.exists():
                    return _ok(available=False, **meta,
                               error="No controls.cfg found in your iRacing folder yet.")
                data = path.read_bytes()
                source = "live"
                from datetime import datetime
                last_saved = datetime.fromtimestamp(
                    path.stat().st_mtime).astimezone().isoformat(timespec="seconds")
        except Exception as exc:
            return _err(str(exc))
        try:
            doc = codec.decode_bytes(data)
        except GfccError as exc:
            return _ok(available=False, source=source, **meta,
                       error=f"This controls file couldn't be read in detail ({exc}). "
                             f"Your backups still keep it safely.")
        from irtracker.gfcc.analyze import find_binding_conflicts

        bindings = self._bindings(doc)
        conflicts = [{"kind": c.kind, "label": c.label, "actions": c.actions}
                     for c in find_binding_conflicts(doc)]
        return _ok(
            available=True, source=source, **meta,
            bindings=bindings,
            conflicts=conflicts,
            lastSaved=last_saved,
            simRunning=(is_live and sim_running(cfg.sim_processes)),
            boundCount=sum(1 for b in bindings if b["kind"] != "unbound"),
            ffbNote="Force-feedback strength and pedal calibration are stored in "
                    "this file, but live inside an encoded block that isn't "
                    "human-readable yet. The mappings and devices below are fully "
                    "decoded.",
        )

    @staticmethod
    def _device_name_from_note(note: str | None) -> str | None:
        if note and " - " in note:
            return note.split(" - ", 1)[1]
        return None

    def _bindings(self, doc: dict) -> list[dict]:
        from irtracker.gfcc.analyze import binding_value
        devices = doc.get("_devices", {})
        out = []
        for e in doc["controls"]["entries"]:
            kind = e.get("type", "unbound")
            value = e.get("value", 0)
            display = binding_value(e)
            device = "Keyboard" if kind == "key" else None

            # Resolve a device for wheel/pedal bindings via the product GUID slot.
            for i in range(3):
                g = e.get(f"slot{i}")
                if g and g in devices:
                    device = self._device_name_from_note(devices[g]) or "Game controller"
                    break
            if kind in ("axis", "button") and device is None:
                device = "Game controller"

            out.append({
                "action": e["name"],
                "kind": kind,
                "value": value,
                "display": display,
                "device": device,
            })
        return out

    def apply_bindings_gui(self, bindings: list, profile: str | None = None) -> dict:
        """In-app controls editor: patch keyboard bindings into the live
        controls.cfg and snapshot the change. Each binding is
        {"action": str, "key": str, "modifiers": [str, ...]}.

        Same safety as `gfcc encode --install`: refuses while the sim runs,
        backs up the live file first, patches via apply_bindings (keyboard
        only; axis/button binds are refused), and snapshots the result.
        Returns the change lines and the snapshot commit.
        """
        cfg = self._config()
        if cfg is None:
            return _err(self._cfg_error or "could not load configuration")
        if sim_running(cfg.sim_processes):
            return _err("Can't change controls while iRacing is running. "
                        "Close the sim and the iRacing UI, then try again.")
        from irtracker.gfcc.patch import apply_bindings, BindingsError

        view = profile if profile in cfg.control_profile_names() \
            else active_control_profile(cfg.iracing_dir)
        path = self._profile_file(cfg, "controls.cfg", view)
        if not path.exists():
            return _err("No controls.cfg found in your iRacing folder.")
        try:
            base = path.read_bytes()
            doc = codec.decode_bytes(base)
        except OSError as exc:
            return _err(str(exc))
        except GfccError as exc:
            return _err(f"Couldn't read your controls file: {exc}")

        try:
            changes = apply_bindings(doc, bindings)
            out_bytes = codec.build(doc)
            codec.decode_bytes(out_bytes)  # self-check on the result
        except BindingsError as exc:
            return _err(str(exc))
        except GfccError as exc:
            return _err(f"Couldn't rebuild the controls file: {exc}")

        backup = backup_live_file(cfg, "controls.cfg")
        try:
            path.write_bytes(out_bytes)
        except OSError as exc:
            return _err(f"Couldn't write the new controls file: {exc}")

        # Snapshot the change so it enters history immediately.
        tracker = Tracker(cfg)
        running = sim_running(cfg.sim_processes)
        context = ContextCache(cfg.state_dir).context
        result = tracker.take_snapshot(
            "manual", message="Edited keyboard bindings in the app",
            sim_running=running,
            car=context.car if running else None,
            track=context.track if running else None)

        return _ok(changes=changes, backup=str(backup) if backup else None,
                   commit=result.commit[:8] if result.committed else None)

    # -- share / import a controls profile (export to / import from a file) -----

    def _controls_devices(self, doc, joycalib: str | None = None) -> list[dict]:
        from irtracker.gfcc.devices import build_report
        return [{"name": d.name or "Game controller", "guid": d.instance_guid}
                for d in build_report(doc, joycalib).referenced]

    def export_controls_profile(self, profile: str | None = None) -> dict:
        """Save a control profile (controls.cfg + joyCalib.yaml) to a portable
        .json file the user can keep or send to someone."""
        cfg = self._config()
        if cfg is None:
            return _err(self._cfg_error or "could not load configuration")
        from irtracker import build as build_mod, ctrlprofile
        view = profile if profile in cfg.control_profile_names() \
            else active_control_profile(cfg.iracing_dir)
        cpath = self._profile_file(cfg, "controls.cfg", view)
        if not cpath.exists():
            return _err("No controls.cfg found to export.")
        files: dict[str, bytes] = {}
        try:
            files["controls.cfg"] = cpath.read_bytes()
            jc = self._profile_file(cfg, "joyCalib.yaml", view)
            if jc.exists():
                files["joyCalib.yaml"] = jc.read_bytes()
        except OSError as exc:
            return _err(str(exc))
        devices = []
        try:
            devices = [d["name"] for d in self._controls_devices(codec.decode_bytes(files["controls.cfg"]))]
        except GfccError:
            pass
        text = ctrlprofile.build_bundle(view or "controls", files,
                                        build=build_mod.current_build(), devices=devices)
        dest = self._save_dialog(f"{view or 'controls'}-controls.json",
                                 "Controls profile (*.json)")
        if dest is None:
            return _ok(cancelled=True)
        try:
            Path(dest).write_text(text, encoding="utf-8")
        except OSError as exc:
            return _err(str(exc))
        return _ok(path=str(dest), message=f"Saved controls profile to {dest}")

    def preview_controls_import(self, text: str) -> dict:
        cfg = self._config()
        if cfg is None:
            return _err(self._cfg_error or "could not load configuration")
        from irtracker import ctrlprofile
        try:
            bundle = ctrlprofile.parse_bundle(text)
        except ValueError as exc:
            return _err(str(exc))
        try:
            doc = codec.decode_bytes(bundle["files"]["controls.cfg"])
        except GfccError as exc:
            return _err(f"The controls in this file couldn't be read ({exc}).")
        bound = [b for b in self._bindings(doc) if b["kind"] != "unbound"]
        devices = self._controls_devices(doc)
        # Different machine? Compare the bundle's device GUIDs to your own.
        my_guids: set = set()
        mine = self._profile_file(cfg, "controls.cfg", active_control_profile(cfg.iracing_dir))
        if mine.exists():
            try:
                my_guids = {d["guid"] for d in self._controls_devices(codec.decode_bytes(mine.read_bytes()))}
            except (OSError, GfccError):
                pass
        bundle_guids = {d["guid"] for d in devices}
        mismatch = bool(bundle_guids) and bool(my_guids) and bundle_guids.isdisjoint(my_guids)
        return _ok(
            name=bundle.get("name"), exportedAt=bundle.get("exportedAt"),
            build=bundle.get("build"), bindingCount=len(bound), devices=devices,
            hasJoyCalib="joyCalib.yaml" in bundle["files"], deviceMismatch=mismatch,
            sample=[{"action": _pretty_action(b["action"]), "display": b["display"]}
                    for b in bound[:14]])

    def import_controls_profile(self, text: str, profile: str | None = None) -> dict:
        """Install a controls profile from a bundle into the active (or given)
        control profile. Safety-backed-up and snapshotted, so it's reversible."""
        cfg = self._config()
        if cfg is None:
            return _err(self._cfg_error or "could not load configuration")
        if sim_running(cfg.sim_processes):
            return _err("Can't change controls while iRacing is running. Close the "
                        "sim and the iRacing UI, then try again.", simBlocked=True)
        from irtracker import ctrlprofile
        try:
            bundle = ctrlprofile.parse_bundle(text)
        except ValueError as exc:
            return _err(str(exc))
        try:
            codec.decode_bytes(bundle["files"]["controls.cfg"])  # validate before writing
        except GfccError as exc:
            return _err(f"The controls in this file couldn't be read ({exc}).")
        view = profile if profile in cfg.control_profile_names() \
            else active_control_profile(cfg.iracing_dir)
        backup_live_file(cfg, "controls.cfg")
        try:
            cpath = self._profile_file(cfg, "controls.cfg", view)
            cpath.parent.mkdir(parents=True, exist_ok=True)
            cpath.write_bytes(bundle["files"]["controls.cfg"])
            if "joyCalib.yaml" in bundle["files"]:
                backup_live_file(cfg, "joyCalib.yaml")
                jcpath = self._profile_file(cfg, "joyCalib.yaml", view)
                jcpath.parent.mkdir(parents=True, exist_ok=True)
                jcpath.write_bytes(bundle["files"]["joyCalib.yaml"])
        except OSError as exc:
            return _err(f"Couldn't write the controls file: {exc}")
        tracker = Tracker(cfg)
        running = sim_running(cfg.sim_processes)
        context = ContextCache(cfg.state_dir).context
        result = tracker.take_snapshot(
            "manual", message=f"Imported controls profile \"{bundle.get('name')}\"",
            sim_running=running, car=context.car if running else None,
            track=context.track if running else None)
        return _ok(name=bundle.get("name"),
                   commit=result.commit[:8] if result.committed else None,
                   message=f"Imported controls profile \"{bundle.get('name')}\". "
                           f"Restart iRacing to use it.")

    def blame_control(self, action: str, profile: str | None = None) -> dict:
        """When did a control's binding last change? Walks the controls.cfg
        history (rename-aware) for one action and returns its change timeline,
        newest first, each with value + when/why/where it changed."""
        tracker = self._tracker()
        if tracker is None:
            return _err(self._cfg_error or "could not load configuration")
        cfg, repo = tracker.cfg, tracker.repo
        if not repo.initialized or not repo.head():
            return _ok(action=action, events=[], current=None)
        from irtracker.gfcc.analyze import binding_value
        view = profile if profile in cfg.control_profile_names() \
            else active_control_profile(cfg.iracing_dir)
        key = f"profiles/controls/{view}/controls.cfg" if view else "controls.cfg"

        def value_at(commit: str):
            # The file may sit at the profile path now or at the bare top-level
            # path in pre-migration history; try both.
            data = None
            for name in (key, "controls.cfg"):
                try:
                    data = repo.show_file(commit, name)
                    break
                except GitError:
                    continue
            if data is None:
                return None
            try:
                doc = codec.decode_bytes(data)
            except GfccError:
                return None
            entry = next((e for e in doc["controls"]["entries"]
                          if e["name"] == action), None)
            return binding_value(entry) if entry else "Not assigned"

        snaps = repo.log(path=key, follow=True)  # newest first
        seq = [(s, value_at(s.commit)) for s in reversed(snaps)]  # oldest first
        seq = [(s, v) for s, v in seq if v is not None]
        events, prev = [], object()
        for s, v in seq:
            if v != prev:
                events.append({
                    "value": v, "date": s.author_date, "trigger": s.meta.trigger,
                    "car": s.meta.car, "track": s.meta.track,
                    "message": s.meta.message, "rev": s.commit,
                    "contextLabel": s.meta.context_label(),
                })
            prev = v
        events.reverse()  # newest first
        return _ok(action=action, events=events,
                   current=(seq[-1][1] if seq else None))

    @staticmethod
    def _text_at(repo, rev: str, name: str) -> str | None:
        try:
            return repo.show_file(rev, name).decode("utf-8", "replace")
        except GitError:
            return None

    def blame_setting(self, file: str, section: str, key: str) -> dict:
        """When did an INI setting last change? Walk a tracked file's history for
        one Section/key and return its change timeline (newest first)."""
        tracker = self._tracker()
        if tracker is None:
            return _err(self._cfg_error or "could not load configuration")
        repo = tracker.repo
        base = dict(file=file, section=section, key=key)
        if not repo.initialized or not repo.head():
            return _ok(**base, events=[], current=None)
        from irtracker.semdiff import parse_ini

        def value_at(commit: str):
            text = self._text_at(repo, commit, file)
            if text is None:
                return None
            return parse_ini(text).get(section, {}).get(key)

        snaps = repo.log(path=file)  # newest first
        seq = [(s, value_at(s.commit)) for s in reversed(snaps)]  # oldest first
        events, prev = [], object()
        for s, v in seq:
            if v != prev:
                events.append({
                    "value": v, "date": s.author_date, "trigger": s.meta.trigger,
                    "car": s.meta.car, "track": s.meta.track,
                    "message": s.meta.message, "rev": s.commit,
                    "contextLabel": s.meta.context_label(),
                })
            prev = v
        events.reverse()
        return _ok(**base, events=events, current=(seq[-1][1] if seq else None))

    def _recent_setting_changes(self, tracker, ini_files: list[str],
                                max_snaps: int = 40, limit: int = 40) -> list[dict]:
        """Settings whose VALUE changed across recent snapshots (newest first,
        de-duplicated to the most recent change; ignored keys excluded)."""
        from irtracker.semdiff import CHANGED, diff_ini, matches_ignore
        repo, cfg = tracker.repo, tracker.cfg
        ignore_by_file = {n: (cfg.policy_for(n).ignore_keys if cfg.policy_for(n) else [])
                          for n in ini_files}
        ini_set = {n.lower() for n in ini_files}
        out: list[dict] = []
        seen: set[tuple] = set()
        for s in repo.log()[:max_snaps]:
            for name in (n for n in s.meta.files if n.lower() in ini_set):
                new_text = self._text_at(repo, s.commit, name)
                if new_text is None:
                    continue
                old_text = self._text_at(repo, f"{s.commit}~1", name) or ""
                for ch in diff_ini(old_text, new_text):
                    if ch.kind != CHANGED:
                        continue
                    if matches_ignore(ch.section, ch.key, ignore_by_file.get(name, [])):
                        continue
                    ident = (name, ch.section, ch.key)
                    if ident in seen:
                        continue
                    seen.add(ident)
                    out.append({"file": name, "section": ch.section, "key": ch.key,
                                "value": ch.new, "date": s.author_date,
                                "trigger": s.meta.trigger,
                                "contextLabel": s.meta.context_label()})
                    if len(out) >= limit:
                        return out
        return out

    def list_settings(self) -> dict:
        """All current INI settings (for client-side search) + recently-changed
        ones (the default view), each clickable for its change history."""
        tracker = self._tracker()
        if tracker is None:
            return _err(self._cfg_error or "could not load configuration")
        cfg = tracker.cfg
        from irtracker.semdiff import parse_ini
        ini_files = [n for n in cfg.tracked_files_present() if n.lower().endswith(".ini")]
        all_keys: list[dict] = []
        for name in sorted(ini_files):
            try:
                text = cfg.live_path(name).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for section, keys in parse_ini(text).items():
                for key, value in keys.items():
                    all_keys.append({"file": name, "section": section,
                                     "key": key, "value": value})
        recent = []
        if tracker.repo.initialized and tracker.repo.head():
            recent = self._recent_setting_changes(tracker, ini_files)
        return _ok(all=all_keys, recent=recent)

    def search_history(self, query: str, file: str | None = None,
                       limit: int = 100) -> dict:
        """Configuration history search: find every snapshot where a key,
        section, action, or value matched the query (ROADMAP "Configuration
        history search").

        Walks the git history of tracked INI/YAML files (and controls.cfg
        actions) and returns matching changes, newest first. Each result is a
        {file, section, key, old, new, date, trigger, car, track, message,
        rev, contextLabel} dict — the same shape blame uses, but across all
        keys instead of one. Optional ``file`` narrows to one tracked file.

        For INI/YAML we diff consecutive versions and match on section, key,
        or value. For controls.cfg we decode each version and match on action
        name or binding value (the decoded view, never raw bytes).
        """
        tracker = self._tracker()
        if tracker is None:
            return _err(self._cfg_error or "could not load configuration")
        repo = tracker.repo
        if not repo.initialized or not repo.head():
            return _ok(query=query, results=[])
        q = (query or "").strip().lower()
        if not q:
            return _ok(query=query, results=[])
        cfg = tracker.cfg
        from irtracker.semdiff import ADDED, REMOVED, CHANGED, diff_ini, diff_yaml, describe_binding

        # Decide which files to search.
        if file:
            candidates = [file]
        else:
            candidates = sorted(
                n for n in (repo.tracked_in_worktree() or cfg.tracked_files_present())
                if not is_sidecar(n))
        results: list[dict] = []
        # Per-file walking (oldest -> newest so diffs are forward).
        for name in candidates:
            low = name.lower()
            snaps = repo.log(path=name, follow=True)
            if not snaps:
                continue
            is_ini = low.endswith(".ini")
            is_yaml = low.endswith((".yaml", ".yml"))
            is_controls = low == "controls.cfg" or "controls.cfg" in low
            if not (is_ini or is_yaml or is_controls):
                continue

            # Build the ordered list of (snapshot, content) and diff adjacent
            # pairs. We match on the CHANGE itself (section/key + old/new for
            # INI/YAML, action + old/new binding for controls).
            seq = []
            for s in reversed(snaps):  # oldest first
                if is_controls:
                    data = None
                    for key_name in (name, "controls.cfg"):
                        try:
                            data = repo.show_file(s.commit, key_name)
                            break
                        except GitError:
                            continue
                    if data is None:
                        continue
                    try:
                        doc = codec.decode_bytes(data)
                    except GfccError:
                        continue
                    seq.append((s, doc))
                else:
                    text = self._text_at(repo, s.commit, name)
                    if text is None:
                        continue
                    seq.append((s, text))

            for i in range(1, len(seq)):
                s_new = seq[i][0]
                if is_controls:
                    old_doc, new_doc = seq[i - 1][1], seq[i][1]
                    old_e = {e["name"]: e for e in old_doc["controls"]["entries"]}
                    new_e = {e["name"]: e for e in new_doc["controls"]["entries"]}
                    for action in list(old_e) + [a for a in new_e if a not in old_e]:
                        o, n = old_e.get(action), new_e.get(action)
                        if o is None and n is not None:
                            kind, old_v, new_v = ADDED, None, describe_binding(n)
                        elif o is not None and n is None:
                            kind, old_v, new_v = REMOVED, describe_binding(o), None
                        elif o != n:
                            kind = CHANGED
                            old_v = describe_binding(o)
                            new_v = describe_binding(n)
                            # device GUID drift only — not a value match
                            if old_v == new_v:
                                continue
                        else:
                            continue
                        # match on action name or old/new binding value
                        hay = f"{action} {old_v or ''} {new_v or ''}".lower()
                        if q not in hay:
                            continue
                        results.append({
                            "file": name, "section": "", "key": action,
                            "kind": kind, "old": old_v, "new": new_v,
                            "date": s_new.author_date,
                            "trigger": s_new.meta.trigger,
                            "car": s_new.meta.car, "track": s_new.meta.track,
                            "message": s_new.meta.message, "rev": s_new.commit,
                            "contextLabel": s_new.meta.context_label(),
                        })
                else:
                    old_text, new_text = seq[i - 1][1], seq[i][1]
                    if is_ini:
                        changes = diff_ini(old_text, new_text)
                    else:
                        changes = diff_yaml(old_text, new_text)
                    for ch in changes:
                        hay = f"{ch.section} {ch.key} {ch.old or ''} {ch.new or ''}".lower()
                        if q not in hay:
                            continue
                        results.append({
                            "file": name, "section": ch.section, "key": ch.key,
                            "kind": ch.kind, "old": ch.old, "new": ch.new,
                            "date": s_new.author_date,
                            "trigger": s_new.meta.trigger,
                            "car": s_new.meta.car, "track": s_new.meta.track,
                            "message": s_new.meta.message, "rev": s_new.commit,
                            "contextLabel": s_new.meta.context_label(),
                        })
            if len(results) >= limit:
                break

        # newest first; cap at limit
        results.sort(key=lambda r: r["date"], reverse=True)
        results = results[:limit]
        return _ok(query=query, file=file, results=results)

    def identify_input(self, query: str, profile: str | None = None) -> dict:
        """Reverse lookup: what action(s) a key/button/axis is bound to."""
        cfg = self._config()
        if cfg is None:
            return _err(self._cfg_error or "could not load configuration")
        view = profile if profile in cfg.control_profile_names() \
            else active_control_profile(cfg.iracing_dir)
        controls = self._profile_file(cfg, "controls.cfg", view)
        if not controls.exists():
            return _err("No controls.cfg found in your iRacing folder.")
        try:
            doc = codec.decode_bytes(controls.read_bytes())
            from irtracker.gfcc.analyze import find_input
            label, kind, matches = find_input(doc, query)
        except (OSError, GfccError) as exc:
            return _err(str(exc))
        return _ok(query=query, label=label, kind=kind, matches=matches, free=not matches)

    def get_devices(self, profile: str | None = None) -> dict:
        cfg = self._config()
        if cfg is None:
            return _err(self._cfg_error or "could not load configuration")
        from irtracker.gfcc.devices import build_report

        view = profile if profile in cfg.control_profile_names() \
            else active_control_profile(cfg.iracing_dir)
        base_doc = None
        controls = self._profile_file(cfg, "controls.cfg", view)
        if controls.exists():
            try:
                base_doc = codec.decode_bytes(controls.read_bytes())
            except (OSError, GfccError):
                base_doc = None
        joycalib = None
        jc = self._profile_file(cfg, "joyCalib.yaml", view)
        if jc.exists():
            joycalib = jc.read_text(encoding="utf-8", errors="replace")

        report = build_report(base_doc, joycalib)
        connected_guids = {d.instance_guid for d in report.connected}
        connected_products = {d.product_guid: d.instance_guid for d in report.connected}

        def presence(d) -> str:
            if d.instance_guid in connected_guids:
                return "connected"
            if d.product_guid and d.product_guid in connected_products:
                return "moved-port"
            return "not-connected"

        def referenced_dict(d) -> dict:
            p = presence(d)
            return {"name": d.name, "instanceGuid": d.instance_guid,
                    "productGuid": d.product_guid, "note": d.note, "presence": p,
                    # When the same hardware is connected under a new instance
                    # GUID, this is the GUID to re-map onto (the one-click fix).
                    "suggestedNewGuid": connected_products.get(d.product_guid) if p == "moved-port" else None}

        return _ok(
            connected=[{"name": d.name, "instanceGuid": d.instance_guid,
                        "productGuid": d.product_guid, "note": d.note}
                       for d in report.connected],
            enumError=report.enum_error,
            referenced=[referenced_dict(d) for d in report.referenced],
            calibrated=[{"name": d.name, "instanceGuid": d.instance_guid,
                         "productGuid": d.product_guid, "note": d.note,
                         "presence": presence(d)}
                        for d in report.calibrated],
        )

    def remap_device(self, old_instance: str, new_instance: str) -> dict:
        """Repoint every binding (and pedal/wheel calibration) from an old device
        instance GUID to a newly-connected one -- the one-click fix for a wheel
        that lost its binds after a USB-port change or PC swap."""
        tracker = self._tracker()
        if tracker is None:
            return _err(self._cfg_error or "could not load configuration")
        cfg = tracker.cfg
        if sim_running(cfg.sim_processes):
            return _err("Can't change your controls while iRacing is running. Close "
                        "the sim (and the iRacing UI) first, then try again.", simBlocked=True)
        controls = cfg.live_path("controls.cfg")
        if not controls.exists():
            return _err("No controls.cfg found in your iRacing folder.")
        try:
            doc = codec.decode_bytes(controls.read_bytes())
            changed = remap_device(doc, old_instance, new_instance)
            if not changed:
                return _ok(changed=[], message="None of your bindings used that device "
                           "-- nothing needed changing.")
            out = codec.build(doc)
            codec.decode_bytes(out)  # self-check before writing
        except (OSError, GfccError, ValueError) as exc:
            return _err(str(exc))
        try:
            backup_live_file(cfg, "controls.cfg")
            controls.write_bytes(out)
            jc_count = 0
            jc = cfg.live_path("joyCalib.yaml")
            if jc.exists():
                new_text, jc_count = remap_joycalib(
                    jc.read_text(encoding="utf-8", errors="replace"), old_instance, new_instance)
                if jc_count:
                    backup_live_file(cfg, "joyCalib.yaml")
                    jc.write_text(new_text, encoding="utf-8")
            tracker.take_snapshot("manual", names={"controls.cfg", "joyCalib.yaml"},
                                  message="re-mapped device to the connected controller")
        except Exception as exc:  # pragma: no cover - filesystem edge cases
            return _err(str(exc))
        msg = f"Re-mapped {len(changed)} binding(s) to the connected device."
        if jc_count:
            msg += " Pedal/wheel calibration was moved across too."
        msg += " A safety backup of the previous files was made first."
        return _ok(changed=changed, joycalibUpdated=jc_count, message=msg)

    # -- auto-backup (watcher) ---------------------------------------------------

    def set_autostart(self, on: bool) -> dict:
        from irtracker import tasksched
        try:
            if on:
                desc = tasksched.install()
                return _ok(message=f"Auto-backup will now start when you log in ({desc}).")
            removed = tasksched.uninstall()
            return _ok(message="Auto-backup will no longer start automatically."
                       if removed else "Auto-backup wasn't set to start automatically.")
        except Exception as exc:
            return _err(str(exc))

    def start_watcher(self) -> dict:
        cfg = self._config()
        if cfg is None:
            return _err(self._cfg_error or "could not load configuration")
        from irtracker import watcher as watcher_mod
        if watcher_mod.watcher_alive(cfg):
            return _ok(message="Auto-backup is already running.")
        if getattr(sys, "frozen", False):
            # the packaged .exe routes CLI args to the CLI (see launcher.py)
            args = [sys.executable, "watcher", "run", "--quiet"]
        else:
            pythonw = sys.executable.replace("python.exe", "pythonw.exe")
            if not os.path.exists(pythonw):
                pythonw = sys.executable
            args = [pythonw, "-m", "irtracker", "watcher", "run", "--quiet"]
        if self._config_arg:
            args += ["--config", self._config_arg]
        # Detached + own process group so it keeps running after the GUI closes;
        # no console window. (Don't combine DETACHED_PROCESS with CREATE_NO_WINDOW.)
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        try:
            subprocess.Popen(args, creationflags=flags, close_fds=True)
        except Exception as exc:
            return _err(str(exc))
        return _ok(message="Auto-backup is now watching your iRacing folder.")

    def stop_watcher(self) -> dict:
        cfg = self._config()
        if cfg is None:
            return _err(self._cfg_error or "could not load configuration")
        from irtracker import watcher as watcher_mod
        if not watcher_mod.watcher_alive(cfg):
            return _ok(message="Auto-backup isn't running.")
        watcher_mod.request_stop(cfg)
        return _ok(message="Auto-backup is stopping.")

    def pause_watcher(self, paused: bool) -> dict:
        cfg = self._config()
        if cfg is None:
            return _err(self._cfg_error or "could not load configuration")
        from irtracker import watcher as watcher_mod
        if paused:
            watcher_mod.request_pause(cfg)
            return _ok(message="Auto-backup paused.")
        watcher_mod.request_resume(cfg)
        return _ok(message="Auto-backup resumed.")

    # -- misc --------------------------------------------------------------------

    def run_health_check(self) -> dict:
        cfg = self._config()
        if cfg is None:
            return _err(self._cfg_error or "could not load configuration")
        from irtracker.doctor import run_checks, summarize

        checks = run_checks(cfg)
        fails, warns = summarize(checks)
        return _ok(fails=fails, warns=warns,
                   checks=[{"name": c.name, "status": c.status, "detail": c.detail}
                           for c in checks])

    def run_config_lint(self) -> dict:
        """Sanity-check the live config: risky INI values, binding conflicts,
        and disconnected devices. Advisory, not authoritative."""
        cfg = self._config()
        if cfg is None:
            return _err(self._cfg_error or "could not load configuration")
        from irtracker import lint
        from irtracker.semdiff import parse_ini

        ram_mb = None
        try:
            import psutil
            ram_mb = int(psutil.virtual_memory().total // (1024 * 1024))
        except Exception:
            pass

        parsed = {}
        for name in cfg.tracked_files_present():
            if name.lower().endswith(".ini"):
                try:
                    parsed[name] = parse_ini(
                        cfg.live_path(name).read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    pass
        findings = [{"severity": f.severity, "title": f.title,
                     "detail": f.detail, "where": f.where}
                    for f in lint.lint_ini(parsed, ram_mb)]

        controls = cfg.live_path("controls.cfg")
        doc = None
        if controls.exists():
            try:
                doc = codec.decode_bytes(controls.read_bytes())
            except (OSError, GfccError):
                doc = None
        if doc is not None:
            from irtracker.gfcc.analyze import find_binding_conflicts
            conflicts = find_binding_conflicts(doc)
            if conflicts:
                shown = "; ".join(f"{c.label} ({len(c.actions)} actions)" for c in conflicts[:3])
                findings.append({
                    "severity": "warn",
                    "title": f"{len(conflicts)} binding conflict{'s' if len(conflicts) > 1 else ''}",
                    "detail": f"An input is assigned to multiple actions: {shown}"
                              f"{' …' if len(conflicts) > 3 else ''}. "
                              f"Open Controls & Devices to sort it out.",
                    "where": "Controls"})
            try:
                from irtracker.gfcc.devices import build_report
                jc = cfg.live_path("joyCalib.yaml")
                report = build_report(doc, jc.read_text(encoding="utf-8", errors="replace")
                                      if jc.exists() else None)
                connected = {d.instance_guid for d in report.connected}
                products = {d.product_guid for d in report.connected if d.product_guid}
                moved = [d for d in report.referenced
                         if d.instance_guid not in connected and d.product_guid in products]
                missing = [d for d in report.referenced
                           if d.instance_guid not in connected and d.product_guid not in products]
                if moved:
                    findings.append({"severity": "info",
                        "title": f"{len(moved)} device on a different USB port",
                        "detail": "A device used by your controls is connected under a new ID "
                                  "(often a different USB port). Controls & Devices offers a "
                                  "one-click re-map so your bindings keep working.",
                        "where": "Devices"})
                if missing:
                    findings.append({"severity": "warn",
                        "title": f"{len(missing)} device not connected",
                        "detail": "A device your controls rely on isn't plugged in right now. "
                                  "Its bindings won't work until it's reconnected.",
                        "where": "Devices"})
            except Exception:
                pass
        return _ok(findings=findings, ramMb=ram_mb)

    # -- snapshot notes (a tuning journal; sidecar keyed by commit) -------------

    def _notes_path(self, cfg) -> Path:
        return cfg.state_dir / "notes.json"

    def _load_notes(self, cfg) -> dict:
        try:
            return json.loads(self._notes_path(cfg).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def set_note(self, rev: str, text: str) -> dict:
        tracker = self._tracker()
        if tracker is None:
            return _err(self._cfg_error or "could not load configuration")
        try:
            sha = tracker.repo.resolve(rev)
        except Exception as exc:
            return _err(str(exc))
        cfg = tracker.cfg
        notes = self._load_notes(cfg)
        clean = (text or "").strip()
        if clean:
            notes[sha] = clean
        else:
            notes.pop(sha, None)
        try:
            cfg.state_dir.mkdir(parents=True, exist_ok=True)
            self._notes_path(cfg).write_text(
                json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            return _err(str(exc))
        return _ok(note=clean)

    # -- UI preferences read at launch (theme lives in localStorage; the tray
    #    setting must be readable by the Python launch code) --------------------

    def _ui_prefs(self, cfg) -> dict:
        try:
            return json.loads((cfg.state_dir / "ui.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def tray_enabled(self) -> bool:
        cfg = self._config()
        return True if cfg is None else bool(self._ui_prefs(cfg).get("tray", True))

    def set_tray_enabled(self, on) -> dict:
        cfg = self._config()
        if cfg is None:
            return _err(self._cfg_error or "could not load configuration")
        prefs = self._ui_prefs(cfg)
        prefs["tray"] = bool(on)
        try:
            cfg.state_dir.mkdir(parents=True, exist_ok=True)
            (cfg.state_dir / "ui.json").write_text(json.dumps(prefs, indent=2), encoding="utf-8")
        except OSError as exc:
            return _err(str(exc))
        return _ok(tray=bool(on))

    def ack_build(self, build: str) -> dict:
        """Dismiss the 'iRacing updated' notice for a given build."""
        cfg = self._config()
        if cfg is None:
            return _err(self._cfg_error or "could not load configuration")
        prefs = self._ui_prefs(cfg)
        prefs["ackBuild"] = build
        try:
            cfg.state_dir.mkdir(parents=True, exist_ok=True)
            (cfg.state_dir / "ui.json").write_text(json.dumps(prefs, indent=2), encoding="utf-8")
        except OSError as exc:
            return _err(str(exc))
        return _ok()

    def check_for_update(self) -> dict:
        from irtracker import updater
        return updater.check_for_update()

    def apply_update(self, exe_url: str, sha_url: str | None = None) -> dict:
        from irtracker import updater
        result = updater.apply_update(exe_url, sha_url)
        if result.get("ok"):
            # Let this JS call return, then exit so the swap helper can replace
            # the running .exe and relaunch it.
            threading.Timer(1.2, lambda: os._exit(0)).start()
            result["message"] = "Update downloaded — the app will close and reopen in a moment…"
        return result

    def pick_folder(self) -> dict:
        """Open a native folder picker (pywebview only)."""
        if self._window is None:
            return _err("The folder picker isn't available here — type the path instead.")
        try:
            import webview
            picked = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        except Exception as exc:  # pragma: no cover
            return _err(str(exc))
        if not picked:
            return _ok(cancelled=True)
        return _ok(path=picked if isinstance(picked, str) else picked[0])

    def update_settings(self, iracing_dir: str | None = None,
                        data_dir: str | None = None, move_existing: bool = True) -> dict:
        """Change the iRacing folder and/or where backups are stored, persisting
        to config.toml. Optionally moves existing backups to the new location."""
        cfg = self._config()
        if cfg is None:
            return _err(self._cfg_error or "could not load configuration")
        new_ira = Path(iracing_dir).expanduser() if iracing_dir else cfg.iracing_dir
        new_data = Path(data_dir).expanduser() if data_dir else cfg.data_dir
        if not new_ira.is_dir():
            return _err(f"That iRacing folder doesn't exist:\n{new_ira}")

        moved = False
        if str(new_data.resolve()) != str(cfg.data_dir.resolve()):
            if move_existing and cfg.data_dir.exists():
                if (new_data / "repo").exists():
                    return _err("The chosen folder already contains backups. Pick an "
                                "empty folder, or turn off \"move my existing backups\".")
                try:
                    if new_data.exists():
                        for child in cfg.data_dir.iterdir():
                            shutil.move(str(child), str(new_data / child.name))
                    else:
                        new_data.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(cfg.data_dir), str(new_data))
                    moved = True
                except Exception as exc:
                    return _err(f"Couldn't move your backups: {exc}")

        path = Path(self._config_arg) if self._config_arg else config_path()
        try:
            existing = path.read_text(encoding="utf-8-sig") if path.exists() else ""
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_update_config_paths(existing, str(new_ira), str(new_data)),
                            encoding="utf-8")
        except OSError as exc:
            return _err(f"Couldn't save the settings file: {exc}")

        self._cfg = None  # force reload with the new paths
        self._cfg_error = None
        msg = "Settings saved."
        if moved:
            msg += " Your existing backups were moved to the new folder."
        return _ok(message=msg, moved=moved,
                   iracingDir=str(new_ira), dataDir=str(new_data))

    def set_tracked(self, items: list | None = None) -> dict:
        """Replace which files are tracked (and their policy) from the GUI,
        persisting to config.toml. Existing per-pattern ignore_keys are kept."""
        cfg = self._config()
        if cfg is None:
            return _err(self._cfg_error or "could not load configuration")
        valid = {"track", "track-collapsed", "ignore"}
        existing = {tp.pattern: tp for tp in cfg.tracked}
        new_list, seen = [], set()
        for it in items or []:
            pattern = (it.get("pattern") or "").strip()
            policy = (it.get("policy") or "track").strip()
            if not pattern or pattern.lower() in seen:
                continue
            if policy not in valid:
                return _err(f"Unknown tracking option {policy!r}.")
            seen.add(pattern.lower())
            keys = existing[pattern].ignore_keys if pattern in existing else []
            new_list.append(TrackedPattern(pattern=pattern, policy=policy, ignore_keys=keys))
        if not new_list:
            return _err("Keep at least one file in the list.")

        path = Path(self._config_arg) if self._config_arg else config_path()
        try:
            existing_text = path.read_text(encoding="utf-8-sig") if path.exists() else "[paths]\n"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_set_tracked_in_text(existing_text, new_list), encoding="utf-8")
        except OSError as exc:
            return _err(f"Couldn't save the settings file: {exc}")

        self._cfg = None  # force reload with the new tracked set
        self._cfg_error = None
        return _ok(message="Saved which files are backed up.")

    # -- config recipes (shareable subset of settings) -------------------------

    def recipe_sources(self) -> dict:
        """The settings files (and their sections) a recipe can be built from."""
        cfg = self._config()
        if cfg is None:
            return _err(self._cfg_error or "could not load configuration")
        from irtracker.semdiff import parse_ini
        out = []
        for name in cfg.tracked_files_present():
            if not name.lower().endswith(".ini"):
                continue
            try:
                sections = list(parse_ini(
                    cfg.live_path(name).read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue
            if sections:
                out.append({"file": name, "label": name, "sections": sections})
        return _ok(items=out)

    def export_recipe(self, name: str, file: str, sections: list | None = None) -> dict:
        cfg = self._config()
        if cfg is None:
            return _err(self._cfg_error or "could not load configuration")
        from irtracker import recipes
        from irtracker.semdiff import parse_ini
        if not file or not file.lower().endswith(".ini"):
            return _err("Pick a settings (.ini) file to share.")
        path = cfg.live_path(file)
        if not path.exists():
            return _err(f"No {file} found in your iRacing folder.")
        try:
            parsed = parse_ini(path.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            return _err(str(exc))
        secs = [s for s in (sections or list(parsed)) if s in parsed]
        if not secs:
            return _err("Pick at least one section to include.")
        recipe = recipes.build_recipe(name, file, parsed, secs)
        if not recipe["values"]:
            return _err("Those sections have no settings to share.")
        return _ok(text=recipes.recipe_json(recipe), name=recipe["name"],
                   count=len(recipe["values"]), file=file)

    def preview_recipe(self, text: str) -> dict:
        cfg = self._config()
        if cfg is None:
            return _err(self._cfg_error or "could not load configuration")
        from irtracker import recipes
        from irtracker.semdiff import parse_ini
        try:
            recipe = recipes.parse_recipe(text)
        except ValueError as exc:
            return _err(str(exc))
        path = cfg.live_path(recipe["file"])
        current = {}
        if path.exists():
            try:
                current = parse_ini(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                current = {}
        return _ok(name=recipe.get("name"), file=recipe["file"],
                   total=len(recipe.get("values", [])),
                   changes=recipes.recipe_changes(recipe, current),
                   fileExists=path.exists())

    def apply_recipe(self, text: str) -> dict:
        tracker = self._tracker()
        if tracker is None:
            return _err(self._cfg_error or "could not load configuration")
        cfg = tracker.cfg
        from irtracker import recipes
        from irtracker.semdiff import parse_ini
        try:
            recipe = recipes.parse_recipe(text)
        except ValueError as exc:
            return _err(str(exc))
        file = recipe["file"]
        if not file.lower().endswith(".ini"):
            return _err("Recipes can only change settings (.ini) files.")
        if sim_running(cfg.sim_processes):
            return _err("Can't change settings while iRacing is running. Close the "
                        "sim (and the iRacing UI) first, then try again.", simBlocked=True)
        path = cfg.live_path(file)
        if not path.exists():
            return _err(f"You don't have a {file} to apply this to yet.")
        try:
            cur_text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return _err(str(exc))
        changes = recipes.recipe_changes(recipe, parse_ini(cur_text))
        if not changes:
            return _ok(applied=0, message="Your settings already match this recipe.")
        tracker.take_snapshot("pre_restore",
                              message=f'before applying recipe "{recipe.get("name")}"')
        patch = {(c["section"], c["key"]): c["new"] for c in changes}
        try:
            path.write_text(recipes.patch_ini_text(cur_text, patch), encoding="utf-8")
        except OSError as exc:
            return _err(str(exc))
        tracker.take_snapshot(
            "manual", names={file},
            message=f'applied recipe "{recipe.get("name")}" ({len(changes)} settings)')
        return _ok(applied=len(changes),
                   message=f"Applied {len(changes)} setting(s) from \"{recipe.get('name')}\".")


    # -- setup documentation export (Markdown / PDF) ---------------------------

    def _documentation_blocks(self, cfg, profile=None) -> list:
        from datetime import datetime
        from irtracker import build as build_mod
        from irtracker.semdiff import parse_ini
        from irtracker.report import friendly_name
        blocks = [("h1", f"iRacing setup — {datetime.now().strftime('%Y-%m-%d %H:%M')}")]
        b = build_mod.current_build()
        if b:
            blocks.append(("kv", ("iRacing build", b)))
        active = active_control_profile(cfg.iracing_dir)
        view = profile if profile in cfg.control_profile_names() else active
        if view:
            blocks.append(("kv", ("Active control profile", view)))

        controls = self._profile_file(cfg, "controls.cfg", view)
        doc = None
        if controls.exists():
            try:
                doc = codec.decode_bytes(controls.read_bytes())
            except (OSError, GfccError):
                doc = None
        if doc is not None:
            from irtracker.gfcc.devices import build_report
            jc = self._profile_file(cfg, "joyCalib.yaml", view)
            report = build_report(doc, jc.read_text(encoding="utf-8", errors="replace")
                                  if jc.exists() else None)
            blocks.append(("h2", "Devices"))
            blocks.append(("h3", "Connected now"))
            for d in report.connected or []:
                blocks.append(("li", f"{d.name or 'Game controller'} — {d.instance_guid}"))
            if not report.connected:
                blocks.append(("li", "(none detected)"))
            if report.referenced:
                blocks.append(("h3", "Used in your controls"))
                for d in report.referenced:
                    blocks.append(("li", f"{d.name or 'Game controller'} — {d.instance_guid}"))

            groups: dict = {}
            for bnd in self._bindings(doc):
                if bnd["kind"] == "unbound":
                    continue
                groups.setdefault(bnd["device"] or "Other", []).append(bnd)
            blocks.append(("h2", f"Controls — {sum(len(v) for v in groups.values())} assignments"))
            for dev in sorted(groups, key=lambda d: (d == "Keyboard", d)):
                blocks.append(("h3", dev))
                for bnd in sorted(groups[dev], key=lambda x: _pretty_action(x["action"])):
                    blocks.append(("kv", (_pretty_action(bnd["action"]), bnd["display"])))

        blocks.append(("h2", "Settings"))
        for name in cfg.tracked_files_present():
            if not name.lower().endswith(".ini"):
                continue
            try:
                parsed = parse_ini(cfg.live_path(name).read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if not parsed:
                continue
            blocks.append(("h3", f"{friendly_name(name)} ({name})"))
            for section, keys in parsed.items():
                blocks.append(("text", f"[{section}]"))
                for k, v in keys.items():
                    blocks.append(("kv", (k, v)))
        return blocks

    def documentation_markdown(self, profile: str | None = None) -> dict:
        cfg = self._config()
        if cfg is None:
            return _err(self._cfg_error or "could not load configuration")
        from irtracker import report
        try:
            text = report.documentation_markdown(self._documentation_blocks(cfg, profile))
        except Exception as exc:
            return _err(str(exc))
        return _ok(text=text)

    def _save_dialog(self, default_name: str, file_types: str):
        """Native save dialog -> Path; None if the user cancelled; Desktop fallback
        when there's no native window."""
        if self._window is not None:
            try:
                import webview
                picked = self._window.create_file_dialog(
                    webview.SAVE_DIALOG, save_filename=default_name, file_types=(file_types,))
                if not picked:
                    return None
                return Path(picked if isinstance(picked, str) else picked[0])
            except Exception as exc:  # pragma: no cover - dialog edge cases
                log.warning("save dialog failed (%s); falling back to Desktop", exc)
        desktop = Path.home() / "Desktop"
        return (desktop if desktop.is_dir() else Path.home()) / default_name

    def export_documentation_pdf(self, profile: str | None = None) -> dict:
        cfg = self._config()
        if cfg is None:
            return _err(self._cfg_error or "could not load configuration")
        from datetime import datetime
        from irtracker import report
        try:
            pdf = report.build_setup_pdf(self._documentation_blocks(cfg, profile),
                                         WEBUI_DIR / "logo.png")
        except Exception as exc:
            return _err(f"Couldn't build the PDF: {exc}")
        dest = self._save_dialog(f"iracing-setup-{datetime.now().strftime('%Y%m%d')}.pdf",
                                 "PDF document (*.pdf)")
        if dest is None:
            return _ok(cancelled=True)
        try:
            Path(dest).write_bytes(pdf)
        except OSError as exc:
            return _err(str(exc))
        return _ok(path=str(dest), message=f"Saved setup documentation to {dest}")

    # -- Discord webhook on snapshot (opt-in) ----------------------------------

    @staticmethod
    def _discord_prefs(cfg) -> dict:
        try:
            p = json.loads((cfg.state_dir / "notify.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            p = {}
        return {"webhook": p.get("discord_webhook", ""),
                "enabled": bool(p.get("discord_on_snapshot"))}

    def get_discord(self) -> dict:
        cfg = self._config()
        if cfg is None:
            return _err(self._cfg_error or "could not load configuration")
        return _ok(**self._discord_prefs(cfg))

    def set_discord(self, webhook: str, enabled) -> dict:
        cfg = self._config()
        if cfg is None:
            return _err(self._cfg_error or "could not load configuration")
        url = (webhook or "").strip()
        if enabled and not url.lower().startswith("https://"):
            return _err("Paste your Discord webhook URL (it starts with https://).")
        try:
            cfg.state_dir.mkdir(parents=True, exist_ok=True)
            (cfg.state_dir / "notify.json").write_text(json.dumps(
                {"discord_webhook": url, "discord_on_snapshot": bool(enabled)}, indent=2),
                encoding="utf-8")
        except OSError as exc:
            return _err(str(exc))
        return _ok(message="Saved Discord settings.")

    def send_discord_test(self, webhook: str) -> dict:
        from irtracker import notify
        url = (webhook or "").strip()
        if not url.lower().startswith("https://"):
            return _err("Paste your Discord webhook URL first (it starts with https://).")
        try:
            notify.discord_test(url)
        except Exception as exc:
            return _err(f"Couldn't post to that webhook ({exc}).")
        return _ok(message="Sent a test message — check your Discord channel.")

    def open_folder(self, which: str) -> dict:
        cfg = self._config()
        targets = {}
        if cfg:
            targets = {
                "iracing": cfg.iracing_dir,
                "data": cfg.data_dir,
                "repo": cfg.repo_dir,
                "backups": cfg.data_dir / "backups",
            }
        targets["config"] = Path(self._config_arg) if self._config_arg else config_path()
        target = targets.get(which)
        if target is None:
            return _err(f"unknown folder {which!r}")
        path = target.parent if which == "config" else target
        if not path.exists():
            return _err(f"{path} doesn't exist yet.")
        try:
            os.startfile(str(path))  # noqa: S606 - Windows Explorer, user-initiated
        except Exception as exc:
            return _err(str(exc))
        return _ok()

    def mark_onboarded(self) -> dict:
        """Remember that the first-run setup wizard has been completed/skipped."""
        cfg = self._config()
        if cfg is None:
            return _err(self._cfg_error or "could not load configuration")
        try:
            cfg.state_dir.mkdir(parents=True, exist_ok=True)
            (cfg.state_dir / "onboarded").write_text("1", encoding="utf-8")
        except OSError as exc:
            return _err(str(exc))
        return _ok()

    def open_url(self, url: str) -> dict:
        if not url or not str(url).startswith(("http://", "https://")):
            return _err("invalid URL")
        try:
            webbrowser.open(url)
        except Exception as exc:
            return _err(str(exc))
        return _ok()


# -- HTML assembly ---------------------------------------------------------------

def build_html() -> str:
    """Inline styles.css, app.js and the logo into index.html so the page is a
    single, self-contained document (works identically under pywebview and a
    browser)."""
    html = (WEBUI_DIR / "index.html").read_text(encoding="utf-8")
    css = (WEBUI_DIR / "styles.css").read_text(encoding="utf-8")
    js = (WEBUI_DIR / "app.js").read_text(encoding="utf-8")
    logo_uri = ""
    logo = WEBUI_DIR / "logo.png"
    if logo.exists():
        logo_uri = "data:image/png;base64," + base64.b64encode(logo.read_bytes()).decode("ascii")
    return (html.replace("/*__STYLES__*/", css)
                .replace("/*__APP_JS__*/", js)
                .replace("__LOGO_URI__", logo_uri))


# -- launchers -------------------------------------------------------------------

def _setup_tray(api: "GuiApi", window) -> None:
    """Best-effort system-tray + minimize-to-tray-on-close. Any failure leaves
    the app with normal close-to-quit behaviour, so this never raises."""
    try:
        if not api.tray_enabled():
            return
    except Exception:
        return
    st = {"tray": None, "quitting": False, "notified": False}

    def on_open():
        try:
            window.show()
        except Exception:
            pass

    def on_backup():
        try:
            r = api.backup_now(None)
            tray = st["tray"]
            if tray is not None:
                msg = "Backup saved." if r.get("created") else (r.get("message") or "Already up to date.")
                try:
                    tray.notify(msg, "iRacing Config Tracker")
                except Exception:
                    pass
        except Exception:
            pass

    def on_quit():
        st["quitting"] = True
        if st["tray"] is not None:
            try:
                st["tray"].stop()
            except Exception:
                pass
        try:
            window.destroy()
        except Exception:
            pass

    try:
        from irtracker import tray as tray_mod
        st["tray"] = tray_mod.start_tray(
            str(_webui_dir() / "logo.png"),
            on_open=on_open, on_backup=on_backup, on_quit=on_quit)
    except Exception as exc:
        log.info("tray setup skipped (%s)", exc)
        return
    if st["tray"] is None:
        return

    def on_closing(*_args):
        if st["quitting"]:
            return True  # a real quit was requested from the tray menu
        try:
            window.hide()
        except Exception:
            return True  # can't hide -> let it close normally
        if not st["notified"]:
            st["notified"] = True
            try:
                st["tray"].notify("Still running in the tray — click the icon to reopen.",
                                  "iRacing Config Tracker")
            except Exception:
                pass
        return False  # cancel the close: the app stays in the tray

    try:
        window.events.closing += on_closing
    except Exception as exc:
        log.info("could not hook window close (%s); tray will be info-only", exc)


def _launch_pywebview(api: GuiApi) -> bool:
    try:
        import webview
    except ImportError:
        return False
    try:
        window = webview.create_window(
            WINDOW_TITLE, html=build_html(), js_api=api,
            width=1280, height=820, min_size=(960, 640),
            background_color="#0b1020")
        api._window = window
        _setup_tray(api, window)  # best-effort; never raises
        webview.start()
        return True
    except Exception as exc:
        # Any native-window failure (e.g. missing WebView2 runtime, a broken
        # bundle) drops through to the browser transport rather than crashing.
        log.warning("native window unavailable (%s); falling back to browser", exc)
        return False


class _BrowserBridge:
    """Minimal stdlib HTTP transport used when pywebview isn't installed."""

    def __init__(self, api: GuiApi):
        self.api = api
        self._html = build_html()

    def serve(self, port: int = 0, open_browser: bool = True) -> None:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        api, html = self.api, self._html

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):  # silence per-request logging
                pass

            def _send(self, code, body: bytes, ctype="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path in ("/", "/index.html"):
                    self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
                else:
                    self._send(404, b'{"ok":false,"error":"not found"}')

            def do_POST(self):
                if not self.path.startswith("/api/"):
                    self._send(404, b'{"ok":false,"error":"not found"}')
                    return
                method = self.path[len("/api/"):]
                fn = getattr(api, method, None)
                if not callable(fn) or method.startswith("_"):
                    self._send(404, json.dumps(
                        {"ok": False, "error": f"unknown action {method!r}"}).encode())
                    return
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(length) if length else b"[]"
                try:
                    args = json.loads(raw or b"[]")
                    result = fn(*args)
                except Exception as exc:  # pragma: no cover - defensive
                    result = {"ok": False, "error": str(exc)}
                self._send(200, json.dumps(result).encode("utf-8"))

        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        host, bound_port = server.server_address
        url = f"http://{host}:{bound_port}/"
        print(f"{WINDOW_TITLE} is open in your browser: {url}")
        print("Close this window (or press Ctrl+C) to quit.")
        if open_browser:
            threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.shutdown()


def launch(config_arg: str | None = None) -> int:
    api = GuiApi(config_arg)
    # IRTRACK_GUI_BROWSER forces the browser transport (skips the native window);
    # IRTRACK_GUI_PORT / IRTRACK_GUI_NO_OPEN are mainly for testing.
    force_browser = bool(os.environ.get("IRTRACK_GUI_BROWSER"))
    if not force_browser and _launch_pywebview(api):
        return 0
    if not force_browser:
        print("(Tip: install the 'pywebview' package to get a real app window: "
              "pip install pywebview)")
    port = int(os.environ.get("IRTRACK_GUI_PORT") or 0)
    open_browser = os.environ.get("IRTRACK_GUI_NO_OPEN") is None
    _BrowserBridge(api).serve(port=port, open_browser=open_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(launch())
