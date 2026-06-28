import json

from irtracker import overlay


def test_render_text():
    assert overlay.render_text({"profile": "Baseline", "pending": 0}) == "iRacing Config: Baseline (backed up)"
    assert overlay.render_text({"profile": "Oval", "pending": 3}) == "iRacing Config: Oval (3 unsaved)"
    assert overlay.render_text({}) == "iRacing Config: default (backed up)"


def test_enable_writes_file_and_disable_clears(tmp_path):
    from irtracker.gui import GuiApi
    ira = tmp_path / "iRacing"; ira.mkdir()
    (ira / "app.ini").write_text("[ControlProfiles]\nGlobal=Baseline\n", encoding="utf-8")
    cfgp = tmp_path / "config.toml"
    cfgp.write_text(f'[paths]\niracing_dir = "{ira.as_posix()}"\n'
                    f'data_dir = "{(tmp_path / "data").as_posix()}"\n'
                    f'[watcher]\nsim_processes = ["__none__.exe"]\n', encoding="utf-8")
    api = GuiApi(str(cfgp))
    cfg = api._config()
    json_path, txt_path = overlay.paths(cfg)

    assert not overlay.is_enabled(cfg)              # opt-in, off by default
    overlay.refresh(cfg)
    assert not txt_path.exists()                    # refresh is a no-op while disabled

    assert api.set_overlay_enabled(True)["ok"]
    assert overlay.is_enabled(cfg)
    assert "Baseline" in txt_path.read_text(encoding="utf-8")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["profile"] == "Baseline" and data["app"] == "iRacing Config Tracker"
    assert "text" in data and "updated" in data

    assert api.set_overlay_enabled(False)["ok"]
    assert not json_path.exists() and not txt_path.exists()
