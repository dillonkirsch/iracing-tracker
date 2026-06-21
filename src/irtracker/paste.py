"""Share recipe text via a public link.

Two trusted providers, tried in order:
  1. pastebin.com  — needs the user's free API key (api_dev_key).
  2. PrivateBin     — end-to-end encrypted; the decryption key lives in the URL
                      fragment, so the server never sees the plaintext. Hand-rolled
                      v2 protocol (AES-256-GCM + PBKDF2) using `cryptography`.

Uploading PUBLISHES the text, so the GUI confirms first. On total failure
`share` raises with each provider's error so the message is debuggable.
"""
from __future__ import annotations

import base64
import json
import urllib.parse
import urllib.request
import zlib

_UA = "iRacingConfigTracker/1.0 (+https://github.com/dillonkirsch/iracing-tracker)"
_TIMEOUT = 15
DEFAULT_PRIVATEBIN = "https://privatebin.net"

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = ""
    while n > 0:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\x00"))) + out


def _b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + _B58.index(ch)
    full = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return b"\x00" * (len(s) - len(s.lstrip("1"))) + full


# -- pastebin.com --------------------------------------------------------------

def _pastebin(text: str, api_key: str) -> str:
    data = urllib.parse.urlencode({
        "api_dev_key": api_key, "api_option": "paste",
        "api_paste_code": text, "api_paste_private": "1",   # unlisted
        "api_paste_expire_date": "1M", "api_paste_format": "text",
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://pastebin.com/api/api_post.php", data=data,
        headers={"User-Agent": _UA, "Content-Type": "application/x-www-form-urlencoded"},
        method="POST")
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        out = r.read().decode("utf-8", "replace").strip()
    if not out.startswith("http"):  # pastebin returns "Bad API request, ..." on errors
        raise RuntimeError(out[:120])
    return out


# -- PrivateBin (encrypted) ----------------------------------------------------

def _derive(passphrase: bytes, salt: bytes, iterations: int) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                      iterations=iterations).derive(passphrase)


def _privatebin_send(text: str, instance: str) -> str:
    import os
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    passphrase, salt, iv, iters = os.urandom(32), os.urandom(8), os.urandom(16), 100000
    adata = [[base64.b64encode(iv).decode(), base64.b64encode(salt).decode(),
              iters, 256, 128, "aes", "gcm", "zlib"], "plaintext", 0, 0]
    aad = json.dumps(adata, separators=(",", ":")).encode("utf-8")
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    compressed = co.compress(text.encode("utf-8")) + co.flush()
    ct = AESGCM(_derive(passphrase, salt, iters)).encrypt(iv, compressed, aad)
    payload = {"v": 2, "adata": adata, "ct": base64.b64encode(ct).decode(),
               "meta": {"expire": "1month"}}
    base = instance.rstrip("/") + "/"
    req = urllib.request.Request(
        base, data=json.dumps(payload).encode("utf-8"),
        headers={"User-Agent": _UA, "Content-Type": "application/json",
                 "X-Requested-With": "JSONHttpRequest"}, method="POST")
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        resp = json.loads(r.read().decode("utf-8", "replace"))
    if str(resp.get("status", "0")) != "0" or "id" not in resp:
        raise RuntimeError(resp.get("message", "rejected by the server"))
    return f"{instance.rstrip('/')}/?{resp['id']}#{_b58encode(passphrase)}"


def _privatebin_fetch(url: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    u = urllib.parse.urlparse(url)
    passphrase = _b58decode(u.fragment)
    req = urllib.request.Request(
        f"{u.scheme}://{u.netloc}/?{u.query}",
        headers={"User-Agent": _UA, "X-Requested-With": "JSONHttpRequest"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    if "ct" not in data or "adata" not in data:
        raise RuntimeError(data.get("message", "paste not found or expired"))
    adata = data["adata"]
    iv = base64.b64decode(adata[0][0])
    salt = base64.b64decode(adata[0][1])
    iters = int(adata[0][2])
    aad = json.dumps(adata, separators=(",", ":")).encode("utf-8")
    pt = AESGCM(_derive(passphrase, salt, iters)).decrypt(
        iv, base64.b64decode(data["ct"]), aad)
    if adata[0][7] == "zlib":
        pt = zlib.decompressobj(-15).decompress(pt)
    return pt.decode("utf-8", "replace")


# -- public API ----------------------------------------------------------------

def share(text: str, pastebin_key: str | None = None,
          privatebin_instance: str | None = None) -> str:
    """Upload text; return the first working public URL, or raise with details."""
    errors = []
    if pastebin_key:
        try:
            return _pastebin(text or "", pastebin_key)
        except Exception as exc:
            errors.append(f"pastebin.com ({type(exc).__name__}: {str(exc)[:80]})")
    try:
        return _privatebin_send(text or "", privatebin_instance or DEFAULT_PRIVATEBIN)
    except Exception as exc:
        errors.append(f"PrivateBin ({type(exc).__name__}: {str(exc)[:80]})")
    raise RuntimeError("couldn't reach a share service — tried " + "; ".join(errors))


def fetch(url: str) -> str:
    """Download the text behind a share link (pastebin.com, PrivateBin, or raw)."""
    u = urllib.parse.urlparse(str(url))
    if u.scheme not in ("http", "https"):
        raise ValueError("that doesn't look like a link")
    if u.fragment and u.query:                      # PrivateBin: ?id#key
        return _privatebin_fetch(url)
    target = url
    if u.netloc.endswith("pastebin.com") and not u.path.startswith("/raw/"):
        target = f"{u.scheme}://{u.netloc}/raw{u.path}"
    req = urllib.request.Request(target, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")
