import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from irtracker import snapshot as snapshot_mod  # noqa: E402
from irtracker.config import Config, TrackedPattern  # noqa: E402

CORPUS = Path(__file__).parent / "corpus"


@pytest.fixture(autouse=True)
def fast_reads(monkeypatch):
    monkeypatch.setattr(snapshot_mod, "SETTLE_SECONDS", 0.0)


@pytest.fixture
def corpus_cfg_bytes() -> bytes:
    return (CORPUS / "controls.cfg").read_bytes()


@pytest.fixture
def cfg(tmp_path) -> Config:
    iracing = tmp_path / "iracing"
    iracing.mkdir()
    return Config(
        iracing_dir=iracing,
        data_dir=tmp_path / "data",
        debounce_seconds=0.1,
        tracked=[
            TrackedPattern("app.ini", "track",
                           ignore_keys=["Display/windowed*"]),
            TrackedPattern("rendererDX11*.ini", "track"),
            TrackedPattern("controls.cfg", "track"),
            TrackedPattern("joyCalib.yaml", "track"),
            TrackedPattern("core.ini", "track"),
            TrackedPattern("fueldata.ini", "track-collapsed"),
            TrackedPattern("camera.ini", "ignore"),
        ],
    )
