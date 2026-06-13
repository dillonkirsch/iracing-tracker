"""Windows toast notifications on detected changes (FR-30).

winotify is optional; missing or failing toasts degrade to log lines, never
break a snapshot.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_disabled = False


def toast(title: str, body: str) -> None:
    global _disabled
    if _disabled:
        return
    try:
        from winotify import Notification

        n = Notification(
            app_id="iRacing Config Tracker",
            title=title,
            msg=body,
        )
        n.show()
    except ImportError:
        log.info("winotify not installed; toast suppressed: %s - %s", title, body)
        _disabled = True
    except Exception as exc:
        log.warning("toast failed: %s", exc)


def snapshot_toast(files: dict[str, str], trigger_label: str, context_label: str) -> None:
    names = ", ".join(sorted(n for n in files)) or "files"
    toast("Config change saved",
          f"{names} ({trigger_label}, {context_label})")
