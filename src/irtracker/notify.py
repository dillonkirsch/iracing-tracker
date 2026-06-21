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


# -- Discord webhook (opt-in; for streamers/leagues) ---------------------------

def _post_discord(url: str, payload: dict) -> None:
    import json
    import urllib.request
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "iRacingConfigTracker"},
        method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()


def _snapshot_payload(files, trigger_label: str, context: str, build: str | None) -> dict:
    names = ", ".join(sorted(files)) or "files"
    desc = f"**{trigger_label}**"
    if context and context not in ("", "manual edit"):
        desc += f" — {context}"
    fields = [{"name": "Files", "value": names[:1000] or "—", "inline": False}]
    if build:
        fields.append({"name": "iRacing build", "value": build, "inline": True})
    return {"username": "iRacing Config Tracker",
            "embeds": [{"title": "\U0001F4F8 Config backup saved", "description": desc,
                        "color": 0x3B82F6, "fields": fields}]}


def discord_snapshot(url: str, files, trigger_label: str, context: str,
                     build: str | None = None) -> None:
    """Fire-and-forget a Discord post about a snapshot (never blocks/raises)."""
    import threading
    payload = _snapshot_payload(files, trigger_label, context, build)

    def go():
        try:
            _post_discord(url, payload)
        except Exception as exc:
            log.warning("discord webhook failed: %s", exc)

    threading.Thread(target=go, daemon=True, name="discord").start()


def discord_test(url: str) -> None:
    """Post a test message synchronously; raises on failure so the GUI reports it."""
    _post_discord(url, {"username": "iRacing Config Tracker",
                        "content": "✅ Test from iRacing Config Tracker — "
                                   "your webhook works! Backups will post here."})
