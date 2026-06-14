"""Binding analysis: surface duplicate / conflicting control assignments that
iRacing itself won't warn you about (two actions on the same button or key).

Axes are deliberately excluded: sharing one axis across actions is normal
(steering left/right on a single wheel axis, combined throttle/brake pedals), so
flagging them would just be noise. Conflicts are reported for buttons and keys,
where a shared input genuinely fires two actions at once.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from irtracker.gfcc.codec import guid_from_str
from irtracker.gfcc.keymap import VK_NAMES, mods_note

_EXTRA_SLOT = "00000001-0000-0000-0000-000000000000"


@dataclass
class Conflict:
    kind: str                                  # "button" | "key"
    label: str                                 # human description of the shared input
    actions: list[str] = field(default_factory=list)


def _instance_guid(entry: dict[str, Any]) -> str:
    """The device instance GUID an entry is bound to ('' for keyboard).

    A slot holds either an instance GUID, a product GUID (trailing 'PIDVID'
    marker), or the extra-slot marker; only the instance identifies the device.
    """
    for i in range(3):
        g = entry.get(f"slot{i}")
        if not g or g.upper() == _EXTRA_SLOT:
            continue
        try:
            if guid_from_str(g)[10:] == b"PIDVID":  # product GUID, skip
                continue
        except Exception:
            continue
        return g.upper()
    return ""


def _bits(value: int) -> list[int]:
    out, i = [], 0
    while value:
        if value & 1:
            out.append(i)
        value >>= 1
        i += 1
    return out


def _norm_mods(value: Any) -> int:
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            return 0
    return int(value or 0)


def _signature(entry: dict[str, Any]):
    """A hashable key for the physical input an entry uses, or None if the
    entry is an axis or unbound (not a conflict candidate)."""
    kind = entry.get("type")
    value = entry.get("value", 0)
    if kind == "button" and value:
        return ("button", _instance_guid(entry), value)
    if kind == "key" and value:
        return ("key", _norm_mods(entry.get("modifiers")), value)
    return None


def _button_label(value: int) -> str:
    return "+".join(f"Btn {b}" for b in _bits(value)) or f"value {value}"


def _key_label(mods: int, value: int) -> str:
    name = VK_NAMES.get(value, f"key {value}")
    note = mods_note(mods) if mods else ""
    return f"{note}+{name}" if note else name


def _context(name: str) -> str:
    """iRacing input context an action belongs to. The sim reuses the same key
    across contexts on purpose (e.g. 'A' pans the camera in the replay tool AND
    triggers the pit limiter while driving), so a key shared across contexts is
    not a real conflict -- only a collision *within* one context is."""
    if name.startswith("DCam"):
        return "dashcam"
    if name.startswith("Cam"):
        return "camera"
    if name.startswith("Replay"):
        return "replay"
    return "drive"


def find_binding_conflicts(doc: dict[str, Any]) -> list[Conflict]:
    """Return inputs assigned to more than one action.

    Buttons: any two actions on the same physical button (same device + value).
    Keys: two actions on the same key+modifiers *within the same input context*
    (cross-context key reuse is intentional in iRacing and is ignored).
    """
    button_groups: dict[tuple, list[str]] = {}
    key_groups: dict[tuple, list[str]] = {}
    for entry in doc["controls"]["entries"]:
        sig = _signature(entry)
        if not sig:
            continue
        if sig[0] == "button":
            button_groups.setdefault((sig[1], sig[2]), []).append(entry["name"])
        else:
            key_groups.setdefault((sig[1], sig[2]), []).append(entry["name"])

    conflicts: list[Conflict] = []
    for (_inst, value), names in button_groups.items():
        if len(names) > 1:
            conflicts.append(Conflict("button", _button_label(value), sorted(names)))
    for (mods, value), names in key_groups.items():
        by_context: dict[str, list[str]] = {}
        for name in names:
            by_context.setdefault(_context(name), []).append(name)
        for group in by_context.values():
            if len(group) > 1:
                conflicts.append(Conflict("key", _key_label(mods, value), sorted(group)))

    conflicts.sort(key=lambda c: (c.kind, c.label))
    return conflicts
