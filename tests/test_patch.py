"""M5 patch-mode tests (FR-22): keyboard binds are added/replaced, everything
else is preserved byte-for-byte."""
import json

import pytest

import re

from irtracker.gfcc import codec
from irtracker.gfcc.devices import references_from_decoded
from irtracker.gfcc.keymap import MOD_ALT, MOD_CTRL, MOD_SHIFT, mods_mask
from irtracker.gfcc.patch import (
    BindingsError, apply_bindings, load_bindings, remap_device, remap_joycalib)

_FAKE_GUID = "AAAAAAAA-1111-2222-3333-444444444444"


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


# -- device re-map (FR-23) -----------------------------------------------------

def test_remap_device_moves_bindings_and_round_trips(corpus_cfg_bytes):
    doc = codec.decode_bytes(corpus_cfg_bytes)
    refs = references_from_decoded(doc)
    assert refs, "corpus controls.cfg should reference a wheel/pedal device"
    old = refs[0].instance_guid

    changed = remap_device(doc, old, _FAKE_GUID)
    assert changed, "re-map should move at least one binding"

    out = codec.build(doc)
    rebuilt = codec.decode_bytes(out)  # still valid + round-trips
    # the old instance GUID is gone everywhere; the new one is present
    slots = [rebuilt["controls"]["entries"][i].get(f"slot{j}")
             for i in range(len(rebuilt["controls"]["entries"])) for j in range(3)]
    assert old not in slots
    assert _FAKE_GUID in slots


def test_remap_device_is_byte_reversible(corpus_cfg_bytes):
    doc = codec.decode_bytes(corpus_cfg_bytes)
    old = references_from_decoded(doc)[0].instance_guid
    remap_device(doc, old, _FAKE_GUID)
    remap_device(doc, _FAKE_GUID, old)              # map back
    assert codec.build(doc) == corpus_cfg_bytes      # identical to the original


def test_remap_device_rejects_identical_guids():
    with pytest.raises(BindingsError, match="identical"):
        remap_device({"controls": {"entries": []}}, _FAKE_GUID, _FAKE_GUID)


def test_remap_device_unknown_guid_is_a_noop(corpus_cfg_bytes):
    doc = codec.decode_bytes(corpus_cfg_bytes)
    changed = remap_device(doc, "DEADBEEF-0000-0000-0000-000000000000", _FAKE_GUID)
    assert changed == []
    assert codec.build(doc) == corpus_cfg_bytes


def test_remap_joycalib_swaps_guid_and_reverses(corpus_joycalib_text):
    text = corpus_joycalib_text
    m = re.search(r"InstanceGUID:\s*'?\{?([0-9A-Fa-f]{8}-[0-9A-Fa-f-]{27})", text)
    assert m, "corpus joyCalib.yaml should contain an InstanceGUID"
    old = m.group(1)

    new_text, n = remap_joycalib(text, old, _FAKE_GUID)
    assert n >= 1
    assert old.upper() not in new_text.upper()
    assert _FAKE_GUID in new_text
    back, _ = remap_joycalib(new_text, _FAKE_GUID, old)
    assert back == text
