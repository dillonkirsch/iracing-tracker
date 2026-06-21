import json
import os
import urllib.request

from irtracker import paste


def test_base58_roundtrip():
    for _ in range(5):
        b = os.urandom(32)
        assert paste._b58decode(paste._b58encode(b)) == b
    assert paste._b58decode(paste._b58encode(b"\x00\x00abc")) == b"\x00\x00abc"  # leading zeros


class _FakeResp:
    def __init__(self, body): self._b = body
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_privatebin_send_fetch_roundtrip(monkeypatch):
    """Encrypt+POST then GET+decrypt, with the network mocked: proves our own
    PrivateBin send/fetch are end-to-end consistent (AES-GCM + adata + deflate)."""
    stored = {}

    def fake_urlopen(req, timeout=None):
        if req.get_method() == "POST":
            body = json.loads(req.data.decode("utf-8"))
            assert body["v"] == 2 and "ct" in body and "adata" in body  # PrivateBin v2 shape
            stored["paste"] = body
            return _FakeResp(json.dumps({"status": 0, "id": "abc123", "url": "/?abc123"}).encode())
        return _FakeResp(json.dumps(stored["paste"]).encode())  # GET returns the stored blob

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    text = "iRacing recipe — VR graphics — FieldOfView=100, mirrorQuality=2"
    url = paste._privatebin_send(text, "https://privatebin.example")
    assert url.startswith("https://privatebin.example/?abc123#")
    assert paste._privatebin_fetch(url) == text


def test_share_prefers_pastebin_then_privatebin(monkeypatch):
    calls = []
    monkeypatch.setattr(paste, "_pastebin", lambda t, k: calls.append("pb") or "https://pastebin.com/X")
    monkeypatch.setattr(paste, "_privatebin_send", lambda t, i: calls.append("priv") or "https://priv/?a#k")
    assert paste.share("x", pastebin_key="key") == "https://pastebin.com/X" and calls == ["pb"]
    calls.clear()
    assert paste.share("x") == "https://priv/?a#k" and calls == ["priv"]   # no key -> PrivateBin
