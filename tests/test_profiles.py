"""iRacing control-profiles resolution.

iRacing's "control profiles" feature relocates controls.cfg/joyCalib.yaml into
profiles\\controls\\<active>\\; the active profile name lives in app.ini's
[ControlProfiles] Global key. The tracker must follow it (read, scan, restore)
while every other tracked file stays at the top of the iRacing folder.
"""
from irtracker.config import active_control_profile
from irtracker.snapshot import Tracker

# trailing whitespace + an inline ';' comment, like real iRacing .ini lines
APP_WITH_PROFILE = (
    "[Misc]\nglobal=ignore-me\n\n[ControlProfiles]\nGlobal=Oval  \t; active profile\n"
)


def _profile_dir(cfg, name="Oval"):
    d = cfg.iracing_dir / "profiles" / "controls" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_active_control_profile_parsing(cfg):
    assert active_control_profile(cfg.iracing_dir) is None  # no app.ini -> legacy
    (cfg.iracing_dir / "app.ini").write_text(APP_WITH_PROFILE, encoding="utf-8")
    # only the [ControlProfiles] Global key counts, not the earlier bare 'global='
    assert active_control_profile(cfg.iracing_dir) == "Oval"


def test_live_path_follows_active_profile(cfg):
    # legacy (no profile): everything is top-level
    assert cfg.live_path("controls.cfg") == cfg.iracing_dir / "controls.cfg"

    (cfg.iracing_dir / "app.ini").write_text(APP_WITH_PROFILE, encoding="utf-8")
    # profile named but its folder doesn't exist yet -> still fall back
    assert cfg.live_path("controls.cfg") == cfg.iracing_dir / "controls.cfg"

    pdir = _profile_dir(cfg)
    assert cfg.live_path("controls.cfg") == pdir / "controls.cfg"
    assert cfg.live_path("joyCalib.yaml") == pdir / "joyCalib.yaml"
    # non-profile files never move
    assert cfg.live_path("app.ini") == cfg.iracing_dir / "app.ini"


def test_tracked_files_present_finds_profile_file(cfg, corpus_cfg_bytes):
    (cfg.iracing_dir / "app.ini").write_text(APP_WITH_PROFILE, encoding="utf-8")
    pdir = _profile_dir(cfg)
    (pdir / "controls.cfg").write_bytes(corpus_cfg_bytes)
    present = cfg.tracked_files_present()
    assert "controls.cfg" in present  # found via the profile folder
    assert "app.ini" in present
    # tracked even though no top-level copy exists
    assert not (cfg.iracing_dir / "controls.cfg").exists()


def test_snapshot_reads_profile_not_stale_toplevel(cfg, corpus_cfg_bytes):
    (cfg.iracing_dir / "app.ini").write_text(APP_WITH_PROFILE, encoding="utf-8")
    prof = _profile_dir(cfg) / "controls.cfg"
    prof.write_bytes(corpus_cfg_bytes)
    # a stale top-level leftover (iRacing's migration artifact) must be ignored
    (cfg.iracing_dir / "controls.cfg").write_bytes(b"stale leftover")

    tracker = Tracker(cfg)
    assert tracker.take_snapshot("manual").committed
    assert tracker.repo.show_file("HEAD", "controls.cfg") == corpus_cfg_bytes


def test_restore_writes_back_into_profile_folder(cfg, corpus_cfg_bytes):
    (cfg.iracing_dir / "app.ini").write_text(APP_WITH_PROFILE, encoding="utf-8")
    prof = _profile_dir(cfg) / "controls.cfg"
    prof.write_bytes(corpus_cfg_bytes)
    stale = cfg.iracing_dir / "controls.cfg"
    stale.write_bytes(b"stale leftover")

    tracker = Tracker(cfg)
    tracker.take_snapshot("manual")
    prof.write_bytes(b"locally changed")  # user edits the profile file

    tracker.restore_file("controls.cfg", "HEAD", sim_is_running=False)
    assert prof.read_bytes() == corpus_cfg_bytes          # profile restored
    assert stale.read_bytes() == b"stale leftover"        # top-level untouched
