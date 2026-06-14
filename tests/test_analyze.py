"""Duplicate / conflicting binding detection."""
import json

from irtracker.gfcc import codec
from irtracker.gfcc.analyze import find_binding_conflicts
from irtracker.gfcc.patch import apply_bindings, load_bindings

_INST_A = "D94DE5E0-6276-11F1-8001-444553540000"
_INST_B = "D94E3400-6276-11F1-8003-444553540000"
_PRODUCT = "C24F046D-0000-0000-0000-504944564944"  # trailing PIDVID marker


def _bindings(*items):
    return load_bindings(json.dumps({"version": 1, "bindings": list(items)}))


def test_shared_axis_is_not_a_conflict(corpus_cfg_bytes):
    # SteerLeft / SteerRight share one wheel axis in the corpus — that's normal.
    doc = codec.decode_bytes(corpus_cfg_bytes)
    flagged = {a for c in find_binding_conflicts(doc) for a in c.actions}
    assert "SteerLeft" not in flagged
    assert "SteerRight" not in flagged


def test_detects_key_conflict(corpus_cfg_bytes):
    doc = codec.decode_bytes(corpus_cfg_bytes)
    apply_bindings(doc, _bindings(
        {"action": "PitSpeedLimiter", "key": "p"},
        {"action": "Throttle2", "key": "p"}))
    key_conflicts = [c for c in find_binding_conflicts(doc) if c.kind == "key"]
    assert any({"PitSpeedLimiter", "Throttle2"} <= set(c.actions) for c in key_conflicts)


def test_modifier_distinguishes_keys(corpus_cfg_bytes):
    # P and Alt+P are different inputs and must never land in the same conflict.
    doc = codec.decode_bytes(corpus_cfg_bytes)
    apply_bindings(doc, _bindings(
        {"action": "PitSpeedLimiter", "key": "p"},
        {"action": "Throttle2", "key": "p", "modifiers": ["alt"]}))
    conflicts = find_binding_conflicts(doc)
    assert not any({"PitSpeedLimiter", "Throttle2"} <= set(c.actions) for c in conflicts)


def test_cross_context_key_reuse_is_not_flagged(corpus_cfg_bytes):
    # In the corpus 'A' is CamLatInc (camera) + PitSpeedLimiter (driving) -- the
    # sim reuses keys across contexts on purpose, so this is not a conflict.
    doc = codec.decode_bytes(corpus_cfg_bytes)
    conflicts = find_binding_conflicts(doc)
    assert not any({"CamLatInc", "PitSpeedLimiter"} <= set(c.actions) for c in conflicts)


def test_detects_button_conflict():
    doc = {"controls": {"entries": [
        {"name": "ActionA", "type": "button", "value": 16, "slot1": _INST_A, "slot2": _PRODUCT},
        {"name": "ActionB", "type": "button", "value": 16, "slot1": _INST_A, "slot2": _PRODUCT},
        {"name": "ActionC", "type": "button", "value": 8, "slot1": _INST_A, "slot2": _PRODUCT},
    ]}}
    conflicts = [c for c in find_binding_conflicts(doc) if c.kind == "button"]
    assert len(conflicts) == 1
    assert set(conflicts[0].actions) == {"ActionA", "ActionB"}


def test_same_button_on_different_devices_is_not_a_conflict():
    doc = {"controls": {"entries": [
        {"name": "A", "type": "button", "value": 16, "slot1": _INST_A, "slot2": _PRODUCT},
        {"name": "B", "type": "button", "value": 16, "slot1": _INST_B, "slot2": _PRODUCT},
    ]}}
    assert find_binding_conflicts(doc) == []
