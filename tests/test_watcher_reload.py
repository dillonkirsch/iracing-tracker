"""Watcher config-reload: GUI edits to file policies take effect without a restart."""
from irtracker.config import load_config
from irtracker.watcher import Watcher


def _config_file(tmp_path, fuel_policy="track-collapsed"):
    ira = tmp_path / "iRacing"
    ira.mkdir()
    cfgp = tmp_path / "config.toml"
    tracked = (
        '[[tracked]]\npattern = "app.ini"\npolicy = "track"\n\n'
        f'[[tracked]]\npattern = "fueldata.ini"\npolicy = "{fuel_policy}"\n'
    )
    cfgp.write_text(
        f'[paths]\niracing_dir = "{ira.as_posix()}"\n'
        f'data_dir = "{(tmp_path / "data").as_posix()}"\n'
        f'[watcher]\nsim_processes = ["___none___.exe"]\n\n'
        f'{tracked}', encoding="utf-8")
    return cfgp


def test_reload_config_picks_up_policy_change(tmp_path):
    cfgp = _config_file(tmp_path, fuel_policy="track-collapsed")
    cfg = load_config(cfgp)
    w = Watcher(cfg)
    w._config_path = cfgp

    assert w.cfg.policy_for("fueldata.ini").policy == "track-collapsed"

    # GUI edits the config: fueldata.ini -> ignore
    cfgp.write_text(
        cfgp.read_text(encoding="utf-8").replace(
            'policy = "track-collapsed"\n',
            'policy = "ignore"\n', 1),
        encoding="utf-8")

    w._reload_config()

    # Watcher now sees the new policy without a restart
    assert w.cfg.policy_for("fueldata.ini").policy == "ignore"


def test_reload_config_noop_when_unchanged(tmp_path):
    cfgp = _config_file(tmp_path)
    cfg = load_config(cfgp)
    w = Watcher(cfg)
    w._config_path = cfgp
    original_tracked = w.cfg.tracked

    w._reload_config()

    # tracked list identity unchanged when nothing changed
    assert w.cfg.tracked is original_tracked


def test_reload_config_handles_broken_file(tmp_path):
    cfgp = _config_file(tmp_path)
    cfg = load_config(cfgp)
    w = Watcher(cfg)
    w._config_path = cfgp

    cfgp.write_text("this is not valid toml {{{{", encoding="utf-8")

    # Should not raise; keeps old config
    w._reload_config()
    assert w.cfg.policy_for("fueldata.ini").policy == "track-collapsed"
