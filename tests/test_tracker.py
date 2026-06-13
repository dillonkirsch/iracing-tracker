"""M2/M3 end-to-end tests against a temp git repo and fake iRacing folder."""
import json
import zipfile

import pytest

from irtracker.config import SIDECAR_NAME
from irtracker.snapshot import SimRunningError, Tracker

from conftest import CORPUS

APP_V1 = "[Force Feedback]\nstrength=20.0\n\n[Display]\nwindowedXPos=10\n"
APP_V2 = "[Force Feedback]\nstrength=35.0\n\n[Display]\nwindowedXPos=10\n"


@pytest.fixture
def tracker(cfg):
    return Tracker(cfg)


def write(cfg, name, content):
    # Bytes always: write_text would translate \n -> \r\n on Windows and the
    # tracker is intentionally byte-exact.
    path = cfg.iracing_dir / name
    if isinstance(content, str):
        content = content.encode("utf-8")
    path.write_bytes(content)


def test_first_snapshot_and_history(tracker, cfg):
    write(cfg, "app.ini", APP_V1)
    write(cfg, "core.ini", "[Task]\nx=1\n")
    r1 = tracker.take_snapshot("manual", message="initial")
    assert r1.committed
    assert r1.files == {"app.ini": "added", "core.ini": "added"}

    write(cfg, "app.ini", APP_V2)
    r2 = tracker.take_snapshot("event", names={"app.ini"}, sim_running=True,
                               car="Porsche 992 GT3", track="Summit Point")
    assert r2.files == {"app.ini": "modified"}

    log = tracker.filtered_log()
    assert len(log) == 2
    assert log[0].meta.car == "Porsche 992 GT3"
    assert log[0].meta.context_label() == "Porsche 992 GT3 @ Summit Point"
    assert log[1].meta.context_label() == "manual edit"

    # filterable by car (FR-8)
    assert len(tracker.filtered_log(car="porsche")) == 1
    assert not tracker.filtered_log(car="mazda")

    # byte-exact content at each version (FR-19)
    first = log[-1]
    assert tracker.repo.show_file(first.commit, "app.ini") == APP_V1.encode()


def test_ignored_keys_do_not_trigger_snapshot(tracker, cfg):
    write(cfg, "app.ini", APP_V1)
    tracker.take_snapshot("manual")
    # only the ignored window-geometry key changes -> no commit (FR-27)
    write(cfg, "app.ini", APP_V1.replace("windowedXPos=10", "windowedXPos=999"))
    r = tracker.take_snapshot("event", names={"app.ini"})
    assert not r.committed
    assert r.skipped_ignored == ["app.ini"]

    # a real change later picks the drifted key up incidentally
    write(cfg, "app.ini", APP_V2.replace("windowedXPos=10", "windowedXPos=999"))
    r = tracker.take_snapshot("event", names={"app.ini"})
    assert r.committed
    assert b"windowedXPos=999" in tracker.repo.show_file("HEAD", "app.ini")


def test_ignore_policy_file_never_snapshotted(tracker, cfg):
    write(cfg, "camera.ini", "[a]\nb=1\n")
    r = tracker.take_snapshot("manual")
    assert not r.committed


def test_track_collapsed_amends_consecutive_changes(tracker, cfg):
    write(cfg, "app.ini", APP_V1)
    tracker.take_snapshot("manual")
    write(cfg, "fueldata.ini", "[car1]\ntrack=1.0\n")
    tracker.take_snapshot("event", names={"fueldata.ini"})
    count_before = len(tracker.filtered_log())

    write(cfg, "fueldata.ini", "[car1]\ntrack=1.1\n")
    r = tracker.take_snapshot("event", names={"fueldata.ini"})
    assert r.committed and r.collapsed
    assert len(tracker.filtered_log()) == count_before  # squashed, not stacked
    assert b"1.1" in tracker.repo.show_file("HEAD", "fueldata.ini")

    # a tagged snapshot is never amended away (baselines are stable)
    tracker.repo.create_tag("baseline", "HEAD")
    write(cfg, "fueldata.ini", "[car1]\ntrack=1.2\n")
    r = tracker.take_snapshot("event", names={"fueldata.ini"})
    assert r.committed and not r.collapsed
    assert len(tracker.filtered_log()) == count_before + 1


def test_deletion_recorded_and_restorable(tracker, cfg):
    yaml_content = (CORPUS / "joyCalib.yaml").read_text(encoding="utf-8")
    write(cfg, "joyCalib.yaml", yaml_content)
    tracker.take_snapshot("manual")
    first = tracker.repo.head()

    (cfg.iracing_dir / "joyCalib.yaml").unlink()
    r = tracker.take_snapshot("event", names={"joyCalib.yaml"})
    assert r.files == {"joyCalib.yaml": "deleted"}

    tracker.restore_file("joyCalib.yaml", first, sim_is_running=False)
    assert (cfg.iracing_dir / "joyCalib.yaml").read_bytes() == yaml_content.encode("utf-8")


def test_restore_blocked_while_sim_runs(tracker, cfg):
    write(cfg, "app.ini", APP_V1)
    tracker.take_snapshot("manual")
    with pytest.raises(SimRunningError):
        tracker.restore_file("app.ini", "HEAD", sim_is_running=True)
    with pytest.raises(SimRunningError):
        tracker.restore_baseline("HEAD", sim_is_running=True)


def test_restore_takes_auto_snapshot_first(tracker, cfg):
    write(cfg, "app.ini", APP_V1)
    tracker.take_snapshot("manual")
    v1 = tracker.repo.head()
    write(cfg, "app.ini", APP_V2)
    tracker.take_snapshot("manual")

    # dirty, un-snapshotted edit that the pre-restore snapshot must save
    dirty = APP_V2 + "\n[New]\nx=1\n"
    write(cfg, "app.ini", dirty)
    tracker.restore_file("app.ini", v1, sim_is_running=False)

    assert (cfg.iracing_dir / "app.ini").read_bytes() == APP_V1.encode("utf-8")
    log = tracker.filtered_log()
    triggers = [s.meta.trigger for s in log]
    assert triggers[0] == "restore"
    assert "pre_restore" in triggers
    pre = next(s for s in log if s.meta.trigger == "pre_restore")
    assert tracker.repo.show_file(pre.commit, "app.ini") == dirty.encode()


def test_restore_baseline_across_set(tracker, cfg):
    write(cfg, "app.ini", APP_V1)
    write(cfg, "core.ini", "[Task]\nx=1\n")
    tracker.take_snapshot("manual")
    tracker.repo.create_tag("good-baseline", "HEAD", "known good")

    write(cfg, "app.ini", APP_V2)
    write(cfg, "core.ini", "[Task]\nx=2\n")
    write(cfg, "fueldata.ini", "[c]\nt=1\n")  # not in the baseline
    tracker.take_snapshot("manual")

    restored, extras = tracker.restore_baseline("good-baseline", sim_is_running=False)
    assert sorted(restored) == ["app.ini", "core.ini"]
    assert extras == ["fueldata.ini"]
    assert (cfg.iracing_dir / "app.ini").read_bytes() == APP_V1.encode("utf-8")
    assert (cfg.iracing_dir / "fueldata.ini").exists()  # left untouched


def test_controls_sidecar_committed_and_failsafe(tracker, cfg, corpus_cfg_bytes):
    write(cfg, "controls.cfg", corpus_cfg_bytes)
    r = tracker.take_snapshot("manual")
    assert "controls.cfg" in r.files and SIDECAR_NAME in r.files
    sidecar = json.loads(tracker.repo.show_file("HEAD", SIDECAR_NAME))
    assert sidecar["controls"]["entries"]

    # FR-25: unparseable controls.cfg still versions raw bytes
    bad = b"GFCC" + b"\x99" * 40
    write(cfg, "controls.cfg", bad)
    r = tracker.take_snapshot("event", names={"controls.cfg"})
    assert r.committed
    assert tracker.repo.show_file("HEAD", "controls.cfg") == bad
    sidecar = json.loads(tracker.repo.show_file("HEAD", SIDECAR_NAME))
    assert "decode_error" in sidecar


def test_export_zip(tracker, cfg, tmp_path):
    write(cfg, "app.ini", APP_V1)
    write(cfg, "core.ini", "[Task]\nx=1\n")
    tracker.take_snapshot("manual", message="for export")
    out = tmp_path / "export.zip"
    names = tracker.export("HEAD", out)
    assert set(names) == {"app.ini", "core.ini"}
    with zipfile.ZipFile(out) as zf:
        assert zf.read("app.ini") == APP_V1.encode()
        meta = json.loads(zf.read("snapshot-metadata.json"))
        assert meta["message"] == "for export"


def test_live_changes_reporting(tracker, cfg):
    write(cfg, "app.ini", APP_V1)
    assert tracker.live_changes() == {"app.ini": "added"}
    tracker.take_snapshot("manual")
    assert tracker.live_changes() == {}
    write(cfg, "app.ini", APP_V2)
    assert tracker.live_changes() == {"app.ini": "modified"}
