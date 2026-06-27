import pytest

from irtracker import ctrlprofile


def test_bundle_roundtrip():
    text = ctrlprofile.build_bundle(
        "Baseline", {"controls.cfg": b"\x00\x01raw", "joyCalib.yaml": b"calib: 1\n"},
        build="2026.06.12.02", devices=["G29"])
    b = ctrlprofile.parse_bundle(text)
    assert b["kind"] == "irtracker-controls" and b["name"] == "Baseline"
    assert b["build"] == "2026.06.12.02" and b["devices"] == ["G29"]
    assert b["files"]["controls.cfg"] == b"\x00\x01raw"      # binary survives base64
    assert b["files"]["joyCalib.yaml"] == b"calib: 1\n"


def test_parse_rejects_junk():
    with pytest.raises(ValueError):
        ctrlprofile.parse_bundle("not json")
    with pytest.raises(ValueError):
        ctrlprofile.parse_bundle('{"kind": "something-else"}')
    with pytest.raises(ValueError):
        ctrlprofile.parse_bundle('{"kind": "irtracker-controls", "files": {}}')  # no controls.cfg
