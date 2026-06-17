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
    SIDECAR_NAME, Config, active_control_profile, config_path, is_sidecar, load_config)
from irtracker.gfcc import codec
from irtracker.gfcc.codec import GfccError
from irtracker.gfcc.patch import remap_device, remap_joycalib
from irtracker.repo import Snapshot
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
            "tags": list(s.tags),
            "collapsed": s.meta.collapsed,
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
        if repo.initialized and repo.head():
            snaps = repo.log()
            snapshot_count = len(snaps)
            if snaps:
                latest = self._snap_dict(snaps[0])

        pending = [{"name": n, "kind": k} for n, k in sorted(tracker.live_changes().items())]

        protected = []
        for tp in cfg.tracked:
            if tp.policy != "ignore":
                protected.append({"pattern": tp.pattern, "policy": tp.policy})

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
            snapshotCount=snapshot_count,
            protected=protected,
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
        return _ok(items=[self._snap_dict(s) for s in snaps])

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
        car = track = None
        if running:
            ctx = ContextCache(tracker.cfg.state_dir).context
            car, track = ctx.car, ctx.track
        result = tracker.take_snapshot("manual", message=(message or None),
                                       sim_running=running, car=car, track=track)
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
        from irtracker.gfcc.keymap import VK_NAMES
        devices = doc.get("_devices", {})
        out = []
        for e in doc["controls"]["entries"]:
            kind = e.get("type", "unbound")
            value = e.get("value", 0)
            display = "Not assigned"
            device = None

            if kind == "key":
                display = e.get("_key") or VK_NAMES.get(value, f"key {value}")
                device = "Keyboard"
            elif kind == "axis":
                display = f"Axis {value}"
            elif kind == "button":
                if "_button" in e:
                    display = e["_button"]
                elif value:
                    display = "+".join(f"Btn {b}" for b in _bits(value))

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
