"""Health-check (doctor) smoke tests."""
from irtracker.doctor import FAIL, OK, WARN, Check, run_checks, summarize


def test_doctor_runs_and_reports(cfg):
    checks = run_checks(cfg)
    assert checks, "doctor should return at least one check"
    assert all(isinstance(c, Check) for c in checks)
    assert all(c.status in (OK, WARN, FAIL) for c in checks)
    names = {c.name for c in checks}
    for expected in ("Git available", "iRacing folder", "Backup history",
                     "Tracked files", "Controls decoder"):
        assert expected in names, expected
    fails, warns = summarize(checks)
    assert isinstance(fails, int) and isinstance(warns, int)


def test_doctor_flags_missing_iracing_folder(cfg, tmp_path):
    cfg.iracing_dir = tmp_path / "does-not-exist"
    checks = {c.name: c for c in run_checks(cfg)}
    assert checks["iRacing folder"].status == FAIL
