"""Optional system-tray presence for the GUI (Windows).

Strictly best-effort: any failure to set up the tray must NOT break the app
window. `start_tray` returns None when pystray/Pillow aren't available or the
icon can't be created, and the caller simply runs without a tray. The tray
runs its own message loop on a daemon thread; menu callbacks fire on that
thread, so they only call thread-safe window operations.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

log = logging.getLogger(__name__)


def start_tray(icon_path: str, *, on_open: Callable[[], None],
               on_backup: Callable[[], None], on_quit: Callable[[], None],
               tooltip: str = "iRacing Config Tracker"):
    """Create a tray icon on a daemon thread. Returns the pystray Icon (which
    has .stop() and .notify()), or None if a tray can't be created."""
    try:
        import pystray
        from PIL import Image
    except Exception as exc:  # pystray/Pillow not bundled, or import error
        log.info("system tray unavailable (%s)", exc)
        return None
    try:
        image = Image.open(icon_path)
    except Exception as exc:
        log.info("tray icon image unavailable (%s)", exc)
        return None

    def _wrap(fn):
        def handler(icon, item):  # pystray passes (icon, item)
            try:
                fn()
            except Exception:
                log.exception("tray action failed")
        return handler

    menu = pystray.Menu(
        pystray.MenuItem("Open", _wrap(on_open), default=True),
        pystray.MenuItem("Back up now", _wrap(on_backup)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", _wrap(on_quit)),
    )
    try:
        icon = pystray.Icon("irtracker", image, tooltip, menu)
        threading.Thread(target=icon.run, daemon=True, name="tray").start()
        return icon
    except Exception as exc:
        log.warning("could not start system tray (%s)", exc)
        return None
