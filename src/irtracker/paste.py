"""Best-effort sharing of recipe text via a no-auth paste service.

Uploading PUBLISHES the text to a third party (anyone with the link can read
it), so callers must confirm with the user first. Failures raise so the GUI can
fall back to "copy the text yourself".
"""
from __future__ import annotations

import urllib.request

PASTE_BASE = "https://paste.rs/"
_UA = "iRacingConfigTracker"
_TIMEOUT = 15


def share(text: str) -> str:
    """Upload text; return the public URL."""
    req = urllib.request.Request(
        PASTE_BASE, data=(text or "").encode("utf-8"),
        headers={"User-Agent": _UA, "Content-Type": "text/plain; charset=utf-8"},
        method="POST")
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        url = r.read().decode("utf-8", "replace").strip()
    if not url.startswith("http"):
        raise RuntimeError("the paste service did not return a link")
    return url


def fetch(url: str) -> str:
    """Download the text behind a paste link."""
    if not str(url).lower().startswith(("http://", "https://")):
        raise ValueError("that doesn't look like a link")
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")
