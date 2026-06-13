"""M1 golden-file tests (FR-20/21): the corpus of real samples must round-trip
byte-identically, and synthetic documents must rebuild consistently."""
import struct

import pytest

from irtracker.gfcc import codec
from irtracker.gfcc.codec import GfccError

from conftest import CORPUS


def test_corpus_roundtrips_byte_identical():
    samples = list(CORPUS.glob("*.cfg"))
    assert samples, "corpus must contain at least one real controls.cfg"
    for sample in samples:
        data = sample.read_bytes()
        assert codec.build(codec.parse(data)) == data, sample.name
        assert codec.verify_roundtrip(data)


def test_decode_bytes_returns_documented_shape(corpus_cfg_bytes):
    doc = codec.decode_bytes(corpus_cfg_bytes)
    assert doc["header"]["magic"] == "GFCC"
    assert doc["controls"]["magic"] == "LRTC"
    assert doc["controls"]["entries"], "real file should have entries"
    entry = doc["controls"]["entries"][0]
    assert "name" in entry and "type" in entry
    # the observed global blob is 147 bytes
    assert len(bytes.fromhex(doc["global_config_hex"])) == 147


def test_annotations_are_ignored_on_rebuild(corpus_cfg_bytes):
    doc = codec.parse(corpus_cfg_bytes)
    doc["_devices"] = {"bogus": "annotation"}
    for e in doc["controls"]["entries"]:
        e["_key"] = "Garbage"
    assert codec.build(doc) == corpus_cfg_bytes


def _synthetic_doc():
    return {
        "header": {"magic": "GFCC", "version": 2},
        "global_config_hex": "deadbeef" * 4,
        "controls": {
            "magic": "LRTC",
            "version": 1,
            "entries": [
                {"name": "Ignition", "flags": 6, "type": "key", "value": 73},
                {"name": "Reset", "flags": 6, "type": "key", "value": 82,
                 "modifiers": "0x30000"},
                {"name": "Throttle2", "flags": 6, "type": "unbound"},
            ],
        },
        "trailer_hex": "2020",
    }


def test_synthetic_keyboard_only_sample_roundtrips():
    data = codec.build(_synthetic_doc())
    doc = codec.parse(data)
    assert codec.build(doc) == data
    entries = {e["name"]: e for e in doc["controls"]["entries"]}
    assert entries["Ignition"]["_key"] == "I"
    assert entries["Reset"]["_key"] == "Shift+R"
    assert entries["Throttle2"]["type"] == "unbound"


def test_bad_magic_rejected():
    with pytest.raises(GfccError, match="GFCC magic"):
        codec.parse(b"NOPE" + b"\x00" * 20)


def test_size_mismatch_rejected(corpus_cfg_bytes):
    truncated = corpus_cfg_bytes[:-1]
    with pytest.raises(GfccError, match="size mismatch"):
        codec.parse(truncated)


def test_corrupt_payload_rejected(corpus_cfg_bytes):
    # Declare a correct outer size but garbage where LRTC should be.
    payload = b"\xff" * 64
    data = b"GFCC" + struct.pack("<II", 1, len(payload)) + payload
    with pytest.raises(GfccError, match="LRTC"):
        codec.parse(data)


def test_guid_string_roundtrip():
    g = "D94DE5E0-6276-11F1-8001-444553540000"
    assert codec.guid_to_str(codec.guid_from_str(g)) == g
    assert codec.guid_to_str(codec.guid_from_str("{" + g + "}")) == g
