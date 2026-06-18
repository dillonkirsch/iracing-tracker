"""Git-backed snapshot store (requirements section 8).

The repo working tree mirrors the tracked files; one commit per snapshot;
metadata travels as a `snapshot-meta:` JSON line in the commit message; tags
mark named baselines. Plain `git log -p -- app.ini` works out of the box, and
`.gitattributes` + diff.gfcc.textconv render controls.cfg as JSON in git diff.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Hide the console window when shelling out to git. Without this, a windowed
# (no-console) app like the packaged GUI/watcher pops a brief console window on
# every git call, which flickers and steals focus from other apps.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

META_PREFIX = "snapshot-meta: "

TRIGGER_LABELS = {
    "event": "change detected",
    "startup_scan": "startup scan",
    "rescan": "periodic rescan",
    "sim_exit": "sim exit",
    "manual": "manual",
    "pre_restore": "auto-snapshot before restore",
    "restore": "restore",
    "resume_scan": "watcher resume scan",
}


@dataclass
class SnapshotMeta:
    trigger: str
    files: dict[str, str]  # name -> added | modified | deleted
    sim_running: bool = False
    car: str | None = None
    track: str | None = None
    message: str | None = None
    time: str = ""
    collapsed: bool = False

    def to_json(self) -> str:
        return json.dumps({
            "trigger": self.trigger, "files": self.files,
            "sim_running": self.sim_running, "car": self.car, "track": self.track,
            "message": self.message, "time": self.time, "collapsed": self.collapsed,
        }, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "SnapshotMeta":
        d = json.loads(text)
        return cls(
            trigger=d.get("trigger", "unknown"), files=d.get("files", {}),
            sim_running=bool(d.get("sim_running")), car=d.get("car"),
            track=d.get("track"), message=d.get("message"),
            time=d.get("time", ""), collapsed=bool(d.get("collapsed")),
        )

    def context_label(self) -> str:
        if self.car or self.track:
            parts = [p for p in (self.car, self.track) if p]
            return " @ ".join(parts)
        if self.sim_running or self.trigger == "sim_exit":
            return "sim (car/track unknown)"
        return "manual edit"

    def subject(self) -> str:
        names = ", ".join(sorted(self.files)) or "no files"
        label = TRIGGER_LABELS.get(self.trigger, self.trigger)
        return f"{names} ({label})"


@dataclass
class Snapshot:
    commit: str
    author_date: str
    meta: SnapshotMeta
    tags: list[str] = field(default_factory=list)

    @property
    def short(self) -> str:
        return self.commit[:8]


class GitError(RuntimeError):
    pass


class SnapshotRepo:
    def __init__(self, repo_dir: Path):
        self.dir = repo_dir

    # -- plumbing ---------------------------------------------------------

    def git(self, *args: str, check: bool = True,
            text: bool = True) -> subprocess.CompletedProcess:
        # Force UTF-8 for text mode: git stores commit messages/metadata as UTF-8
        # bytes, but Python would otherwise decode them with the Windows locale
        # (cp1252), corrupting non-ASCII (accented car/track names, the → arrow).
        proc = subprocess.run(
            ["git", "-C", str(self.dir), *args],
            capture_output=True, text=text, creationflags=_NO_WINDOW,
            **({"encoding": "utf-8", "errors": "replace"} if text else {}),
        )
        if check and proc.returncode != 0:
            err = proc.stderr if text else proc.stderr.decode("utf-8", "replace")
            raise GitError(f"git {' '.join(args)} failed: {err.strip()}")
        return proc

    @property
    def initialized(self) -> bool:
        return (self.dir / ".git").is_dir()

    def init(self, textconv_cmd: str | None = None) -> None:
        """Create the repo, identity, and gfcc textconv wiring (idempotent)."""
        self.dir.mkdir(parents=True, exist_ok=True)
        if not self.initialized:
            self.git("init", "-b", "main")
            log.info("initialized snapshot repo at %s", self.dir)
        self.git("config", "user.name", "iRacing Config Tracker")
        self.git("config", "user.email", "tracker@localhost")
        # Configs land verbatim; never normalize line endings.
        self.git("config", "core.autocrlf", "false")
        attrs = self.dir / ".gitattributes"
        wanted = "* -text\ncontrols.cfg diff=gfcc\n"
        if not attrs.exists() or attrs.read_text(encoding="utf-8") != wanted:
            attrs.write_text(wanted, encoding="utf-8")
        if textconv_cmd:
            self.git("config", "diff.gfcc.textconv", textconv_cmd)
            self.git("config", "diff.gfcc.binary", "true")

    def head(self) -> str | None:
        proc = self.git("rev-parse", "--verify", "HEAD", check=False)
        return proc.stdout.strip() if proc.returncode == 0 else None

    def resolve(self, rev: str) -> str:
        proc = self.git("rev-parse", "--verify", f"{rev}^{{commit}}", check=False)
        if proc.returncode != 0:
            raise GitError(f"unknown version {rev!r}")
        return proc.stdout.strip()

    # -- snapshots ---------------------------------------------------------

    def working_changes(self) -> dict[str, str]:
        """Working-tree changes vs HEAD as {name: added|modified|deleted}."""
        out = self.git("status", "--porcelain", "--untracked-files=all").stdout
        changes: dict[str, str] = {}
        for line in out.splitlines():
            status, name = line[:2], line[3:].strip().strip('"')
            if name == ".gitattributes":
                continue
            if "D" in status:
                changes[name] = "deleted"
            elif "?" in status or "A" in status:
                changes[name] = "added"
            else:
                changes[name] = "modified"
        return changes

    def commit_snapshot(self, meta: SnapshotMeta, amend: bool = False) -> str:
        if not meta.time:
            meta.time = datetime.now().astimezone().isoformat(timespec="seconds")
        self.git("add", "-A")
        msg = f"{meta.subject()}\n\n{META_PREFIX}{meta.to_json()}\n"
        args = ["commit", "-m", msg]
        if amend:
            args.append("--amend")
        self.git(*args)
        return self.head() or ""

    def commit_is_tagged(self, commit: str) -> bool:
        out = self.git("tag", "--points-at", commit, check=False).stdout
        return bool(out.strip())

    # -- history -----------------------------------------------------------

    def log(self, path: str | None = None, limit: int | None = None,
            follow: bool = False) -> list[Snapshot]:
        sep, rec_sep = "\x1f", "\x1e"
        args = ["log", f"--format=%H{sep}%aI{sep}%B{rec_sep}"]
        if limit:
            args.append(f"-n{limit}")
        if follow and path:
            args.append("--follow")  # track the file across renames (e.g. the
            #                          controls.cfg -> profile-folder migration)
        if path:
            args += ["--", path]
        proc = self.git(*args, check=False)
        if proc.returncode != 0:
            return []  # no commits yet
        tags = self._tags_by_commit()
        snapshots = []
        for record in proc.stdout.split(rec_sep):
            record = record.strip("\n")
            if not record.strip():
                continue
            commit, date, body = record.split(sep, 2)
            commit = commit.strip()
            snapshots.append(Snapshot(
                commit=commit, author_date=date,
                meta=self._parse_meta(body),
                tags=tags.get(commit, []),
            ))
        return snapshots

    @staticmethod
    def _parse_meta(body: str) -> SnapshotMeta:
        for line in body.splitlines():
            if line.startswith(META_PREFIX):
                try:
                    return SnapshotMeta.from_json(line[len(META_PREFIX):])
                except json.JSONDecodeError:
                    break
        subject = body.splitlines()[0] if body.splitlines() else ""
        return SnapshotMeta(trigger="unknown", files={}, message=subject)

    def snapshot_at(self, rev: str) -> Snapshot:
        commit = self.resolve(rev)
        sep = "\x1f"
        out = self.git("log", "-1", f"--format=%H{sep}%aI{sep}%B", commit).stdout
        c, date, body = out.split(sep, 2)
        tags = self.git("tag", "--points-at", commit).stdout.split()
        return Snapshot(commit=c.strip(), author_date=date,
                        meta=self._parse_meta(body), tags=tags)

    def _tags_by_commit(self) -> dict[str, list[str]]:
        out = self.git("for-each-ref", "refs/tags",
                       "--format=%(objectname) %(refname:short)", check=False).stdout
        result: dict[str, list[str]] = {}
        for line in out.splitlines():
            commit, _, name = line.partition(" ")
            # Annotated tags: resolve to the commit they point at.
            target = self.git("rev-parse", f"{name}^{{commit}}", check=False).stdout.strip()
            result.setdefault(target or commit, []).append(name)
        return result

    # -- content access ----------------------------------------------------

    def show_file(self, rev: str, name: str) -> bytes:
        """Byte-exact blob content of a file at a revision (FR-19)."""
        commit = self.resolve(rev)
        proc = self.git("cat-file", "blob", f"{commit}:{name}", text=False, check=False)
        if proc.returncode != 0:
            raise GitError(f"{name!r} does not exist in version {rev}")
        return proc.stdout

    def file_exists_at(self, rev: str, name: str) -> bool:
        commit = self.resolve(rev)
        proc = self.git("cat-file", "-e", f"{commit}:{name}", check=False)
        return proc.returncode == 0

    def files_at(self, rev: str) -> list[str]:
        commit = self.resolve(rev)
        # -r so per-profile files (profiles/controls/<name>/controls.cfg) come
        # back as full paths, not just the top-level "profiles" directory.
        out = self.git("ls-tree", "-r", "--name-only", commit).stdout
        return [n for n in out.splitlines() if n and n != ".gitattributes"]

    def tracked_in_worktree(self) -> list[str]:
        out = self.git("ls-files", check=False).stdout
        return [n for n in out.splitlines() if n and n != ".gitattributes"]

    # -- tags ----------------------------------------------------------------

    def create_tag(self, name: str, rev: str = "HEAD", message: str | None = None) -> None:
        commit = self.resolve(rev)
        if message:
            self.git("tag", "-a", name, commit, "-m", message)
        else:
            self.git("tag", name, commit)

    def delete_tag(self, name: str) -> None:
        self.git("tag", "-d", name)

    def list_tags(self) -> list[tuple[str, str, str]]:
        """(tag, commit, message) tuples."""
        out = self.git(
            "for-each-ref", "refs/tags",
            "--format=%(refname:short)\x1f%(*objectname)%(objectname)\x1f%(contents:subject)",
            check=False).stdout
        tags = []
        for line in out.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 3:
                name, commit, msg = parts
                tags.append((name, commit[:40], msg))
        return tags

    def raw_git_diff(self, rev_a: str, rev_b: str | None, path: str | None = None) -> str:
        """git diff with textconv applied (controls.cfg renders as JSON)."""
        args = ["diff", self.resolve(rev_a)]
        if rev_b:
            args.append(self.resolve(rev_b))
        if path:
            args += ["--", path]
        return self.git(*args).stdout


def meta_for_export(snapshot: Snapshot) -> dict[str, Any]:
    return {
        "commit": snapshot.commit,
        "date": snapshot.author_date,
        "tags": snapshot.tags,
        "trigger": snapshot.meta.trigger,
        "files": snapshot.meta.files,
        "sim_running": snapshot.meta.sim_running,
        "car": snapshot.meta.car,
        "track": snapshot.meta.track,
        "message": snapshot.meta.message,
        "time": snapshot.meta.time,
    }
