"""Snapshot engine: syncs tracked files from the live iRacing folder into the
git repo, applying per-file policies (track / ignore / track-collapsed), INI
ignore-key suppression, the controls.cfg decoded sidecar, and restore flows.
"""
from __future__ import annotations

import json
import logging
import shutil
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from irtracker.config import SIDECAR_NAME, Config
from irtracker.gfcc import GfccError, codec
from irtracker.repo import Snapshot, SnapshotMeta, SnapshotRepo, meta_for_export
from irtracker import semdiff

log = logging.getLogger(__name__)


class SimRunningError(RuntimeError):
    """Live-folder write attempted while the sim is running (hard block, FR-18/24)."""


@dataclass
class SnapshotResult:
    commit: str | None = None
    files: dict[str, str] = field(default_factory=dict)
    skipped_ignored: list[str] = field(default_factory=list)
    collapsed: bool = False

    @property
    def committed(self) -> bool:
        return self.commit is not None


# Module-level so tests can zero it out.
SETTLE_SECONDS = 0.4


def stable_read(path: Path, retries: int = 6, settle: float | None = None) -> bytes | None:
    """Read a file once its size/mtime are stable across reads (FR-3).

    Retries with backoff on sharing violations / partial writes. Returns None
    if the file disappears or stays unreadable.
    """
    settle = SETTLE_SECONDS if settle is None else settle
    delay = max(settle, 0.05)
    for attempt in range(retries):
        try:
            st1 = path.stat()
            data = path.read_bytes()
            time.sleep(settle if attempt == 0 else min(delay, 2.0))
            st2 = path.stat()
            if (st1.st_size, st1.st_mtime_ns) == (st2.st_size, st2.st_mtime_ns) \
                    and len(data) == st1.st_size:
                return data
        except FileNotFoundError:
            return None
        except OSError as exc:
            log.debug("read retry %d for %s: %s", attempt + 1, path, exc)
        delay *= 1.7
        time.sleep(delay)
    log.error("file never stabilized for reading: %s", path)
    return None


class Tracker:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.repo = SnapshotRepo(cfg.repo_dir)

    # -- setup ---------------------------------------------------------------

    def ensure_repo(self) -> None:
        import sys
        textconv = f'"{sys.executable}" -m irtracker decode --textconv'
        self.repo.init(textconv_cmd=textconv.replace("\\", "/"))

    # -- snapshotting ----------------------------------------------------------

    def take_snapshot(
        self,
        trigger: str,
        names: set[str] | None = None,
        message: str | None = None,
        sim_running: bool = False,
        car: str | None = None,
        track: str | None = None,
    ) -> SnapshotResult:
        """Sync (a subset of) tracked files into the repo and commit if anything
        real changed. `names=None` means full scan, covering deletions too (FR-4).
        """
        self.ensure_repo()
        result = SnapshotResult()

        candidates = self._candidates(names)
        for name in sorted(candidates):
            kind = self._sync_file(name, result)
            if kind:
                result.files[name] = kind

        changes = self.repo.working_changes()
        if not changes:
            return result
        result.files = changes

        meta = SnapshotMeta(
            trigger=trigger, files=changes, sim_running=sim_running,
            car=car, track=track, message=message,
            time=datetime.now().astimezone().isoformat(timespec="seconds"),
        )

        amend = self._should_collapse(changes)
        meta.collapsed = amend
        result.collapsed = amend
        result.commit = self.repo.commit_snapshot(meta, amend=amend)
        log.info("snapshot %s: %s (%s)%s", result.commit[:8],
                 ", ".join(sorted(changes)), trigger, " [collapsed]" if amend else "")
        return result

    def _candidates(self, names: set[str] | None) -> set[str]:
        if names is not None:
            return {n for n in names if self._effective_policy(n)}
        live = set(self.cfg.tracked_files_present())
        # Files in the repo but gone from the live folder are deletion candidates.
        in_repo = {n for n in self.repo.tracked_in_worktree() if n != SIDECAR_NAME}
        return {n for n in live | in_repo if self._effective_policy(n)}

    def _effective_policy(self, name: str):
        tp = self.cfg.policy_for(name)
        return tp if tp and tp.policy != "ignore" else None

    def _sync_file(self, name: str, result: SnapshotResult) -> str | None:
        """Copy one live file into the working tree (or delete), honoring
        ignore-key suppression. Returns the change kind or None."""
        tp = self._effective_policy(name)
        if tp is None:
            return None
        live = self.cfg.live_path(name)
        mirror = self.repo.dir / name

        if not live.exists():
            if mirror.exists():
                mirror.unlink()
                self._refresh_sidecar(name, None)
                return "deleted"
            return None

        # Fast path: an unchanged file needs no stability dance.
        old = mirror.read_bytes() if mirror.exists() else None
        try:
            if old is not None and live.read_bytes() == old:
                return None
        except OSError:
            pass  # locked or mid-write; fall through to the patient read
        data = stable_read(live)
        if data is None:
            return None
        if old is not None:
            if old == data:
                return None
            if tp.ignore_keys and name.lower().endswith(".ini"):
                changes = semdiff.diff_ini(
                    old.decode("utf-8", "replace"), data.decode("utf-8", "replace"))
                if semdiff.only_ignored_changes(changes, tp.ignore_keys):
                    result.skipped_ignored.append(name)
                    log.debug("%s: only ignored keys changed, skipping", name)
                    return None
            kind = "modified"
        else:
            kind = "added"

        mirror.parent.mkdir(parents=True, exist_ok=True)
        mirror.write_bytes(data)
        self._refresh_sidecar(name, data)
        return kind

    def _refresh_sidecar(self, name: str, data: bytes | None) -> None:
        """Regenerate controls.decoded.json whenever controls.cfg changes (M3).
        If decoding fails, raw versioning continues and the decoded view is
        marked unavailable for that version (FR-25)."""
        if name.lower() != "controls.cfg":
            return
        sidecar = self.repo.dir / SIDECAR_NAME
        if data is None:
            if sidecar.exists():
                sidecar.unlink()
            return
        try:
            doc = codec.decode_bytes(data)
        except GfccError as exc:
            doc = {
                "decode_error": str(exc),
                "_comment": "controls.cfg could not be decoded for this version; "
                            "raw byte history is unaffected (FR-25).",
            }
            log.warning("controls.cfg decode failed; sidecar marks it unavailable: %s", exc)
        sidecar.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

    def _should_collapse(self, changes: dict[str, str]) -> bool:
        """Amend instead of stacking commits when this change and the previous
        snapshot both touch only track-collapsed files with the same file set."""
        config_files = [n for n in changes if n != SIDECAR_NAME]
        if not config_files:
            return False
        for name in config_files:
            tp = self.cfg.policy_for(name)
            if not tp or tp.policy != "track-collapsed":
                return False
        head = self.repo.head()
        if head is None or self.repo.commit_is_tagged(head):
            return False
        prev = self.repo.snapshot_at("HEAD")
        prev_files = {n for n in prev.meta.files if n != SIDECAR_NAME}
        if prev_files != set(config_files):
            return False
        # Never collapse the repo's very first commit away.
        if self.repo.git("rev-parse", "HEAD~1", check=False).returncode != 0:
            return False
        return True

    # -- restore -----------------------------------------------------------------

    def restore_file(self, name: str, rev: str, sim_is_running: bool) -> str:
        """Restore one file to a version: byte-exact blob copy (FR-15/19)."""
        if sim_is_running:
            raise SimRunningError("restore is blocked while the sim is running (FR-18)")
        if name == SIDECAR_NAME:
            raise ValueError(f"{SIDECAR_NAME} is derived from controls.cfg; restore controls.cfg instead")
        data = self.repo.show_file(rev, name)
        self.take_snapshot("pre_restore", message=f"auto-snapshot before restoring {name} to {rev}")
        self.cfg.live_path(name).write_bytes(data)
        commit = self.take_snapshot(
            "restore", names={name},
            message=f"restored {name} to {self.repo.resolve(rev)[:8]}").commit
        return commit or "(file matched repo HEAD; no new commit)"

    def restore_baseline(self, tag: str, sim_is_running: bool) -> tuple[list[str], list[str]]:
        """Restore every tracked file recorded in a tagged baseline (FR-16).
        Returns (restored, skipped) names. Files in the live folder but absent
        from the baseline are reported, not deleted."""
        if sim_is_running:
            raise SimRunningError("restore is blocked while the sim is running (FR-18)")
        files = [n for n in self.repo.files_at(tag) if n != SIDECAR_NAME]
        if not files:
            raise ValueError(f"no files recorded at {tag!r}")
        self.take_snapshot("pre_restore", message=f"auto-snapshot before restoring baseline {tag}")
        restored: list[str] = []
        for name in files:
            data = self.repo.show_file(tag, name)
            self.cfg.live_path(name).write_bytes(data)
            restored.append(name)
        extras = [n for n in self.cfg.tracked_files_present() if n not in files]
        self.take_snapshot("restore", message=f"restored baseline {tag}")
        return restored, extras

    # -- export ---------------------------------------------------------------------

    def export(self, rev: str, out_zip: Path) -> list[str]:
        """Portable zip of a snapshot: all files plus metadata (FR-14)."""
        snap = self.repo.snapshot_at(rev)
        names = self.repo.files_at(rev)
        out_zip.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in names:
                zf.writestr(name, self.repo.show_file(rev, name))
            zf.writestr("snapshot-metadata.json",
                        json.dumps(meta_for_export(snap), indent=2))
        return names

    # -- queries ----------------------------------------------------------------------

    def filtered_log(self, path: str | None = None, car: str | None = None,
                     track: str | None = None, trigger: str | None = None,
                     tag_only: bool = False, limit: int | None = None) -> list[Snapshot]:
        """History filterable by file, car/track context, trigger, tags (FR-8)."""
        snaps = self.repo.log(path=path)
        out = []
        for s in snaps:
            if car and car.lower() not in (s.meta.car or "").lower():
                continue
            if track and track.lower() not in (s.meta.track or "").lower():
                continue
            if trigger and s.meta.trigger != trigger:
                continue
            if tag_only and not s.tags:
                continue
            out.append(s)
            if limit and len(out) >= limit:
                break
        return out

    def live_changes(self) -> dict[str, str]:
        """What would be committed if a snapshot ran now (for `status`)."""
        changes: dict[str, str] = {}
        if not self.repo.initialized:
            return {n: "added" for n in self.cfg.tracked_files_present()}
        for name in sorted(self._candidates(None)):
            live = self.cfg.live_path(name)
            mirror = self.repo.dir / name
            if not live.exists():
                if mirror.exists():
                    changes[name] = "deleted"
                continue
            try:
                data = live.read_bytes()
            except OSError:
                changes[name] = "unreadable"
                continue
            if not mirror.exists():
                changes[name] = "added"
            elif mirror.read_bytes() != data:
                changes[name] = "modified"
        return changes


def backup_live_file(cfg: Config, name: str) -> Path | None:
    """Timestamped copy of a live file into data_dir\\backups (encode --install)."""
    src = cfg.live_path(name)
    if not src.exists():
        return None
    backups = cfg.data_dir / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backups / f"{name}.{stamp}.bak"
    shutil.copy2(src, dest)
    return dest
