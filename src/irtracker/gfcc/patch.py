"""Keyboard-binding patch mode (FR-22).

Inputs: a decoded base controls.cfg and a bindings JSON document:

    {
      "version": 1,
      "bindings": [
        { "action": "PitSpeedLimiter", "key": "p", "modifiers": ["alt"] },
        { "action": "ReplayPlayPause", "key": "space" }
      ]
    }

The action vocabulary is exactly the entry names the decoder emits. Patching
preserves every non-keyboard binding byte-for-byte: only entries named in the
bindings file are touched, and an action currently bound to an axis or button
is refused rather than overwritten (protects wheel/pedal binds; full device
generation is v2).

Per-entry patch semantics (informed by observed files, where keyboard binds
never carry device GUID slots and unk0/flags appear tied to the action, not
the bind type): preserve unk0 and flags, set type=key/value/modifiers, clear
the GUID slots.
"""
from __future__ import annotations

import difflib
import json
from typing import Any

from irtracker.gfcc.codec import GfccError
from irtracker.gfcc.keymap import VK_NAMES, mods_mask, mods_note, vk_for_name

BINDINGS_VERSION = 1


class BindingsError(GfccError):
    """Bindings JSON is invalid or conflicts with the base file."""


def load_bindings(text: str) -> list[dict[str, Any]]:
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BindingsError(f"bindings file is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("version") != BINDINGS_VERSION:
        raise BindingsError(f'bindings file must have "version": {BINDINGS_VERSION}')
    bindings = doc.get("bindings")
    if not isinstance(bindings, list) or not bindings:
        raise BindingsError('bindings file must have a non-empty "bindings" list')
    for i, b in enumerate(bindings):
        if not isinstance(b, dict) or "action" not in b or "key" not in b:
            raise BindingsError(f'binding #{i + 1} must have "action" and "key"')
        mods = b.get("modifiers", [])
        if not isinstance(mods, list) or not all(isinstance(m, str) for m in mods):
            raise BindingsError(f'binding #{i + 1}: "modifiers" must be a list of strings')
    return bindings


def apply_bindings(doc: dict[str, Any], bindings: list[dict[str, Any]]) -> list[str]:
    """Patch keyboard bindings into a decoded controls.cfg dict, in place.

    Returns one human-readable change line per binding. Raises BindingsError on
    unknown actions/keys or attempts to overwrite axis/button binds.
    """
    entries = doc["controls"]["entries"]
    by_name = {e["name"]: e for e in entries}
    by_lower = {e["name"].lower(): e for e in entries}
    changes: list[str] = []

    for b in bindings:
        action = str(b["action"])
        entry = by_name.get(action) or by_lower.get(action.lower())
        if entry is None:
            hint = difflib.get_close_matches(action, by_name.keys(), n=3)
            suffix = f" (did you mean: {', '.join(hint)}?)" if hint else ""
            raise BindingsError(f"unknown action {action!r}{suffix}; "
                                f"run decode on the base file for the full vocabulary")

        if entry.get("type") in ("axis", "button"):
            raise BindingsError(
                f"action {entry['name']!r} is currently bound to a {entry['type']}; "
                f"refusing to overwrite a non-keyboard binding (v1 patches keyboard binds only)")

        vk = vk_for_name(str(b["key"]))
        if vk is None:
            raise BindingsError(f"unknown key {b['key']!r} for action {entry['name']!r}")
        mask = mods_mask(b.get("modifiers", []))

        old = entry.get("_key") or entry.get("type", "unbound")
        entry["type"] = "key"
        entry["value"] = vk
        for stale in ("modifiers", "_mods", "_key", "_button", "slot0", "slot1", "slot2"):
            entry.pop(stale, None)
        if mask:
            entry["modifiers"] = f"{mask:#x}"
            entry["_mods"] = mods_note(mask)
        entry["_key"] = (entry.get("_mods", "") + "+" if mask else "") + VK_NAMES[vk]
        changes.append(f"{entry['name']}: {old} -> {entry['_key']}")

    return changes
