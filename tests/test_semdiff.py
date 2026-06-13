"""Semantic diff tests (FR-9/26/27)."""
from irtracker import semdiff


INI_A = """\
[Force Feedback]
steeringDampingFactor=0.05              \t; damping
strengthScale=1.0

[Display]
windowedXPos=100
windowedYPos=200

[Misc]
keepMe=1
"""

INI_B = """\
[Force Feedback]
steeringDampingFactor=0.10              \t; damping
strengthScale=1.0
newKey=42

[Display]
windowedXPos=999
windowedYPos=200

[Misc]
"""


def test_parse_ini_strips_inline_comments_preserves_order():
    doc = semdiff.parse_ini(INI_A)
    assert list(doc) == ["Force Feedback", "Display", "Misc"]
    assert doc["Force Feedback"]["steeringDampingFactor"] == "0.05"


def test_diff_ini_kinds():
    changes = semdiff.diff_ini(INI_A, INI_B)
    by = {(c.section, c.key): c for c in changes}
    assert by[("Force Feedback", "steeringDampingFactor")].kind == semdiff.CHANGED
    assert by[("Force Feedback", "steeringDampingFactor")].new == "0.10"
    assert by[("Force Feedback", "newKey")].kind == semdiff.ADDED
    assert by[("Display", "windowedXPos")].kind == semdiff.CHANGED
    assert by[("Misc", "keepMe")].kind == semdiff.REMOVED
    assert ("Display", "windowedYPos") not in by
    assert ("Force Feedback", "strengthScale") not in by


def test_render_groups_by_section():
    text = semdiff.render_changes(semdiff.diff_ini(INI_A, INI_B))
    assert "[Force Feedback]" in text
    assert "steeringDampingFactor: 0.05 -> 0.10" in text


def test_ignore_globs():
    changes = semdiff.diff_ini(
        "[Display]\nwindowedXPos=1\n", "[Display]\nwindowedXPos=2\n")
    assert semdiff.only_ignored_changes(changes, ["Display/windowed*"])
    assert semdiff.only_ignored_changes(changes, ["display/WINDOWEDXPOS"])
    assert not semdiff.only_ignored_changes(changes, ["Display/fullScreen"])
    # a mixed change set must not be suppressed
    mixed = semdiff.diff_ini(
        "[Display]\nwindowedXPos=1\nfullScreen=0\n",
        "[Display]\nwindowedXPos=2\nfullScreen=1\n")
    assert not semdiff.only_ignored_changes(mixed, ["Display/windowed*"])


def test_yaml_diff():
    old = "CalibrationInfo:\n DeviceList:\n - DeviceName: 'G29'\n   AxisList:\n   - Axis: 0\n     CalibCenter: 32772\n"
    new = "CalibrationInfo:\n DeviceList:\n - DeviceName: 'G29'\n   AxisList:\n   - Axis: 0\n     CalibCenter: 32000\n"
    changes = semdiff.diff_yaml(old, new)
    assert len(changes) == 1
    assert "CalibCenter" in changes[0].key
    assert changes[0].old == "32772" and changes[0].new == "32000"


def test_controls_diff_renders_binding_level(corpus_cfg_bytes):
    from irtracker.gfcc import codec
    from irtracker.gfcc.patch import apply_bindings, load_bindings
    import json

    old_doc = codec.decode_bytes(corpus_cfg_bytes)
    new_doc = codec.decode_bytes(corpus_cfg_bytes)
    apply_bindings(new_doc, load_bindings(json.dumps({
        "version": 1,
        "bindings": [{"action": "PitSpeedLimiter", "key": "p", "modifiers": ["alt"]}],
    })))
    lines = semdiff.diff_controls(old_doc, new_doc)
    assert len(lines) == 1
    assert "PitSpeedLimiter" in lines[0]
    assert "Alt+P" in lines[0]
