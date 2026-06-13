"""CLI-level tests: the full snapshot/log/diff/restore loop and the gfcc
decode/encode flow from requirements section 7."""
import json

import pytest

from irtracker import cli

from conftest import CORPUS


@pytest.fixture
def env(tmp_path):
    """A config file pointing at a fake iRacing dir + data dir."""
    iracing = tmp_path / "iracing"
    iracing.mkdir()
    config = tmp_path / "config.toml"
    config.write_text(f"""
[paths]
iracing_dir = '{iracing}'
data_dir = '{tmp_path / "data"}'

[watcher]
# Tests must not react to whatever is running on the host machine.
sim_processes = ["no-such-process.exe"]

[[tracked]]
pattern = "app.ini"
policy = "track"

[[tracked]]
pattern = "controls.cfg"
policy = "track"
""", encoding="utf-8")
    return {"config": config, "iracing": iracing, "tmp": tmp_path}


def run(*argv) -> int:
    return cli.main([str(a) for a in argv])


def test_snapshot_log_diff_show_roundtrip(env, capsys):
    (env["iracing"] / "app.ini").write_bytes(b"[FFB]\nstrength=20\n")
    assert run("snapshot", "--config", env["config"], "-m", "first") == 0
    (env["iracing"] / "app.ini").write_bytes(b"[FFB]\nstrength=44\n")
    assert run("snapshot", "--config", env["config"]) == 0

    assert run("log", "--config", env["config"]) == 0
    out = capsys.readouterr().out
    assert "manual" in out and "app.ini" in out and '"first"' in out

    assert run("diff", "HEAD~1", "HEAD", "--config", env["config"]) == 0
    out = capsys.readouterr().out
    assert "strength: 20 -> 44" in out

    assert run("show", "HEAD", "app.ini", "--config", env["config"]) == 0
    assert "strength=44" in capsys.readouterr().out


def test_tag_and_restore_via_cli(env, capsys):
    (env["iracing"] / "app.ini").write_bytes(b"v1=1\n")
    run("snapshot", "--config", env["config"], "-m", "v1")
    run("tag", "good-ffb", "--config", env["config"], "-m", "baseline")
    (env["iracing"] / "app.ini").write_bytes(b"v2=2\n")
    run("snapshot", "--config", env["config"])

    assert run("restore", "--config", env["config"], "--tag", "good-ffb", "--yes") == 0
    assert (env["iracing"] / "app.ini").read_bytes() == b"v1=1\n"

    run("tag", "--list", "--config", env["config"])
    assert "good-ffb" in capsys.readouterr().out


def test_export_via_cli(env, tmp_path, capsys):
    (env["iracing"] / "app.ini").write_bytes(b"x=1\n")
    run("snapshot", "--config", env["config"])
    out_zip = tmp_path / "snap.zip"
    assert run("export", "HEAD", "-o", out_zip, "--config", env["config"]) == 0
    assert out_zip.exists()


def test_gfcc_decode_encode_flow(tmp_path, capsys):
    base = CORPUS / "controls.cfg"
    out_json = tmp_path / "controls.json"
    assert cli.gfcc_main(["decode", str(base), "-o", str(out_json)]) == 0
    doc = json.loads(out_json.read_text(encoding="utf-8"))
    assert doc["controls"]["entries"]

    binds = tmp_path / "binds.json"
    binds.write_text(json.dumps({
        "version": 1,
        "bindings": [{"action": "PitSpeedLimiter", "key": "p", "modifiers": ["alt"]}],
    }), encoding="utf-8")
    out_cfg = tmp_path / "controls.new.cfg"
    assert cli.gfcc_main(["encode", "--base", str(base), "--bindings", str(binds),
                          "-o", str(out_cfg)]) == 0
    capsys.readouterr()

    # the patched file decodes and shows the new bind
    assert cli.main(["decode", str(out_cfg)]) == 0
    decoded = json.loads(capsys.readouterr().out)
    entry = next(e for e in decoded["controls"]["entries"]
                 if e["name"] == "PitSpeedLimiter")
    assert entry["_key"] == "Alt+P"


def test_encode_requires_output_or_install(tmp_path):
    binds = tmp_path / "binds.json"
    binds.write_text(json.dumps({
        "version": 1,
        "bindings": [{"action": "PitSpeedLimiter", "key": "p"}],
    }), encoding="utf-8")
    assert cli.main(["encode", "--base", str(CORPUS / "controls.cfg"),
                     "--bindings", str(binds)]) == 1


def test_decode_textconv_never_fails(tmp_path, capsys):
    bad = tmp_path / "controls.cfg"
    bad.write_bytes(b"not a gfcc file")
    assert cli.main(["decode", str(bad), "--textconv"]) == 0
    assert "decode_error" in capsys.readouterr().out


def test_gfcc_alias_rejects_non_codec_commands(capsys):
    assert cli.gfcc_main(["status"]) == 2
