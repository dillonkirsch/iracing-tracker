"""Logon autostart for the watcher (FR-28).

Primary mechanism is a Windows scheduled task (schtasks /SC ONLOGON). Some
Windows configurations refuse ONLOGON task creation without elevation, so a
HKCU Run registry entry is the no-admin fallback. Both run the watcher
headless via pythonw.exe.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import winreg

log = logging.getLogger(__name__)

TASK_NAME = "iRacing Config Tracker Watcher"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "iRacingConfigTrackerWatcher"


def _watcher_command() -> str:
    if getattr(sys, "frozen", False):
        # the packaged .exe routes CLI args to the CLI (see launcher.py)
        return f'"{sys.executable}" watcher run --quiet'
    import os
    exe = sys.executable
    pythonw = exe.replace("python.exe", "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = exe
    return f'"{pythonw}" -m irtracker watcher run --quiet'


def install() -> str:
    """Install logon autostart; returns a description of what was installed."""
    cmd = _watcher_command()
    proc = subprocess.run(
        ["schtasks", "/Create", "/F", "/SC", "ONLOGON", "/TN", TASK_NAME, "/TR", cmd],
        capture_output=True, text=True)
    if proc.returncode == 0:
        log.info("installed scheduled task %r", TASK_NAME)
        return f'scheduled task "{TASK_NAME}" (runs at logon)'
    log.warning("schtasks failed (%s); falling back to HKCU Run key",
                proc.stderr.strip() or proc.stdout.strip())
    key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
    with key:
        winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, cmd)
    return f"HKCU Run entry {RUN_VALUE!r} (runs at logon; schtasks was denied)"


def uninstall() -> list[str]:
    removed = []
    proc = subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME],
                          capture_output=True, text=True)
    if proc.returncode == 0:
        removed.append(f'scheduled task "{TASK_NAME}"')
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
        with key:
            winreg.DeleteValue(key, RUN_VALUE)
        removed.append(f"HKCU Run entry {RUN_VALUE!r}")
    except FileNotFoundError:
        pass
    return removed


def installed_status() -> list[str]:
    found = []
    proc = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME],
                          capture_output=True, text=True)
    if proc.returncode == 0:
        found.append(f'scheduled task "{TASK_NAME}"')
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY)
        with key:
            winreg.QueryValueEx(key, RUN_VALUE)
        found.append(f"HKCU Run entry {RUN_VALUE!r}")
    except FileNotFoundError:
        pass
    return found
