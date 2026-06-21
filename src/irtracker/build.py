"""Detect the installed iRacing build version (Windows).

iRacing keeps the live build string in ``<install>\\version_system.txt`` (it's
rewritten on every auto-patch); the install location comes from the uninstall
registry. Everything is wrapped so a lookup failure just returns None and the
caller degrades gracefully (no build stamp on the snapshot).
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

_UNSET = "__unset__"
_location_cache: str | None = _UNSET  # type: ignore[assignment]


def _reg_val(key, name):
    import winreg
    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def _iracing_uninstall() -> dict:
    """{'version': ..., 'location': ...} from the iRacing uninstall entry."""
    import winreg
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for root, path in roots:
        try:
            with winreg.OpenKey(root, path) as k:
                for i in range(winreg.QueryInfoKey(k)[0]):
                    try:
                        with winreg.OpenKey(k, winreg.EnumKey(k, i)) as sk:
                            name = _reg_val(sk, "DisplayName")
                            if name and "iRacing.com Race Simulation" in name:
                                return {"version": _reg_val(sk, "DisplayVersion"),
                                        "location": _reg_val(sk, "InstallLocation")}
                    except OSError:
                        continue
        except OSError:
            continue
    return {}


def _install_location() -> str | None:
    global _location_cache
    if _location_cache == _UNSET:
        _location_cache = _iracing_uninstall().get("location") or None
    return _location_cache


def current_build() -> str | None:
    """The live iRacing build (e.g. '2026.06.12.02'), or None if not found."""
    try:
        loc = _install_location()
        if loc:
            try:
                lines = (Path(loc) / "version_system.txt").read_text(
                    encoding="utf-8", errors="replace").strip().splitlines()
                if lines and lines[0].strip():
                    return lines[0].strip()
            except OSError:
                pass
        return _iracing_uninstall().get("version") or None  # fallback
    except Exception as exc:  # never raise to the caller
        log.debug("iRacing build lookup failed: %s", exc)
        return None
