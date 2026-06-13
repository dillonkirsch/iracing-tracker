"""Semantic diffs for tracked config files (FR-9, FR-10).

INI parsing is a thin order-preserving parser (stdlib configparser is lossy on
ordering/case). Parsing exists only for diffing and ignore lists; the tool
never writes INI/YAML (FR-19). controls.cfg diffs render from decoded JSON,
never raw bytes (FR-10).
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Any

ADDED = "added"
REMOVED = "removed"
CHANGED = "changed"


@dataclass
class KeyChange:
    section: str
    key: str
    kind: str  # added | removed | changed
    old: str | None = None
    new: str | None = None

    def render(self) -> str:
        loc = f"[{self.section}] {self.key}" if self.section else self.key
        if self.kind == ADDED:
            return f"+ {loc} = {self.new}"
        if self.kind == REMOVED:
            return f"- {loc} (was {self.old})"
        return f"  {loc}: {self.old} -> {self.new}"


def parse_ini(text: str) -> dict[str, dict[str, str]]:
    """Order- and case-preserving INI parse: section -> key -> value.

    iRacing INI convention pads values with spaces then a tab before an inline
    ';' comment; the comment is stripped from the value. Keys before any
    section header go under ''.
    """
    sections: dict[str, dict[str, str]] = {}
    current = sections.setdefault("", {})
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith((";", "#")):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            name = stripped[1:-1]
            current = sections.setdefault(name, {})
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        # Strip iRacing inline comments: whitespace + tab + ';'.
        ci = value.find("\t;")
        if ci != -1:
            value = value[:ci]
        current[key.strip()] = value.strip()
    # Drop an empty pre-section block so it doesn't show as a section.
    if not sections.get(""):
        sections.pop("", None)
    return sections


def diff_ini(old_text: str, new_text: str) -> list[KeyChange]:
    old = parse_ini(old_text)
    new = parse_ini(new_text)
    changes: list[KeyChange] = []
    for section in list(old) + [s for s in new if s not in old]:
        okeys = old.get(section, {})
        nkeys = new.get(section, {})
        for key in list(okeys) + [k for k in nkeys if k not in okeys]:
            o, n = okeys.get(key), nkeys.get(key)
            if o is None and n is not None:
                changes.append(KeyChange(section, key, ADDED, new=n))
            elif o is not None and n is None:
                changes.append(KeyChange(section, key, REMOVED, old=o))
            elif o != n:
                changes.append(KeyChange(section, key, CHANGED, old=o, new=n))
    return changes


def matches_ignore(section: str, key: str, ignore_keys: list[str]) -> bool:
    """Ignore entries are 'Section/key' or 'Section/*', case-insensitive globs."""
    probe = f"{section}/{key}".lower()
    return any(fnmatch(probe, pat.lower()) for pat in ignore_keys)


def only_ignored_changes(changes: list[KeyChange], ignore_keys: list[str]) -> bool:
    if not changes or not ignore_keys:
        return False
    return all(matches_ignore(c.section, c.key, ignore_keys) for c in changes)


def _flatten(value: Any, prefix: str, out: dict[str, Any]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            _flatten(v, f"{prefix}.{k}" if prefix else str(k), out)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _flatten(v, f"{prefix}[{i}]", out)
    else:
        out[prefix] = value


def diff_yaml(old_text: str, new_text: str) -> list[KeyChange]:
    """Semantic diff of YAML documents on flattened paths."""
    import yaml

    flat_old: dict[str, Any] = {}
    flat_new: dict[str, Any] = {}
    _flatten(yaml.safe_load(old_text) if old_text.strip() else {}, "", flat_old)
    _flatten(yaml.safe_load(new_text) if new_text.strip() else {}, "", flat_new)
    changes: list[KeyChange] = []
    for path in list(flat_old) + [p for p in flat_new if p not in flat_old]:
        o = flat_old.get(path)
        n = flat_new.get(path)
        if path not in flat_new:
            changes.append(KeyChange("", path, REMOVED, old=repr(o)))
        elif path not in flat_old:
            changes.append(KeyChange("", path, ADDED, new=repr(n)))
        elif o != n:
            changes.append(KeyChange("", path, CHANGED, old=repr(o), new=repr(n)))
    return changes


def describe_binding(entry: dict[str, Any]) -> str:
    """One-line human description of a decoded controls entry's binding."""
    t = entry.get("type", "unbound")
    if t == "key":
        return f"key {entry.get('_key', entry.get('value'))}"
    if t == "button":
        return f"button {entry.get('_button', entry.get('value'))}"
    if t == "axis":
        return f"axis {entry.get('value')}"
    if t == "unbound":
        return "unbound"
    return f"type {t} value {entry.get('value')}"


def diff_controls(old_doc: dict[str, Any], new_doc: dict[str, Any]) -> list[str]:
    """Diff two decoded controls.cfg documents at the binding level (FR-10)."""
    lines: list[str] = []
    old_e = {e["name"]: e for e in old_doc["controls"]["entries"]}
    new_e = {e["name"]: e for e in new_doc["controls"]["entries"]}

    if old_doc.get("global_config_hex") != new_doc.get("global_config_hex"):
        lines.append("  global config blob changed (FFB/calibration area, undecoded)")
    for name in list(old_e) + [n for n in new_e if n not in old_e]:
        o, n = old_e.get(name), new_e.get(name)
        if n is None:
            lines.append(f"- {name} (was {describe_binding(o)})")
            continue
        if o is None:
            lines.append(f"+ {name} = {describe_binding(n)}")
            continue
        od, nd = describe_binding(o), describe_binding(n)
        dev_keys = ("slot0", "slot1", "slot2")
        if od != nd:
            lines.append(f"  {name}: {od} -> {nd}")
        elif any(o.get(k) != n.get(k) for k in dev_keys):
            lines.append(f"  {name}: device GUIDs changed")
        elif any(o.get(k) != n.get(k) for k in ("unk0", "flags")):
            lines.append(f"  {name}: metadata changed "
                         f"(flags {o.get('flags')} -> {n.get('flags')}, "
                         f"unk0 {o.get('unk0', 0)} -> {n.get('unk0', 0)})")
    return lines


def raw_diff(old_text: str, new_text: str, old_label: str, new_label: str) -> str:
    return "".join(difflib.unified_diff(
        old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
        fromfile=old_label, tofile=new_label))


def render_changes(changes: list[KeyChange]) -> str:
    """Group key changes by section, e.g. '[Force Feedback]' then per-key lines."""
    out: list[str] = []
    by_section: dict[str, list[KeyChange]] = {}
    for c in changes:
        by_section.setdefault(c.section, []).append(c)
    for section, items in by_section.items():
        if section:
            out.append(f"[{section}]")
        for c in items:
            loc = f"  {c.key}"
            if c.kind == ADDED:
                out.append(f"{loc} = {c.new}  (added)")
            elif c.kind == REMOVED:
                out.append(f"{loc}  (removed, was {c.old})")
            else:
                out.append(f"{loc}: {c.old} -> {c.new}")
    return "\n".join(out)
