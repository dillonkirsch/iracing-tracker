"""Binding analysis: surface duplicate / conflicting control assignments that
iRacing itself won't warn you about (two actions on the same button or key).

Axes are deliberately excluded: sharing one axis across actions is normal
(steering left/right on a single wheel axis, combined throttle/brake pedals), so
flagging them would just be noise. Conflicts are reported for buttons and keys,
where a shared input genuinely fires two actions at once.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from irtracker.gfcc.codec import GfccError, guid_from_str
from irtracker.gfcc.keymap import VK_NAMES, mods_mask, mods_note, vk_for_name

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


def binding_value(entry: dict[str, Any]) -> str:
    """Human display of the physical input an entry is bound to (no device):
    'Alt+F', 'Btn 5', 'Axis 3', or 'Not assigned'. Shared by the live Controls
    view and per-control blame so values compare consistently across history."""
    kind = entry.get("type", "unbound")
    value = entry.get("value", 0)
    if kind == "key":
        return entry.get("_key") or VK_NAMES.get(value, f"key {value}")
    if kind == "axis":
        return f"Axis {value}"
    if kind == "button":
        if entry.get("_button"):
            return entry["_button"]
        if value:
            return "+".join(f"Btn {b}" for b in _bits(value))
    return "Not assigned"


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


# -- reverse input lookup ("what is this bound to?") ----------------------------

def _device_label(entry: dict[str, Any], devices: dict[str, str]) -> str:
    if entry.get("type") == "key":
        return "Keyboard"
    for i in range(3):
        g = entry.get(f"slot{i}")
        if g and g in devices:
            note = devices[g]
            return note.split(" - ", 1)[1] if " - " in note else "Game controller"
    return "Game controller"


def find_input(doc: dict[str, Any], query: str) -> tuple[str, str, list[dict]]:
    """Reverse lookup: which action(s) is a given input bound to?

    Accepts a button ("Btn 5"), axis ("Axis 3"), or key combo ("Alt+P", "F6").
    Returns (friendly_label, kind, matches) where matches is a list of
    {"action", "device"} (empty if the input is free). Raises GfccError if the
    input can't be understood.
    """
    devices = doc.get("_devices", {})
    entries = doc["controls"]["entries"]
    q = (query or "").strip()
    if not q:
        raise GfccError('type a key (e.g. "Alt+P"), a button ("Btn 5"), or an axis ("Axis 3")')

    def out(es):
        return [{"action": e["name"], "device": _device_label(e, devices)} for e in es]

    m = re.fullmatch(r"(?:btn|button|b)\s*(\d+)", q, re.I)
    if m:
        bit = int(m.group(1))
        mask = 1 << bit
        hits = [e for e in entries if e.get("type") == "button" and (e.get("value", 0) & mask)]
        return f"Btn {bit}", "button", out(hits)

    m = re.fullmatch(r"axis\s*(\d+)", q, re.I)
    if m:
        idx = int(m.group(1))
        hits = [e for e in entries if e.get("type") == "axis" and e.get("value", 0) == idx]
        return f"Axis {idx}", "axis", out(hits)

    parts = [p for p in re.split(r"[+\s]+", q) if p]
    *mod_names, key_name = parts
    try:
        mask = mods_mask(mod_names)
    except ValueError as exc:
        raise GfccError(str(exc)) from exc
    vk = vk_for_name(key_name)
    if vk is None:
        raise GfccError(f"don't recognize the key {key_name!r}")
    note = mods_note(mask)
    label = (note + "+" if note else "") + VK_NAMES.get(vk, key_name)
    hits = [e for e in entries if e.get("type") == "key"
            and e.get("value", 0) == vk and _norm_mods(e.get("modifiers", 0)) == mask]
    return label, "key", out(hits)
