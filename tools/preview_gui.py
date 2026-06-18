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
# Source the throwaway "live" folder from the test corpus — the repo no longer
# ships root-level personal config files.
CORPUS = ROOT / "tests" / "corpus"
REF_FILES = {
    "controls.cfg": CORPUS / "controls.cfg",
    "joyCalib.yaml": CORPUS / "joyCalib.yaml",
}


def _populate() -> str:
    tmp = Path(tempfile.mkdtemp(prefix="irtrack-preview-"))
    ira = tmp / "iRacing"; ira.mkdir()
    data = tmp / "data"; data.mkdir()
    # Mirror iRacing's control-profiles layout: controls.cfg/joyCalib.yaml live in
    # a profile subfolder named by app.ini's [ControlProfiles] Global key.
    (ira / "app.ini").write_text("[ControlProfiles]\nGlobal=Baseline\n", encoding="utf-8")
    # Seed two control profiles so per-profile history/labels are exercised.
    prof = ira / "profiles" / "controls" / "Baseline"; prof.mkdir(parents=True)
    for profile in ("Baseline", "Oval"):
        pdir = ira / "profiles" / "controls" / profile; pdir.mkdir(parents=True, exist_ok=True)
        for name, src in REF_FILES.items():
            if src.exists():
                shutil.copy2(src, pdir / name)
    cfg_path = tmp / "config.toml"
    cfg_path.write_text(
        f'[paths]\niracing_dir = "{ira.as_posix()}"\n'
        f'data_dir = "{data.as_posix()}"\n[watcher]\nnotifications = false\n',
        encoding="utf-8")

    api = GuiApi(str(cfg_path))
    api.backup_now("known-good baseline")
    # a second backup with a small edit so history + diffs have content
    jc = prof / "joyCalib.yaml"
    if jc.exists():
        text = jc.read_text(encoding="utf-8", errors="replace")
        new = re.sub(r"(CalibCenter:\s*)(\d+)", lambda m: f"{m.group(1)}{int(m.group(2)) + 1}",
                     text, count=1)
        jc.write_text(new if new != text else text + "\n# preview tweak\n", encoding="utf-8")
    # also rebind a control so per-control "blame" history has something to show
    cc = prof / "controls.cfg"
    if cc.exists():
        from irtracker.gfcc import codec
        doc = codec.decode_bytes(cc.read_bytes())
        e = next((x for x in doc["controls"]["entries"] if x["name"] == "ToggleUIVisible"), None)
        if e:
            e["value"] = 70; e["modifiers"] = 0x300000  # rebind to Alt+F
            cc.write_bytes(codec.build(doc))
    b = api.backup_now("after a small tweak")
    if b.get("rev"):
        api.create_tag("daytona-good", b["rev"], "known good at Daytona")
    return str(cfg_path)


if __name__ == "__main__":
    cfg = _populate()
    print(f"preview config: {cfg}")
    _BrowserBridge(GuiApi(cfg)).serve(port=PORT, open_browser=False)
