"""Self-update: check GitHub Releases for a newer build and, when running as the
packaged .exe, download + checksum-verify it and swap it in.

The swap uses the standard Windows trick: a running .exe can't overwrite itself,
so we launch a tiny detached helper that waits for this process to exit, replaces
the .exe in place, relaunches it, and deletes itself.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO = "dillonkirsch/iracing-tracker"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
EXE_NAME = "iRacingConfigTracker.exe"
_UA = {"User-Agent": "irtracker-updater"}


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def current_version() -> str:
    """Version of this build (set at package time), or a best-effort fallback."""
    try:
        from irtracker import _buildinfo  # written by the build (gitignored)
        v = (getattr(_buildinfo, "VERSION", "") or "").strip()
        if v:
            return v
    except Exception:
        pass
    try:
        from importlib.metadata import version
        return "v" + version("iracing-config-tracker")
    except Exception:
        return "dev"


def _parts(v: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", v or ""))


def is_newer(latest: str, current: str) -> bool:
    lt, ct = _parts(latest), _parts(current)
    if not lt or not ct:
        return False
    n = max(len(lt), len(ct))
    return lt + (0,) * (n - len(lt)) > ct + (0,) * (n - len(ct))


def check_for_update(timeout: float = 8.0) -> dict:
    cur = current_version()
    try:
        req = urllib.request.Request(
            API_LATEST, headers={**_UA, "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "current": cur}
    tag = data.get("tag_name", "")
    exe_url = sha_url = None
    for a in data.get("assets", []):
        if a.get("name") == EXE_NAME:
            exe_url = a.get("browser_download_url")
        elif a.get("name") == EXE_NAME + ".sha256":
            sha_url = a.get("browser_download_url")
    return {
        "ok": True,
        "current": cur,
        "latest": tag,
        "updateAvailable": is_newer(tag, cur),
        "canApply": is_frozen() and bool(exe_url),
        "url": data.get("html_url"),
        "notes": (data.get("body") or "")[:1200],
        "exeUrl": exe_url,
        "shaUrl": sha_url,
    }


def _download(url: str, dest: Path, timeout: float = 180.0) -> None:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def apply_update(exe_url: str, sha_url: str | None = None) -> dict:
    """Download + verify the new .exe and launch the swap helper. Does NOT exit
    this process — the caller should quit shortly after so the helper can run."""
    if not is_frozen():
        return {"ok": False, "error": "Self-update only works in the packaged app. "
                "From source, use git to update."}
    current = Path(sys.executable)
    tmp = Path(tempfile.mkdtemp(prefix="ict-update-"))
    new_exe = tmp / EXE_NAME
    try:
        _download(exe_url, new_exe)
        if sha_url:
            sha_file = tmp / "sha256.txt"
            _download(sha_url, sha_file)
            want = sha_file.read_text(encoding="utf-8", errors="replace").split()[0].lower()
            got = hashlib.sha256(new_exe.read_bytes()).hexdigest().lower()
            if want != got:
                return {"ok": False, "error": "The downloaded update failed its "
                        "checksum check; not installing it."}
    except Exception as exc:
        return {"ok": False, "error": f"Download failed: {exc}"}

    pid = os.getpid()
    bat = tmp / "apply_update.bat"
    bat.write_text(
        "@echo off\r\n"
        ":wait\r\n"
        f'tasklist /FI "PID eq {pid}" 2>NUL | find "{pid}" >NUL\r\n'
        "if not errorlevel 1 (\r\n"
        "  ping -n 2 127.0.0.1 >NUL\r\n"
        "  goto wait\r\n"
        ")\r\n"
        f'move /Y "{new_exe}" "{current}" >NUL\r\n'
        f'start "" "{current}"\r\n'
        'del "%~f0"\r\n',
        encoding="utf-8")

    flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    subprocess.Popen(["cmd", "/c", str(bat)], creationflags=flags, close_fds=True)
    return {"ok": True, "restarting": True}
