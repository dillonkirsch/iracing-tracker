"""In-app controls editor: GuiApi.apply_bindings_gui end-to-end tests.

Exercises the full path the GUI uses: decode the live controls.cfg, patch
keyboard bindings, re-encode, back up, write, and snapshot. Also checks the
safety guards (sim-running block, axis/button refusal).
"""
import json
from pathlib import Path

import pytest

from irtracker.config import Config, TrackedPattern
from irtracker.gfcc import codec
from irtracker.gui import GuiApi

from conftest import CORPUS


def _make_cfg(tmp_path) -> Config:
    """A config with a fake iRacing dir containing the corpus controls.cfg."""
    iracing = tmp_path / "iracing"
    iracing.mkdir()
    (iracing / "controls.cfg").write_bytes((CORPUS / "controls.cfg").read_bytes())
    return Config(
        iracing_dir=iracing,
        data_dir=tmp_path / "data",
        debounce_seconds=0.1,
        tracked=[TrackedPattern("controls.cfg", "track")],
        sim_processes=["no-such-process.exe"],
    )


def _api_for(cfg: Config) -> GuiApi:
    api = GuiApi()
    api._cfg = cfg
    return api


def test_apply_bindings_gui_patches_key(tmp_path):
    cfg = _make_cfg(tmp_path)
    api = _api_for(cfg)

    r = api.apply_bindings_gui([
        {"action": "PitSpeedLimiter", "key": "p", "modifiers": ["alt"]},
    ])
    assert r["ok"], r.get("error")
    assert len(r["changes"]) == 1
    assert "Alt+P" in r["changes"][0]
    assert r["commit"]  # a snapshot was taken

    # The live file was updated and decodes with the new binding.
    doc = codec.decode_bytes((cfg.iracing_dir / "controls.cfg").read_bytes())
    entry = next(e for e in doc["controls"]["entries"]
                 if e["name"] == "PitSpeedLimiter")
    assert entry["type"] == "key"
    assert entry["_key"] == "Alt+P"

    # A backup of the original was made.
    backups = list((cfg.data_dir / "backups").glob("controls.cfg.*.bak"))
    assert len(backups) == 1


def test_apply_bindings_gui_round_trips(tmp_path):
    """The patched file must survive decode -> encode -> decode (FR-21)."""
    cfg = _make_cfg(tmp_path)
    api = _api_for(cfg)

    r = api.apply_bindings_gui([
        {"action": "RpyPausePlay", "key": "space"},
        {"action": "PitSpeedLimiter", "key": "f6"},
    ])
    assert r["ok"], r.get("error")
    assert len(r["changes"]) == 2

    # Re-read and verify both bindings.
    doc = codec.decode_bytes((cfg.iracing_dir / "controls.cfg").read_bytes())
    by_name = {e["name"]: e for e in doc["controls"]["entries"]}
    assert by_name["RpyPausePlay"]["_key"] == "Space"
    assert by_name["PitSpeedLimiter"]["_key"] == "F6"


def test_apply_bindings_gui_blocks_sim_running(tmp_path, monkeypatch):
    cfg = _make_cfg(tmp_path)
    api = _api_for(cfg)

    # Force sim_running to return True.
    from irtracker import gui as gui_mod
    monkeypatch.setattr(gui_mod, "sim_running", lambda procs: True)

    r = api.apply_bindings_gui([
        {"action": "PitSpeedLimiter", "key": "p"},
    ])
    assert not r["ok"]
    assert "running" in r["error"].lower()

    # The live file was NOT changed.
    original = (CORPUS / "controls.cfg").read_bytes()
    assert (cfg.iracing_dir / "controls.cfg").read_bytes() == original


def test_apply_bindings_gui_refuses_axis(tmp_path):
    """An action currently bound to an axis/button must not be overwritten."""
    cfg = _make_cfg(tmp_path)
    api = _api_for(cfg)

    # SteerLeft is an axis binding in the corpus.
    r = api.apply_bindings_gui([
        {"action": "SteerLeft", "key": "a"},
    ])
    assert not r["ok"]
    assert "axis" in r["error"].lower() or "button" in r["error"].lower()


def test_apply_bindings_gui_unknown_action(tmp_path):
    cfg = _make_cfg(tmp_path)
    api = _api_for(cfg)

    r = api.apply_bindings_gui([
        {"action": "NonexistentAction", "key": "p"},
    ])
    assert not r["ok"]
    assert "unknown action" in r["error"].lower()


def test_apply_bindings_gui_snapshot_in_history(tmp_path):
    """The edit should appear in the git history as a snapshot."""
    cfg = _make_cfg(tmp_path)
    api = _api_for(cfg)

    # First make an initial snapshot so the repo is initialized.
    from irtracker.snapshot import Tracker
    Tracker(cfg).take_snapshot("manual", message="baseline")

    r = api.apply_bindings_gui([
        {"action": "PitSpeedLimiter", "key": "f6"},
    ])
    assert r["ok"], r.get("error")

    # The history should have at least 2 commits now (baseline + edit).
    tracker = Tracker(cfg)
    snaps = tracker.repo.log()
    assert len(snaps) >= 2
    # The most recent snapshot should be the edit.
    latest = snaps[0]
    assert latest.meta.trigger == "manual"
    assert "controls.cfg" in latest.meta.files
