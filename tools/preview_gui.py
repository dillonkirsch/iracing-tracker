"""Dev-only preview launcher for the GUI.

Spins up the web UI (browser-bridge transport) on a fixed port against a
throwaway, pre-populated config so the interface can be screenshotted/iterated
without a real iRacing install. Not part of the shipped package.

    python tools/preview_gui.py            # serves on http://127.0.0.1:8753/
"""
from __future__ import annotations

import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from irtracker.gui import GuiApi, _BrowserBridge  # noqa: E402

PORT = 8753
REF_FILES = ["app.ini", "controls.cfg", "core.ini", "fueldata.ini",
             "joyCalib.yaml", "rendererDX11Monitor.ini"]


def _populate() -> str:
    tmp = Path(tempfile.mkdtemp(prefix="irtrack-preview-"))
    ira = tmp / "iRacing"; ira.mkdir()
    data = tmp / "data"; data.mkdir()
    for n in REF_FILES:
        src = ROOT / n
        if src.exists():
            shutil.copy2(src, ira / n)
    cfg_path = tmp / "config.toml"
    cfg_path.write_text(
        f'[paths]\niracing_dir = "{ira.as_posix()}"\n'
        f'data_dir = "{data.as_posix()}"\n[watcher]\nnotifications = false\n',
        encoding="utf-8")

    api = GuiApi(str(cfg_path))
    api.backup_now("known-good baseline")
    # a second backup with a small edit so history + diffs have content
    app = ira / "app.ini"
    text = app.read_text(encoding="utf-8", errors="replace")
    new = re.sub(r"=(\d+)", lambda m: f"={int(m.group(1)) + 1}", text, count=1)
    app.write_text(new if new != text else text + "\nPreviewKey=1\n", encoding="utf-8")
    b = api.backup_now("after a small tweak")
    if b.get("rev"):
        api.create_tag("daytona-good", b["rev"], "known good at Daytona")
    return str(cfg_path)


if __name__ == "__main__":
    cfg = _populate()
    print(f"preview config: {cfg}")
    _BrowserBridge(GuiApi(cfg)).serve(port=PORT, open_browser=False)
