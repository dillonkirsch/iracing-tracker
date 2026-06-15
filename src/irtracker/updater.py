"""Self-update: check GitHub Releases for a newer build and, when running as the
packaged .exe, download + checksum-verify it and swap it in.

The swap uses the standard Windows trick: a running .exe can't overwrite itself,
so we launch a tiny detached helper that waits for this process to exit, replaces
the .exe in place, relaunches it, and deletes itself.
"""
from __future__ import annotations

import hashlib
import json
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


def _dir_writable(path: Path) -> bool:
    try:
        probe = path / ".ict-write-test.tmp"
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def needs_admin() -> bool:
    """True when the running .exe lives somewhere we can't overwrite without
    elevation (e.g. Program Files)."""
    return is_frozen() and not _dir_writable(Path(sys.executable).parent)


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
        "needsAdmin": needs_admin(),
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
    # Stable staging dir so the log is easy to find if something goes wrong.
    stage = Path(tempfile.gettempdir()) / "iracing-config-tracker-update"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True, exist_ok=True)
    new_exe = stage / EXE_NAME
    log = stage / "update-log.txt"
    try:
        _download(exe_url, new_exe)
        if sha_url:
            sha_file = stage / "sha256.txt"
            _download(sha_url, sha_file)
            want = sha_file.read_text(encoding="utf-8", errors="replace").split()[0].lower()
            got = hashlib.sha256(new_exe.read_bytes()).hexdigest().lower()
            if want != got:
                return {"ok": False, "error": "The downloaded update failed its "
                        "checksum check; not installing it."}
    except Exception as exc:
        return {"ok": False, "error": f"Download failed: {exc}"}

    protected = not _dir_writable(current.parent)
    # In a protected folder the helper runs elevated, so relaunch via explorer to
    # drop back to normal privileges (don't keep running the app as admin).
    relaunch = f'explorer.exe "{current}"' if protected else f'start "" "{current}"'
    # A running .exe stays locked, so move/Y fails until the app exits. Just
    # retry the swap until it succeeds — that naturally waits for the unlock.
    # (Avoid tasklist/find to detect exit: those console tools hang when the
    # helper runs with no console window.)
    bat = stage / "apply_update.bat"
    bat.write_text("\r\n".join([
        "@echo off",
        f'set "LOG={log}"',
        'echo [update] waiting for the app to close, then replacing it > "%LOG%"',
        "set /a tries=0",
        ":retry",
        f'move /Y "{new_exe}" "{current}" >> "%LOG%" 2>&1',
        f'if not exist "{new_exe}" goto done',
        "set /a tries+=1",
        "if %tries% geq 90 goto giveup",
        "ping -n 2 127.0.0.1 >NUL",
        "goto retry",
        ":done",
        'echo [update] replaced; relaunching >> "%LOG%"',
        relaunch,
        'del "%~f0"',
        "exit",
        ":giveup",
        'echo [update] ERROR: the app file is still in use; could not replace it >> "%LOG%"',
        relaunch,
        "exit",
        "",
    ]), encoding="utf-8")

    if protected:
        # ShellExecute "runas" pops UAC and returns once the user responds:
        # >32 = accepted/launched, <=32 = declined or failed.
        try:
            import ctypes
            rc = int(ctypes.windll.shell32.ShellExecuteW(
                None, "runas", "cmd.exe", f'/c "{bat}"', None, 0))
        except Exception as exc:
            return {"ok": False, "error": f"Couldn't request administrator permission: {exc}"}
        if rc <= 32:
            return {"ok": False, "needsAdmin": True,
                    "error": "This update needs administrator permission because the app "
                             "is in a protected folder, and that was declined."}
        return {"ok": True, "restarting": True, "elevated": True}

    # CREATE_NO_WINDOW (alone) gives a hidden console and survives our exit;
    # do NOT combine with DETACHED_PROCESS (that pair is contradictory).
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(["cmd", "/c", str(bat)], creationflags=flags, close_fds=True)
    return {"ok": True, "restarting": True}
