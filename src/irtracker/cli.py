"""Command-line interface (FR-29): status, log, diff, show, restore, snapshot,
tag, export, decode, encode, devices, watcher.

`irtrack` exposes everything; `gfcc` is a codec-focused alias so the section 7
flow (`gfcc decode controls.cfg -o controls.json`) works verbatim.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from irtracker import __version__, semdiff
from irtracker.config import (
    SIDECAR_NAME, Config, config_path, is_sidecar, load_config, setup_logging)
from irtracker.gfcc import codec
from irtracker.gfcc.codec import GfccError
from irtracker.gfcc.patch import apply_bindings, load_bindings, remap_device, remap_joycalib
from irtracker.repo import TRIGGER_LABELS, GitError, Snapshot
from irtracker.simstate import ContextCache, sim_running
from irtracker.snapshot import SimRunningError, Tracker, backup_live_file

log = logging.getLogger(__name__)


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def _load(args) -> Config:
    return load_config(Path(args.config) if args.config else None)


def _fmt_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


def _print_snapshot_line(s: Snapshot) -> None:
    label = TRIGGER_LABELS.get(s.meta.trigger, s.meta.trigger)
    print(f"{s.short}  {_fmt_date(s.author_date)}  {label:<28} {s.meta.context_label()}")
    files = ", ".join(sorted(n for n in s.meta.files if not is_sidecar(n))) or "-"
    tag_str = f"  [{', '.join(s.tags)}]" if s.tags else ""
    print(f"          {files}{tag_str}")
    if s.meta.message:
        print(f'          "{s.meta.message}"')


# -- commands -------------------------------------------------------------------


def cmd_status(args) -> int:
    cfg = _load(args)
    tracker = Tracker(cfg)
    from irtracker import tasksched, watcher as watcher_mod

    print(f"config:      {args.config or config_path()}")
    print(f"iRacing dir: {cfg.iracing_dir}")
    print(f"repo:        {cfg.repo_dir} "
          f"({'initialized' if tracker.repo.initialized else 'not initialized yet'})")
    running = sim_running(cfg.sim_processes)
    print(f"sim:         {'RUNNING' if running else 'not running'}")

    state = watcher_mod.read_state(cfg)
    if state and watcher_mod.watcher_alive(cfg):
        mode = "paused" if state.get("paused") else "active"
        print(f"watcher:     {mode} (pid {state.get('pid')}, started {state.get('started')})")
        if state.get("last_snapshot"):
            print(f"             last snapshot {state['last_snapshot']}")
    else:
        print("watcher:     not running")
    autostart = tasksched.installed_status()
    print(f"autostart:   {', '.join(autostart) if autostart else 'not installed'}")

    if tracker.repo.initialized and tracker.repo.head():
        head = tracker.repo.snapshot_at("HEAD")
        print(f"\nlatest snapshot: {head.short}  {_fmt_date(head.author_date)}  "
              f"({TRIGGER_LABELS.get(head.meta.trigger, head.meta.trigger)})")
    changes = tracker.live_changes()
    if changes:
        print("\npending changes (not yet snapshotted):")
        for name, kind in sorted(changes.items()):
            print(f"  {kind:<10} {name}")
    else:
        print("\nno pending changes; live folder matches the latest snapshot")
    return 0


def cmd_log(args) -> int:
    cfg = _load(args)
    tracker = Tracker(cfg)
    if not tracker.repo.initialized or not tracker.repo.head():
        return _fail("no snapshots yet; run `irtrack snapshot` first")
    snaps = tracker.filtered_log(
        path=args.file, car=args.car, track=args.track,
        trigger=args.trigger, tag_only=args.tags, limit=args.limit)
    if not snaps:
        print("no snapshots match the given filters")
        return 0
    for s in snaps:
        _print_snapshot_line(s)
    return 0


def _semantic_file_diff(name: str, old: bytes | None, new: bytes | None,
                        old_label: str, new_label: str, raw: bool) -> str:
    if old is None:
        return f"(added in {new_label})"
    if new is None:
        return f"(deleted in {new_label}, existed in {old_label})"
    if name.lower() == "controls.cfg":
        try:
            old_doc, new_doc = codec.decode_bytes(old), codec.decode_bytes(new)
        except GfccError as exc:
            return f"(decoded diff unavailable: {exc}; raw bytes differ)"
        if raw:
            return semdiff.raw_diff(
                json.dumps(old_doc, indent=2), json.dumps(new_doc, indent=2),
                old_label, new_label)
        lines = semdiff.diff_controls(old_doc, new_doc)
        return "\n".join(lines) if lines else "(binary metadata changed only)"
    old_text = old.decode("utf-8", "replace")
    new_text = new.decode("utf-8", "replace")
    if raw:
        return semdiff.raw_diff(old_text, new_text, old_label, new_label)
    if name.lower().endswith(".ini"):
        return semdiff.render_changes(semdiff.diff_ini(old_text, new_text))
    if name.lower().endswith((".yaml", ".yml")):
        return semdiff.render_changes(semdiff.diff_yaml(old_text, new_text))
    return semdiff.raw_diff(old_text, new_text, old_label, new_label)


def cmd_diff(args) -> int:
    cfg = _load(args)
    tracker = Tracker(cfg)
    repo = tracker.repo
    if not repo.initialized or not repo.head():
        return _fail("no snapshots yet; run `irtrack snapshot` first")

    rev_a = args.rev_a or "HEAD"
    rev_b = args.rev_b  # None means "live folder"
    old_label = rev_a
    new_label = rev_b or "live"

    if args.git:
        print(repo.raw_git_diff(rev_a, rev_b, args.file), end="")
        return 0

    names: set[str] = set(repo.files_at(rev_a))
    if rev_b:
        names |= set(repo.files_at(rev_b))
    else:
        names |= set(cfg.tracked_files_present())
    names = {n for n in names if not is_sidecar(n)}
    if args.file:
        names &= {args.file}
        if not names:
            return _fail(f"{args.file!r} not found in either side of the diff")

    printed = False
    for name in sorted(names):
        old = repo.show_file(rev_a, name) if repo.file_exists_at(rev_a, name) else None
        if rev_b:
            new = repo.show_file(rev_b, name) if repo.file_exists_at(rev_b, name) else None
        else:
            live = cfg.live_path(name)
            new = live.read_bytes() if live.exists() else None
        if old == new:
            continue
        body = _semantic_file_diff(name, old, new, old_label, new_label, args.raw)
        if not body.strip():
            continue
        printed = True
        print(f"=== {name} ({old_label} -> {new_label}) ===")
        print(body)
        print()
    if not printed:
        print(f"no differences between {old_label} and {new_label}")
    return 0


def cmd_show(args) -> int:
    cfg = _load(args)
    tracker = Tracker(cfg)
    repo = tracker.repo
    if not repo.initialized or not repo.head():
        return _fail("no snapshots yet")
    if not args.file:
        s = repo.snapshot_at(args.rev)
        _print_snapshot_line(s)
        print("\nfiles in this snapshot:")
        for name in repo.files_at(args.rev):
            print(f"  {name}")
        return 0
    data = repo.show_file(args.rev, args.file)
    if args.out:
        Path(args.out).write_bytes(data)
        print(f"wrote {args.out} ({len(data)} bytes)")
        return 0
    if args.file.lower() == "controls.cfg" and not args.raw:
        try:
            print(json.dumps(codec.decode_bytes(data), indent=2))
        except GfccError as exc:
            return _fail(f"cannot decode this version ({exc}); use -o to extract raw bytes")
        return 0
    if args.raw and args.file.lower() == "controls.cfg":
        return _fail("refusing to print raw binary to the console; use -o FILE")
    print(data.decode("utf-8", "replace"), end="")
    return 0


def cmd_snapshot(args) -> int:
    cfg = _load(args)
    tracker = Tracker(cfg)
    running = sim_running(cfg.sim_processes)
    context = ContextCache(cfg.state_dir).context
    result = tracker.take_snapshot(
        "manual", message=args.message, sim_running=running,
        car=context.car if running else None,
        track=context.track if running else None)
    if not result.committed:
        skipped = f" (ignored-key-only changes in: {', '.join(result.skipped_ignored)})" \
            if result.skipped_ignored else ""
        print(f"nothing to snapshot; live folder matches the latest version{skipped}")
        return 0
    print(f"snapshot {result.commit[:8]} created:")
    for name, kind in sorted(result.files.items()):
        print(f"  {kind:<10} {name}")
    return 0


def cmd_restore(args) -> int:
    cfg = _load(args)
    tracker = Tracker(cfg)
    running = sim_running(cfg.sim_processes)
    try:
        if args.tag:
            target = f"baseline {args.tag!r} across the full tracked set"
            if not args.yes:
                reply = input(f"Restore {target}? Current state is auto-snapshotted first. [y/N] ")
                if reply.strip().lower() not in ("y", "yes"):
                    print("aborted")
                    return 1
            restored, extras = tracker.restore_baseline(args.tag, running)
            print(f"restored {len(restored)} file(s) from {args.tag}:")
            for name in restored:
                print(f"  {name}")
            if extras:
                print("present in the live folder but not in this baseline (left untouched):")
                for name in extras:
                    print(f"  {name}")
        else:
            if not args.rev or not args.file:
                return _fail("usage: irtrack restore REV FILE   or   irtrack restore --tag TAG")
            if not args.yes:
                reply = input(f"Restore {args.file} to {args.rev}? "
                              f"Current state is auto-snapshotted first. [y/N] ")
                if reply.strip().lower() not in ("y", "yes"):
                    print("aborted")
                    return 1
            commit = tracker.restore_file(args.file, args.rev, running)
            print(f"restored {args.file} to {args.rev} (recorded as {commit[:8] if len(commit) == 40 else commit})")
    except SimRunningError as exc:
        return _fail(str(exc))
    return 0


def cmd_tag(args) -> int:
    cfg = _load(args)
    repo = Tracker(cfg).repo
    if args.list or (not args.name and not args.delete):
        tags = repo.list_tags()
        if not tags:
            print("no tags")
            return 0
        for name, commit, msg in tags:
            line = f"{name:<30} {commit[:8]}"
            if msg:
                line += f'  "{msg}"'
            print(line)
        return 0
    if args.delete:
        repo.delete_tag(args.delete)
        print(f"deleted tag {args.delete}")
        return 0
    repo.create_tag(args.name, args.rev or "HEAD", args.message)
    print(f"tagged {args.rev or 'HEAD'} as {args.name}")
    return 0


def cmd_export(args) -> int:
    cfg = _load(args)
    tracker = Tracker(cfg)
    out = Path(args.out)
    names = tracker.export(args.rev, out)
    print(f"exported {len(names)} file(s) from {args.rev} to {out}")
    return 0


def cmd_decode(args) -> int:
    if args.textconv:
        # git textconv driver: never fail the diff; FR-25 fallback output.
        try:
            data = Path(args.cfg).read_bytes()
            doc = codec.decode_bytes(data)
        except (OSError, GfccError) as exc:
            doc = {"decode_error": str(exc)}
        print(json.dumps(doc, indent=2))
        return 0
    try:
        data = Path(args.cfg).read_bytes()
        doc = codec.decode_bytes(data)
    except OSError as exc:
        return _fail(str(exc))
    except GfccError as exc:
        return _fail(f"cannot decode {args.cfg}: {exc}")
    text = json.dumps(doc, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(doc['controls']['entries'])} entries), "
              f"round trip verified")
    else:
        print(text, end="")
    return 0


def cmd_encode(args) -> int:
    base_path = Path(args.base)
    try:
        base = base_path.read_bytes()
    except OSError as exc:
        return _fail(str(exc))
    try:
        doc = codec.decode_bytes(base)  # refuses if base would not round-trip (FR-21)
        bindings = load_bindings(Path(args.bindings).read_text(encoding="utf-8"))
        changes = apply_bindings(doc, bindings)
        out_bytes = codec.build(doc)
        codec.decode_bytes(out_bytes)  # self-check on the result
    except OSError as exc:
        return _fail(str(exc))
    except GfccError as exc:
        return _fail(str(exc))

    for line in changes:
        print(f"  {line}")

    if not args.out and not args.install:
        return _fail("specify -o OUTPUT or --install (encode never overwrites the base in place, FR-24)")
    if args.out:
        Path(args.out).write_bytes(out_bytes)
        print(f"wrote {args.out} ({len(out_bytes)} bytes)")
    if args.install:
        cfg = _load(args)
        if sim_running(cfg.sim_processes):
            return _fail("install is blocked while the sim is running (FR-24)")
        backup = backup_live_file(cfg, "controls.cfg")
        target = cfg.live_path("controls.cfg")
        target.write_bytes(out_bytes)
        print(f"installed to {target}")
        if backup:
            print(f"previous file backed up to {backup}")
        print("note: final validation is loading it in the sim")
    return 0


def cmd_remap(args) -> int:
    """Repoint bindings from an old device instance GUID to a new one (FR-23):
    the fix for a wheel/pedals coming back on a new instance after a USB-port
    change or hardware swap."""
    cfg = None
    base_path = Path(args.base) if args.base else None
    if base_path is None:
        cfg = _load(args)
        base_path = cfg.live_path("controls.cfg")
    try:
        base = base_path.read_bytes()
    except OSError as exc:
        return _fail(str(exc))
    try:
        doc = codec.decode_bytes(base)
    except GfccError as exc:
        return _fail(f"cannot decode {base_path}: {exc}")

    old, new = args.src, args.dst
    if args.auto:
        from irtracker.gfcc.devices import build_report

        report = build_report(doc, None)
        connected = {d.product_guid: d.instance_guid for d in report.connected}
        connected_guids = {d.instance_guid for d in report.connected}
        drifted = [(d.instance_guid, connected[d.product_guid]) for d in report.referenced
                   if d.instance_guid not in connected_guids and d.product_guid in connected]
        if not drifted:
            return _fail("no drifted device found to auto-remap "
                         "(nothing referenced is connected under a new instance GUID)")
        if len(drifted) > 1:
            return _fail("multiple drifted devices; rerun with explicit --from/--to")
        old, new = drifted[0]
        print(f"auto-detected device drift: {old} -> {new}")
    if not old or not new:
        return _fail("specify --from OLD --to NEW (or --auto to detect it)")

    try:
        changed = remap_device(doc, old, new)
        out_bytes = codec.build(doc)
        codec.decode_bytes(out_bytes)  # self-check
    except GfccError as exc:
        return _fail(str(exc))
    if not changed:
        print(f"no bindings reference {old}; nothing to change")
        return 0
    print(f"re-mapped {len(changed)} binding(s): {', '.join(changed)}")

    if not args.out and not args.install:
        return _fail("specify -o OUTPUT or --install")
    if args.out:
        Path(args.out).write_bytes(out_bytes)
        print(f"wrote {args.out} ({len(out_bytes)} bytes)")
    if args.install:
        cfg = cfg or _load(args)
        if sim_running(cfg.sim_processes):
            return _fail("install is blocked while the sim is running (FR-24)")
        target = cfg.live_path("controls.cfg")
        backup = backup_live_file(cfg, "controls.cfg")
        target.write_bytes(out_bytes)
        print(f"installed to {target}")
        if backup:
            print(f"previous file backed up to {backup}")
        jc = cfg.live_path("joyCalib.yaml")
        if jc.exists():
            text = jc.read_text(encoding="utf-8", errors="replace")
            new_text, n = remap_joycalib(text, old, new)
            if n:
                backup_live_file(cfg, "joyCalib.yaml")
                jc.write_text(new_text, encoding="utf-8")
                print(f"updated joyCalib.yaml ({n} calibration GUID reference(s))")
        print("note: final validation is loading it in the sim")
    return 0


def cmd_whatis(args) -> int:
    """Reverse lookup: what is a given key/button/axis bound to?"""
    cfg = None
    base_path = Path(args.base) if args.base else None
    if base_path is None:
        cfg = _load(args)
        base_path = cfg.live_path("controls.cfg")
    try:
        data = base_path.read_bytes()
    except OSError as exc:
        return _fail(str(exc))
    try:
        doc = codec.decode_bytes(data)
    except GfccError as exc:
        return _fail(f"cannot decode {base_path}: {exc}")
    from irtracker.gfcc.analyze import find_input
    try:
        label, _kind, matches = find_input(doc, args.input)
    except GfccError as exc:
        return _fail(str(exc))
    if not matches:
        print(f"{label}: not bound (free)")
        return 0
    print(f"{label} is bound to:")
    for m in matches:
        print(f"  {m['action']}  ({m['device']})")
    return 0


def cmd_devices(args) -> int:
    from irtracker.gfcc.devices import build_report

    cfg = None
    base_doc = None
    joycalib = None
    base_path = Path(args.base) if args.base else None
    if base_path is None:
        try:
            cfg = _load(args)
            candidate = cfg.live_path("controls.cfg")
            base_path = candidate if candidate.exists() else None
        except SystemExit:
            base_path = None
    if base_path and base_path.exists():
        try:
            base_doc = codec.decode_bytes(base_path.read_bytes())
        except (OSError, GfccError) as exc:
            print(f"note: could not decode {base_path}: {exc}", file=sys.stderr)
    if cfg:
        jc = cfg.live_path("joyCalib.yaml")
        if jc.exists():
            joycalib = jc.read_text(encoding="utf-8", errors="replace")

    report = build_report(base_doc, joycalib)
    connected_guids = {d.instance_guid for d in report.connected}
    connected_products = {d.product_guid: d.instance_guid for d in report.connected}

    def presence(d) -> str:
        if d.instance_guid in connected_guids:
            return "connected"
        if d.product_guid and d.product_guid in connected_products:
            # FR-23 territory: same hardware, new instance GUID (USB port change).
            return (f"instance GUID drifted -- same product connected as "
                    f"{connected_products[d.product_guid]} (USB port change?)")
        return "NOT CONNECTED"

    print("connected game controllers (DirectInput):")
    if report.connected:
        for d in report.connected:
            note = f"  [{d.note}]" if d.note else ""
            print(f"  {d.name}\n    instance {d.instance_guid}  product {d.product_guid}{note}")
    else:
        print(f"  none found ({report.enum_error or 'no devices attached'})")

    if base_doc:
        print(f"\ndevices referenced by {base_path}:")
        if report.referenced:
            for d in report.referenced:
                name = f" {d.name}" if d.name else ""
                note = f"  [{d.note}]" if d.note else ""
                print(f"  {d.instance_guid}{name}  ({presence(d)}){note}")
        else:
            print("  none (keyboard-only file)")
    if report.calibrated:
        print("\ndevices in joyCalib.yaml:")
        for d in report.calibrated:
            print(f"  {d.name}  instance {d.instance_guid}  ({presence(d)})")

    if base_doc:
        from irtracker.gfcc.analyze import find_binding_conflicts

        conflicts = find_binding_conflicts(base_doc)
        if conflicts:
            print("\nbinding conflicts (one input bound to multiple actions):")
            for c in conflicts:
                print(f"  {c.label}: {', '.join(c.actions)}")
        else:
            print("\nno duplicate button/key bindings detected")
    return 0


def cmd_doctor(args) -> int:
    cfg = _load(args)
    from irtracker.doctor import FAIL, OK, WARN, run_checks, summarize

    mark = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}
    checks = run_checks(cfg)
    for c in checks:
        print(f"{mark.get(c.status, '[????]')} {c.name}: {c.detail}")
    fails, warns = summarize(checks)
    print(f"\n{len(checks)} checks - {fails} failed, {warns} warning(s)")
    return 1 if fails else 0


def cmd_gui(args) -> int:
    from irtracker.gui import launch
    return launch(args.config)


def cmd_watcher(args) -> int:
    cfg = _load(args)
    from irtracker import tasksched, watcher as watcher_mod

    action = args.action
    if action == "run":
        setup_logging(cfg, console=not args.quiet)
        return watcher_mod.Watcher(cfg).run()
    if action == "status":
        state = watcher_mod.read_state(cfg)
        if state and watcher_mod.watcher_alive(cfg):
            mode = "paused" if state.get("paused") else "active"
            print(f"watcher {mode} (pid {state['pid']}, started {state.get('started')})")
            print(f"sim {'running' if state.get('sim_running') else 'not running'}; "
                  f"context: {state.get('car') or '?'} @ {state.get('track') or '?'}")
            if state.get("last_snapshot"):
                print(f"last snapshot: {state['last_snapshot']}")
        else:
            print("watcher not running")
        autostart = tasksched.installed_status()
        print(f"autostart: {', '.join(autostart) if autostart else 'not installed'}")
        return 0
    if action == "pause":
        watcher_mod.request_pause(cfg)
        print("watcher paused (snapshots suspended; resume runs a catch-up scan)")
        return 0
    if action == "resume":
        watcher_mod.request_resume(cfg)
        print("watcher resumed")
        return 0
    if action == "stop":
        if not watcher_mod.watcher_alive(cfg):
            print("watcher not running")
            return 0
        watcher_mod.request_stop(cfg)
        print("stop requested; the watcher exits within a second")
        return 0
    if action == "install":
        desc = tasksched.install()
        print(f"installed: {desc}")
        print("start it now with `irtrack watcher run` or log off/on")
        return 0
    if action == "uninstall":
        removed = tasksched.uninstall()
        print(f"removed: {', '.join(removed) if removed else 'nothing was installed'}")
        return 0
    return _fail(f"unknown watcher action {action!r}")


# -- parsers ---------------------------------------------------------------------


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", help="path to config.toml (default: %%LOCALAPPDATA%%\\iracing-config-tracker\\config.toml)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="irtrack",
        description="Version control for iRacing configuration files.")
    ap.add_argument("--version", action="version", version=f"irtrack {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status", help="repo, watcher, sim, and pending-change overview")
    _add_common(p)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("log", help="snapshot history (filterable by file/car/track/trigger/tag)")
    _add_common(p)
    p.add_argument("-n", "--limit", type=int, default=20)
    p.add_argument("--file", help="only snapshots touching this file")
    p.add_argument("--car", help="substring match on car context")
    p.add_argument("--track", help="substring match on track context")
    p.add_argument("--trigger", choices=sorted(TRIGGER_LABELS),
                   help="only snapshots with this trigger")
    p.add_argument("--tags", action="store_true", help="only tagged snapshots")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("diff", help="semantic diff between versions (default: latest vs live)")
    _add_common(p)
    p.add_argument("rev_a", nargs="?", help="base version (default HEAD)")
    p.add_argument("rev_b", nargs="?", help="target version (default: live folder)")
    p.add_argument("--file", help="limit to one file")
    p.add_argument("--raw", action="store_true", help="raw line diff instead of semantic")
    p.add_argument("--git", action="store_true", help="raw `git diff` output (textconv applied)")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("show", help="snapshot metadata, or a file's content at a version")
    _add_common(p)
    p.add_argument("rev")
    p.add_argument("file", nargs="?")
    p.add_argument("-o", "--out", help="write file content to a path (byte-exact)")
    p.add_argument("--raw", action="store_true",
                   help="raw content for controls.cfg (requires -o)")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("restore", help="restore a file to a version, or a tagged baseline")
    _add_common(p)
    p.add_argument("rev", nargs="?", help="version to restore from")
    p.add_argument("file", nargs="?", help="file to restore")
    p.add_argument("--tag", help="restore this tagged baseline across the tracked set")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("snapshot", help="take a manual snapshot now")
    _add_common(p)
    p.add_argument("-m", "--message", help="optional snapshot message")
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("tag", help="name a baseline (or list/delete tags)")
    _add_common(p)
    p.add_argument("name", nargs="?", help="tag name to create")
    p.add_argument("rev", nargs="?", help="version to tag (default HEAD)")
    p.add_argument("-m", "--message")
    p.add_argument("--list", action="store_true")
    p.add_argument("--delete", metavar="NAME")
    p.set_defaults(func=cmd_tag)

    p = sub.add_parser("export", help="export a snapshot as a portable zip")
    _add_common(p)
    p.add_argument("rev")
    p.add_argument("-o", "--out", required=True, help="output .zip path")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("decode", help="decode controls.cfg to JSON")
    _add_common(p)
    p.add_argument("cfg", help="controls.cfg path")
    p.add_argument("-o", "--out", help="output JSON path (default stdout)")
    p.add_argument("--textconv", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_decode)

    p = sub.add_parser("encode",
                       help="patch keyboard bindings into a base controls.cfg")
    _add_common(p)
    p.add_argument("--base", required=True, help="base controls.cfg")
    p.add_argument("--bindings", required=True, help="keyboard bindings JSON")
    p.add_argument("-o", "--out", help="output path for the patched file")
    p.add_argument("--install", action="store_true",
                   help="backup the live controls.cfg and install the result "
                        "(refused while the sim runs)")
    p.set_defaults(func=cmd_encode)

    p = sub.add_parser("remap",
                       help="repoint bindings from an old device GUID to a new one "
                            "(fix a wheel/pedals after a USB-port change or PC swap)")
    _add_common(p)
    p.add_argument("--base", help="controls.cfg to patch (default: live file)")
    p.add_argument("--from", dest="src", metavar="OLD_GUID", help="old device instance GUID")
    p.add_argument("--to", dest="dst", metavar="NEW_GUID", help="new device instance GUID")
    p.add_argument("--auto", action="store_true",
                   help="auto-detect a single drifted device and remap it")
    p.add_argument("-o", "--out", help="output path for the patched file")
    p.add_argument("--install", action="store_true",
                   help="back up and install into the live folder (refused while the sim runs)")
    p.set_defaults(func=cmd_remap)

    p = sub.add_parser("whatis", help='reverse lookup: what is an input bound to (e.g. "Alt+P", "Btn 5")')
    _add_common(p)
    p.add_argument("input", help='the key/button/axis to identify, e.g. "Alt+P", "Btn 5", "Axis 3"')
    p.add_argument("--base", help="controls.cfg to inspect (default: live file)")
    p.set_defaults(func=cmd_whatis)

    p = sub.add_parser("devices", help="list connected controllers and referenced devices")
    _add_common(p)
    p.add_argument("--base", help="controls.cfg to inspect (default: live file)")
    p.set_defaults(func=cmd_devices)

    p = sub.add_parser("doctor", help="health check: confirm backups, watcher, decoder, and deps are OK")
    _add_common(p)
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("gui", help="open the friendly desktop app (native window or browser)")
    _add_common(p)
    p.set_defaults(func=cmd_gui)

    p = sub.add_parser("watcher", help="run or control the background watcher")
    _add_common(p)
    p.add_argument("action", choices=["run", "status", "pause", "resume", "stop",
                                      "install", "uninstall"])
    p.add_argument("--quiet", action="store_true", help="no console log output (run)")
    p.set_defaults(func=cmd_watcher)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except GitError as exc:
        return _fail(str(exc))
    except KeyboardInterrupt:
        return 130


def gfcc_main(argv: list[str] | None = None) -> int:
    """`gfcc` alias: codec commands only (requirements section 7)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    allowed = {"decode", "encode", "devices", "remap", "whatis"}
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: gfcc {decode,encode,devices,remap,whatis} ...\n"
              "       gfcc decode controls.cfg -o controls.json\n"
              "       gfcc encode --base controls.cfg --bindings my_binds.json -o controls.new.cfg\n"
              "       gfcc encode --base controls.cfg --bindings my_binds.json --install\n"
              "       gfcc remap --auto --install            # fix a wheel after a USB-port change\n"
              "       gfcc remap --from OLD_GUID --to NEW_GUID -o controls.new.cfg")
        return 0
    if argv[0] not in allowed:
        print(f"error: gfcc supports {sorted(allowed)}; use `irtrack` for everything else",
              file=sys.stderr)
        return 2
    return main(argv)


if __name__ == "__main__":
    sys.exit(main())
