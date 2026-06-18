"""iRacing control-profiles: per-profile versioning.

iRacing's "control profiles" feature stores each named profile's controls.cfg /
joyCalib.yaml under profiles\\controls\\<name>\\, with the active profile named
in app.ini's [ControlProfiles] Global key. The tracker versions every profile
independently (one repo key per profile file) while top-level files stay flat.
"""
from irtracker.config import active_control_profile
from irtracker.snapshot import Tracker

# trailing whitespace + an inline ';' comment, like real iRacing .ini lines
APP_WITH_PROFILE = (
    "[Misc]\nglobal=ignore-me\n\n[ControlProfiles]\nGlobal=Oval  \t; active profile\n"
)


def _profile(cfg, name, controls=b"", joycalib=None):
    d = cfg.iracing_dir / "profiles" / "controls" / name
    d.mkdir(parents=True, exist_ok=True)
    if controls is not None:
        (d / "controls.cfg").write_bytes(controls)
    if joycalib is not None:
        (d / "joyCalib.yaml").write_bytes(joycalib)
    return d


def test_active_control_profile_parsing(cfg):
    assert active_control_profile(cfg.iracing_dir) is None  # no app.ini -> legacy
    (cfg.iracing_dir / "app.ini").write_text(APP_WITH_PROFILE, encoding="utf-8")
    # only [ControlProfiles] Global counts, not the earlier bare 'global='
    assert active_control_profile(cfg.iracing_dir) == "Oval"


def test_live_path_resolution(cfg):
    # legacy bare name (no profile) -> top level
    assert cfg.live_path("controls.cfg") == cfg.iracing_dir / "controls.cfg"

    (cfg.iracing_dir / "app.ini").write_text(APP_WITH_PROFILE, encoding="utf-8")
    _profile(cfg, "Oval")
    # a bare name resolves to the *active* profile (live Controls view / re-map)
    assert cfg.live_path("controls.cfg") == cfg.iracing_dir / "profiles" / "controls" / "Oval" / "controls.cfg"
    # an explicit profile-relative key maps straight through (any profile)
    assert cfg.live_path("profiles/controls/Road/controls.cfg") == \
        cfg.iracing_dir / "profiles" / "controls" / "Road" / "controls.cfg"
    # non-profile files never move
    assert cfg.live_path("app.ini") == cfg.iracing_dir / "app.ini"


def test_tracked_files_present_uses_profile_keys(cfg, corpus_cfg_bytes):
    (cfg.iracing_dir / "app.ini").write_text(APP_WITH_PROFILE, encoding="utf-8")
    _profile(cfg, "Oval", controls=corpus_cfg_bytes)
    _profile(cfg, "Road", controls=corpus_cfg_bytes)
    # a stale top-level leftover must be ignored once profiles exist
    (cfg.iracing_dir / "controls.cfg").write_bytes(b"stale leftover")

    present = cfg.tracked_files_present()
    assert "profiles/controls/Oval/controls.cfg" in present
    assert "profiles/controls/Road/controls.cfg" in present
    assert "app.ini" in present
    assert "controls.cfg" not in present  # the stale top-level copy is skipped


def test_each_profile_versioned_separately(cfg, corpus_cfg_bytes):
    (cfg.iracing_dir / "app.ini").write_text(APP_WITH_PROFILE, encoding="utf-8")
    _profile(cfg, "Oval", controls=corpus_cfg_bytes)
    _profile(cfg, "Road", controls=corpus_cfg_bytes)
    (cfg.iracing_dir / "controls.cfg").write_bytes(b"stale leftover")  # ignored

    tracker = Tracker(cfg)
    r = tracker.take_snapshot("manual")
    assert r.committed
    # both profiles are committed under their own keys; the stale top-level isn't
    assert tracker.repo.show_file("HEAD", "profiles/controls/Oval/controls.cfg") == corpus_cfg_bytes
    assert tracker.repo.show_file("HEAD", "profiles/controls/Road/controls.cfg") == corpus_cfg_bytes
    assert not tracker.repo.file_exists_at("HEAD", "controls.cfg")

    # editing ONE profile only versions that profile
    (cfg.iracing_dir / "profiles" / "controls" / "Oval" / "controls.cfg").write_bytes(b"GFCC edited oval")
    r2 = tracker.take_snapshot("event")
    assert "profiles/controls/Oval/controls.cfg" in r2.files
    assert "profiles/controls/Road/controls.cfg" not in r2.files


def test_restore_targets_the_right_profile(cfg, corpus_cfg_bytes):
    (cfg.iracing_dir / "app.ini").write_text(APP_WITH_PROFILE, encoding="utf-8")
    oval = _profile(cfg, "Oval", controls=corpus_cfg_bytes) / "controls.cfg"
    road = _profile(cfg, "Road", controls=corpus_cfg_bytes) / "controls.cfg"

    tracker = Tracker(cfg)
    tracker.take_snapshot("manual")
    oval.write_bytes(b"GFCC oval changed")
    road.write_bytes(b"GFCC road changed")

    tracker.restore_file("profiles/controls/Oval/controls.cfg", "HEAD", sim_is_running=False)
    assert oval.read_bytes() == corpus_cfg_bytes        # Oval restored
    assert road.read_bytes() == b"GFCC road changed"    # Road untouched


def test_active_profile_switch_is_labelled(cfg, corpus_cfg_bytes):
    (cfg.iracing_dir / "app.ini").write_text(
        "[ControlProfiles]\nGlobal=Oval\n", encoding="utf-8")
    _profile(cfg, "Oval", controls=corpus_cfg_bytes)
    _profile(cfg, "Road", controls=corpus_cfg_bytes)
    tracker = Tracker(cfg)
    tracker.take_snapshot("manual")
    # user switches the active profile in iRacing -> app.ini Global changes
    (cfg.iracing_dir / "app.ini").write_text(
        "[ControlProfiles]\nGlobal=Road\n", encoding="utf-8")
    r = tracker.take_snapshot("event")
    assert r.committed
    assert tracker.repo.snapshot_at("HEAD").meta.message == \
        "Switched active control profile: Oval → Road"


def test_gui_controls_profile_selection(tmp_path, corpus_cfg_bytes):
    from irtracker.gui import GuiApi
    ira = tmp_path / "iRacing"; ira.mkdir()
    (ira / "app.ini").write_text("[ControlProfiles]\nGlobal=Baseline\n", encoding="utf-8")
    for p in ("Baseline", "Oval"):
        d = ira / "profiles" / "controls" / p; d.mkdir(parents=True)
        (d / "controls.cfg").write_bytes(corpus_cfg_bytes)
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        f'[paths]\niracing_dir = "{ira.as_posix()}"\n'
        f'data_dir = "{(tmp_path / "data").as_posix()}"\n', encoding="utf-8")

    api = GuiApi(str(cfg_path))
    r = api.get_controls()
    assert r["ok"] and r["available"]
    assert sorted(r["profiles"]) == ["Baseline", "Oval"]
    assert r["profile"] == "Baseline" and r["activeProfile"] == "Baseline"
    # an explicit (non-active) profile is honoured by controls, devices, identify
    assert api.get_controls(profile="Oval")["profile"] == "Oval"
    assert api.get_devices(profile="Oval")["ok"]
    assert api.identify_input("Space", profile="Oval")["ok"]
    # an unknown profile falls back to the active one
    assert api.get_controls(profile="Nope")["profile"] == "Baseline"


def test_known_good_restore_points(tmp_path, corpus_cfg_bytes):
    from irtracker.gui import GuiApi
    ira = tmp_path / "iRacing"; ira.mkdir()
    (ira / "app.ini").write_text("[ControlProfiles]\nGlobal=Baseline\n", encoding="utf-8")
    d = ira / "profiles" / "controls" / "Baseline"; d.mkdir(parents=True)
    (d / "controls.cfg").write_bytes(corpus_cfg_bytes)
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        f'[paths]\niracing_dir = "{ira.as_posix()}"\n'
        f'data_dir = "{(tmp_path / "data").as_posix()}"\n'
        f'[watcher]\nsim_processes = ["___no_such_sim___.exe"]\n', encoding="utf-8")
    api = GuiApi(str(cfg_path))

    # nothing marked yet
    assert api.list_known_good()["items"] == []
    assert api.get_overview()["lastKnownGood"] is None
    assert not api.revert_known_good()["ok"]

    # mark the current setup
    assert api.mark_known_good("Road — Daytona")["ok"]
    kg = api.list_known_good()["items"]
    assert len(kg) == 1 and kg[0]["label"] == "Road — Daytona"
    # a known-good point is NOT a Saved Setup, and the overview surfaces the latest
    assert api.list_profiles()["items"] == []
    assert api.get_overview()["lastKnownGood"]["label"] == "Road — Daytona"

    # drift the live file, then one-click revert restores it
    (d / "controls.cfg").write_bytes(b"GFCC locally broken")
    assert api.revert_known_good()["ok"]
    assert (d / "controls.cfg").read_bytes() == corpus_cfg_bytes

    # removing the mark leaves files alone but clears the known-good point
    assert api.delete_known_good(kg[0]["tag"])["ok"]
    assert api.list_known_good()["items"] == []
    assert api.get_overview()["lastKnownGood"] is None


def test_blame_control_timeline(tmp_path, corpus_cfg_bytes):
    from irtracker.gui import GuiApi
    from irtracker.gfcc import codec
    ira = tmp_path / "iRacing"; ira.mkdir()
    (ira / "app.ini").write_text("[ControlProfiles]\nGlobal=Baseline\n", encoding="utf-8")
    d = ira / "profiles" / "controls" / "Baseline"; d.mkdir(parents=True)
    cfile = d / "controls.cfg"; cfile.write_bytes(corpus_cfg_bytes)
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        f'[paths]\niracing_dir = "{ira.as_posix()}"\n'
        f'data_dir = "{(tmp_path / "data").as_posix()}"\n'
        f'[watcher]\nsim_processes = ["___no_such_sim___.exe"]\n', encoding="utf-8")
    api = GuiApi(str(cfg_path))

    api.backup_now("v1")  # ToggleUIVisible = Space
    doc = codec.decode_bytes(cfile.read_bytes())
    e = next(x for x in doc["controls"]["entries"] if x["name"] == "ToggleUIVisible")
    e["value"] = 70; e["modifiers"] = 0x300000  # rebind to Alt+F
    cfile.write_bytes(codec.build(doc))
    api.backup_now("rebind")

    b = api.blame_control("ToggleUIVisible")
    assert b["current"] == "Alt+F"
    assert [ev["value"] for ev in b["events"]] == ["Alt+F", "Space"]  # newest first
    assert b["events"][0]["message"] == "rebind"

    # an unchanged control yields a single event; an unknown one doesn't crash
    assert len(api.blame_control("BlackBoxToggle")["events"]) == 1
    assert api.blame_control("NoSuchAction")["current"] == "Not assigned"


def test_blame_setting_and_list_settings(tmp_path):
    from irtracker.gui import GuiApi
    ira = tmp_path / "iRacing"; ira.mkdir()
    (ira / "app.ini").write_text(
        "[Force Feedback]\nstrength=20.0\n\n[Graphics]\nFOV=90\n", encoding="utf-8")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        f'[paths]\niracing_dir = "{ira.as_posix()}"\n'
        f'data_dir = "{(tmp_path / "data").as_posix()}"\n'
        f'[watcher]\nsim_processes = ["___no_such_sim___.exe"]\n', encoding="utf-8")
    api = GuiApi(str(cfg_path))
    api.backup_now("v1")
    (ira / "app.ini").write_text(
        "[Force Feedback]\nstrength=35.0\n\n[Graphics]\nFOV=90\n", encoding="utf-8")
    api.backup_now("bumped strength")

    b = api.blame_setting("app.ini", "Force Feedback", "strength")
    assert b["current"] == "35.0"
    assert [e["value"] for e in b["events"]] == ["35.0", "20.0"]  # newest first
    # an unchanged setting has a single event
    assert len(api.blame_setting("app.ini", "Graphics", "FOV")["events"]) == 1

    ls = api.list_settings()
    allkeys = {(it["section"], it["key"]) for it in ls["all"]}
    assert ("Force Feedback", "strength") in allkeys and ("Graphics", "FOV") in allkeys
    recent = {(r["section"], r["key"]) for r in ls["recent"]}
    assert ("Force Feedback", "strength") in recent  # value changed -> recent
    assert ("Graphics", "FOV") not in recent          # unchanged -> not recent


def test_migrates_legacy_bare_keys_into_active_profile(cfg, corpus_cfg_bytes):
    # 1) legacy install: controls.cfg at the top level, no profiles yet
    (cfg.iracing_dir / "controls.cfg").write_bytes(corpus_cfg_bytes)
    tracker = Tracker(cfg)
    tracker.take_snapshot("manual")
    assert tracker.repo.file_exists_at("HEAD", "controls.cfg")

    # 2) iRacing introduces control profiles and moves the file into Baseline
    (cfg.iracing_dir / "controls.cfg").unlink()
    (cfg.iracing_dir / "app.ini").write_text(
        "[ControlProfiles]\nGlobal=Baseline\n", encoding="utf-8")
    _profile(cfg, "Baseline", controls=corpus_cfg_bytes)

    tracker.take_snapshot("startup_scan")
    # the bare key is gone; the profile key carries the content, and git history
    # for the file is continuous (the migration was a rename, not delete+add)
    assert not tracker.repo.file_exists_at("HEAD", "controls.cfg")
    assert tracker.repo.show_file("HEAD", "profiles/controls/Baseline/controls.cfg") == corpus_cfg_bytes
    follow = tracker.repo.git(
        "log", "--follow", "--format=%H", "--",
        "profiles/controls/Baseline/controls.cfg").stdout.strip().splitlines()
    assert len(follow) >= 2  # pre-migration commit is reachable through the rename
