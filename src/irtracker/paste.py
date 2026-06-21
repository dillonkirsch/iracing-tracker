"""Best-effort sharing of recipe text via no-auth paste services.

Uploading PUBLISHES the text to a third party (anyone with the link can read
it), so callers must confirm with the user first. `share` tries several
providers in order and returns the first working link; if all fail it raises
with each provider's error so the GUI can report it and fall back to "copy the
text yourself".
"""
from __future__ import annotations

import socket
import urllib.request

_UA = "iRacingConfigTracker/1.0 (+https://github.com/dillonkirsch/iracing-tracker)"
_TIMEOUT = 12


def _http_post(url: str, data: bytes, headers: dict) -> str:
    req = urllib.request.Request(
        url, data=data, headers={"User-Agent": _UA, **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        out = r.read().decode("utf-8", "replace").strip()
    if not out.startswith("http"):
        raise RuntimeError(f"unexpected response: {out[:80]!r}")
    return out


def _multipart(url: str, text: str) -> str:
    b = "----irtboundary"
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"recipe.txt\"\r\nContent-Type: text/plain\r\n\r\n"
            f"{text}\r\n--{b}--\r\n").encode("utf-8")
    return _http_post(url, body, {"Content-Type": f"multipart/form-data; boundary={b}"})


def _share_0x0(text: str) -> str:
    return _multipart("https://0x0.st", text)


def _share_ttm(text: str) -> str:
    return _multipart("https://ttm.sh", text)


def _share_pasters(text: str) -> str:
    return _http_post("https://paste.rs/", text.encode("utf-8"), {"Content-Type": "text/plain"})


def _share_termbin(text: str) -> str:
    """termbin is a raw-TCP paste (port 9999) — dodges HTTP/Cloudflare blocks."""
    with socket.create_connection(("termbin.com", 9999), timeout=_TIMEOUT) as s:
        s.sendall(text.encode("utf-8"))
        s.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            part = s.recv(4096)
            if not part:
                break
            chunks.append(part)
    out = b"".join(chunks).decode("utf-8", "replace").strip().strip("\x00")
    if not out.startswith("http"):
        raise RuntimeError(f"unexpected response: {out[:80]!r}")
    return out


_PROVIDERS = [
    ("0x0.st", _share_0x0),
    ("termbin.com", _share_termbin),
    ("ttm.sh", _share_ttm),
    ("paste.rs", _share_pasters),
]


def share(text: str) -> str:
    """Upload text; return the first working public URL, or raise with details."""
    errors = []
    for name, fn in _PROVIDERS:
        try:
            return fn(text or "")
        except Exception as exc:
            errors.append(f"{name} ({type(exc).__name__})")
    raise RuntimeError("none of the share services were reachable — tried "
                       + ", ".join(errors))


def fetch(url: str) -> str:
    """Download the text behind a paste link."""
    if not str(url).lower().startswith(("http://", "https://")):
        raise ValueError("that doesn't look like a link")
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")
