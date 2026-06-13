"""M5 patch-mode tests (FR-22): keyboard binds are added/replaced, everything
else is preserved byte-for-byte."""
import json

import pytest

from irtracker.gfcc import codec
from irtracker.gfcc.keymap import MOD_ALT, MOD_CTRL, MOD_SHIFT, mods_mask
from irtracker.gfcc.patch import BindingsError, apply_bindings, load_bindings


def _bindings(*items):
    return load_bindings(json.dumps({"version": 1, "bindings": list(items)}))


def test_patch_changes_only_the_target_entry(corpus_cfg_bytes):
    original = codec.decode_bytes(corpus_cfg_bytes)
    doc = codec.decode_bytes(corpus_cfg_bytes)
    changes = apply_bindings(doc, _bindings(
        {"action": "PitSpeedLimiter", "key": "p", "modifiers": ["alt"]}))
    assert len(changes) == 1

    patched = codec.decode_bytes(codec.build(doc))
    orig_entries = {e["name"]: e for e in original["controls"]["entries"]}
    new_entries = {e["name"]: e for e in patched["controls"]["entries"]}
    assert set(orig_entries) == set(new_entries)

    target = new_entries["PitSpeedLimiter"]
    assert target["type"] == "key"
    assert target["value"] == ord("P")
    assert int(target["modifiers"], 0) == MOD_ALT
    assert target["_key"] == "Alt+P"
    # unk0/flags preserved; GUID slots stay clear for keyboard binds
    assert target.get("unk0", 0) == orig_entries["PitSpeedLimiter"].get("unk0", 0)
    assert target["flags"] == orig_entries["PitSpeedLimiter"]["flags"]
    assert not any(f"slot{i}" in target for i in range(3))

    # every non-keyboard region and every other entry is untouched
    assert patched["global_config_hex"] == original["global_config_hex"]
    assert patched["trailer_hex"] == original["trailer_hex"]
    for name, entry in orig_entries.items():
        if name != "PitSpeedLimiter":
            assert new_entries[name] == entry, name


def test_patch_unbound_action_preserves_flags(corpus_cfg_bytes):
    doc = codec.decode_bytes(corpus_cfg_bytes)
    before = next(e for e in doc["controls"]["entries"] if e["name"] == "Throttle2")
    flags = before["flags"]
    apply_bindings(doc, _bindings({"action": "Throttle2", "key": "f4"}))
    after = next(e for e in doc["controls"]["entries"] if e["name"] == "Throttle2")
    assert after["type"] == "key"
    assert after["value"] == 115  # VK F4
    assert after["flags"] == flags
    # result still builds and re-decodes
    codec.decode_bytes(codec.build(doc))


def test_refuses_to_overwrite_axis_or_button(corpus_cfg_bytes):
    doc = codec.decode_bytes(corpus_cfg_bytes)
    with pytest.raises(BindingsError, match="axis"):
        apply_bindings(doc, _bindings({"action": "Throttle", "key": "t"}))


def test_unknown_action_suggests_close_match(corpus_cfg_bytes):
    doc = codec.decode_bytes(corpus_cfg_bytes)
    with pytest.raises(BindingsError, match="PitSpeedLimiter"):
        apply_bindings(doc, _bindings({"action": "PitSpeedLimitr", "key": "p"}))


def test_unknown_key_rejected(corpus_cfg_bytes):
    doc = codec.decode_bytes(corpus_cfg_bytes)
    with pytest.raises(BindingsError, match="unknown key"):
        apply_bindings(doc, _bindings({"action": "PitSpeedLimiter", "key": "hyperspace"}))


def test_modifier_combinations():
    assert mods_mask(["shift"]) == MOD_SHIFT
    assert mods_mask(["ctrl", "alt"]) == MOD_CTRL | MOD_ALT
    assert mods_mask([]) == 0
    with pytest.raises(ValueError):
        mods_mask(["hyper"])


def test_load_bindings_validation():
    with pytest.raises(BindingsError, match="version"):
        load_bindings('{"bindings": []}')
    with pytest.raises(BindingsError, match="non-empty"):
        load_bindings('{"version": 1, "bindings": []}')
    with pytest.raises(BindingsError, match="action"):
        load_bindings('{"version": 1, "bindings": [{"key": "p"}]}')
    with pytest.raises(BindingsError, match="JSON"):
        load_bindings("{nope")


def test_case_insensitive_action_lookup(corpus_cfg_bytes):
    doc = codec.decode_bytes(corpus_cfg_bytes)
    changes = apply_bindings(doc, _bindings({"action": "pitspeedlimiter", "key": "p"}))
    assert "PitSpeedLimiter" in changes[0]
