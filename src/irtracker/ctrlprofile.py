"""Portable controls-profile bundles (export / import).

A bundle is a single self-describing JSON file holding one control profile's
files — controls.cfg (binary, base64) and joyCalib.yaml if present — plus a
small manifest (name, date, iRacing build, device list) so an import can show a
preview before touching anything. Controls reference device GUIDs that differ
per machine, so an imported profile may need the hardware re-map afterwards.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime

KIND = "irtracker-controls"


def build_bundle(name: str, files: dict[str, bytes], *,
                 build: str | None = None, devices: list[str] | None = None) -> str:
    """Serialize a profile's files (e.g. {'controls.cfg': b..., 'joyCalib.yaml': b...})."""
    return json.dumps({
        "kind": KIND, "v": 1, "name": name or "controls",
        "exportedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "build": build, "devices": devices or [],
        "files": {k: base64.b64encode(v).decode("ascii") for k, v in files.items()},
    }, indent=2)


def parse_bundle(text: str) -> dict:
    """Validate + decode a bundle; ``files`` come back as raw bytes."""
    try:
        d = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("This isn't a controls profile file (not valid JSON).") from exc
    if not isinstance(d, dict) or d.get("kind") != KIND:
        raise ValueError("This isn't an iRacing Config Tracker controls profile.")
    files = d.get("files")
    if not isinstance(files, dict) or "controls.cfg" not in files:
        raise ValueError("This profile is missing its controls.cfg.")
    decoded = {}
    for k, v in files.items():
        try:
            decoded[k] = base64.b64decode(v)
        except Exception as exc:
            raise ValueError(f"The {k} in this profile is corrupted.") from exc
    d["files"] = decoded
    return d
