from irtracker.simstate import ContextCache


class _FakeIR:
    is_initialized = True
    is_connected = True

    def __init__(self, d):
        self.d = d

    def startup(self):
        return True

    def __getitem__(self, k):
        return self.d.get(k)


def _sdk(best, inc, car="Porsche 992 GT3", track="Spa"):
    return _FakeIR({
        "DriverInfo": {"DriverCarIdx": 0, "Drivers": [{"CarIdx": 0, "CarScreenName": car}]},
        "WeekendInfo": {"TrackName": track},
        "LapBestLapTime": best, "PlayerCarMyIncidentCount": inc,
    })


def test_context_poll_captures_and_resets_result(tmp_path):
    cache = ContextCache(tmp_path)
    cache._ir = _sdk(98.234, 4)
    cache.poll()
    assert cache.context.car == "Porsche 992 GT3" and cache.context.track == "Spa"
    assert abs(cache.context.best_lap - 98.234) < 1e-6
    assert cache.context.incidents == 4

    cache._ir = _sdk(0.0, 0)          # new session: incidents reset down, no lap yet
    cache.poll()
    assert cache.context.best_lap is None     # carried-over best lap cleared
    assert cache.context.incidents == 0

    cache._ir = _sdk(97.5, 1)         # fresh lap in the new session
    cache.poll()
    assert abs(cache.context.best_lap - 97.5) < 1e-6 and cache.context.incidents == 1


def test_sessions_aggregate_best_lap_and_pb(tmp_path):
    from irtracker.config import load_config
    from irtracker.snapshot import Tracker
    from irtracker.gui import GuiApi
    ira = tmp_path / "iRacing"; ira.mkdir()
    cfgp = tmp_path / "config.toml"
    cfgp.write_text(f'[paths]\niracing_dir = "{ira.as_posix()}"\n'
                    f'data_dir = "{(tmp_path / "data").as_posix()}"\n'
                    f'[watcher]\nsim_processes = ["__none__.exe"]\n', encoding="utf-8")
    t = Tracker(load_config(cfgp))
    n = 0

    def snap(trigger, best, inc, exit=False):
        nonlocal n; n += 1
        (ira / "app.ini").write_text(f"[Graphics]\nFOV={n}\n", encoding="utf-8")
        t.take_snapshot(trigger, sim_running=not exit, car="Porsche 992 GT3",
                        track="Spa", best_lap=best, incidents=inc)

    snap("event", 99.5, 2); snap("event", 98.2, 4); snap("sim_exit", 98.2, 4, exit=True)
    snap("event", 99.0, 1); snap("sim_exit", 99.0, 1, exit=True)

    api = GuiApi(str(cfgp))
    items = api.get_history()["items"]
    assert items[0]["bestLapStr"] == "1:39.000" and items[0]["incidents"] == 1

    sessions = api.list_sessions()["items"]            # newest first
    assert len(sessions) == 2
    assert sessions[0]["bestLapStr"] == "1:39.000" and sessions[0]["incidents"] == 1
    assert sessions[0]["isPB"] is False
    assert sessions[1]["bestLapStr"] == "1:38.200" and sessions[1]["incidents"] == 4
    assert sessions[1]["isPB"] is True                 # fastest session is the PB
