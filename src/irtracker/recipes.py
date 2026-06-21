"""Config "recipes": a portable, shareable subset of INI settings.

A recipe captures chosen ``[Section] key = value`` pairs from one settings file
so they can be shared (text / link) and applied to another setup. Applying is
line-surgical -- it replaces only the target key lines and preserves the rest
of the file (comments, ordering, untouched keys) -- so it never re-serializes
the INI. Controls (device GUIDs) are intentionally out of scope.
"""
from __future__ import annotations

import json

KIND = "irtracker-recipe"


def build_recipe(name: str, file: str, parsed: dict, sections: list[str]) -> dict:
    """Build a recipe from already-parsed INI ({section: {key: value}})."""
    values = []
    for sec in sections:
        for key, val in parsed.get(sec, {}).items():
            values.append({"section": sec, "key": key, "value": val})
    return {"kind": KIND, "v": 1, "name": (name or file).strip() or file,
            "file": file, "values": values}


def recipe_json(recipe: dict) -> str:
    return json.dumps(recipe, ensure_ascii=False, indent=2)


def parse_recipe(text: str) -> dict:
    try:
        d = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("This doesn't look like a recipe (not valid recipe text).") from exc
    if not isinstance(d, dict) or d.get("kind") != KIND:
        raise ValueError("This isn't an iRacing Config Tracker recipe.")
    if not d.get("file") or not isinstance(d.get("values"), list):
        raise ValueError("This recipe is missing its file or values.")
    return d


def recipe_changes(recipe: dict, current_parsed: dict) -> list[dict]:
    """What a recipe would change vs the current file: [{section,key,old,new}]."""
    out = []
    for item in recipe.get("values", []):
        sec = str(item.get("section", ""))
        key = str(item.get("key", ""))
        new = str(item.get("value", ""))
        if not key:
            continue
        old = current_parsed.get(sec, {}).get(key)
        if old != new:
            out.append({"section": sec, "key": key, "old": old, "new": new})
    return out


def _replace_value(line: str, new_value: str) -> str:
    """Replace the value in a 'key=value\\t; comment' line, keeping the key and
    any inline comment."""
    nl = "\n" if line.endswith("\n") else ""
    body = line.rstrip("\n")
    key, _, rest = body.partition("=")
    ci = rest.find("\t;")  # iRacing inline comments are tab + ';'
    comment = rest[ci:] if ci != -1 else ""
    return f"{key}={new_value}{comment}{nl}"


def patch_ini_text(text: str, changes: dict) -> str:
    """Apply {(section, key): value} to INI text by line surgery, preserving
    everything else. Keys missing from an existing section are appended to it;
    missing sections are appended as new blocks."""
    remaining = dict(changes)
    out: list[str] = []
    section = ""

    def flush(sec: str) -> None:
        for (s, k) in [pair for pair in remaining if pair[0] == sec]:
            out.append(f"{k}={remaining.pop((s, k))}\n")

    for raw in text.splitlines(keepends=True):
        stripped = raw.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            flush(section)               # add leftover keys to the section we're leaving
            section = stripped[1:-1]
            out.append(raw)
            continue
        if "=" in stripped and not stripped.startswith((";", "#")):
            key = stripped.split("=", 1)[0].strip()
            if (section, key) in remaining:
                out.append(_replace_value(raw, str(remaining.pop((section, key)))))
                continue
        out.append(raw)
    flush(section)

    by_sec: dict[str, list] = {}
    for (s, k), v in remaining.items():
        by_sec.setdefault(s, []).append((k, v))
    for s, kvs in by_sec.items():
        out.append(f"\n[{s}]\n")
        for k, v in kvs:
            out.append(f"{k}={v}\n")
    return "".join(out)
