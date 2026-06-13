"""GFCC binary codec for iRacing controls.cfg.

File layout (reverse engineered):
  GFCC chunk: magic 'GFCC' + u32 version + u32 payload_size, payload = rest of file
    global config blob (FFB/calibration; 147 bytes in observed files, kept as hex)
    LRTC chunk: magic 'LRTC' + u32 version + u32 payload_size
      payload = N records, each: cstring name + 68-byte body
        body: 5x u32 (unk0, flags, bind_type, value, modifiers) + 3x 16-byte slots
        bind_type: 0=unbound, 1=axis, 2=button, 4=key
        axis:   value = axis index
        button: value = bitmask, 1 << btn (UI shows 0-based "Btn N"; 32 max/device)
        key:    value = Windows VK code; modifiers bit-pairs:
                0x30000 Shift, 0xC0000 Ctrl, 0x300000 Alt
        axis:   slot0=instance GUID, slot1=product GUID, slot2=extra
        button: slot0=extra,         slot1=instance GUID, slot2=product GUID
    trailer bytes after LRTC (2 spaces in observed files)

Unknown/unparsed regions (global config blob, trailer, unk0/flags fields) are
carried opaquely so decode -> encode of an unmodified file is byte-identical
(FR-21). JSON keys starting with '_' are annotations only and are ignored when
rebuilding the binary.
"""
from __future__ import annotations

import struct
from typing import Any

from irtracker.gfcc.keymap import VK_NAMES, mods_note

TYPE_NAMES = {0: "unbound", 1: "axis", 2: "button", 4: "key"}
TYPE_IDS = {v: k for k, v in TYPE_NAMES.items()}
ZERO16 = b"\x00" * 16
RECORD_BODY_LEN = 68

KNOWN_DEVICES = {
    (0x046D, 0xC24F): "Logitech G29 Driving Force Racing Wheel",
    (0x046D, 0xC260): "Logitech G29 Driving Force Racing Wheel (PS mode)",
    (0x046D, 0xC262): "Logitech G920 Driving Force Racing Wheel",
    (0x046D, 0xC266): "Logitech G923 Racing Wheel (PS)",
    (0x046D, 0xC26E): "Logitech G923 Racing Wheel (Xbox)",
    (0x0EB7, 0x0001): "Fanatec ClubSport Wheel Base",
    (0x0EB7, 0x0020): "Fanatec ClubSport DD",
    (0x0EB7, 0x6204): "Fanatec CSL Elite Pedals",
    (0x16D0, 0x0D5A): "Simucube 2 Sport",
    (0x16D0, 0x0D5F): "Simucube 2 Pro",
    (0x16D0, 0x0D60): "Simucube 2 Ultimate",
    (0x044F, 0xB66E): "Thrustmaster T300RS",
    (0x044F, 0xB692): "Thrustmaster TS-PC Racer",
}


class GfccError(Exception):
    """controls.cfg could not be parsed or rebuilt."""


def guid_to_str(b: bytes) -> str:
    d1, d2, d3 = struct.unpack_from("<IHH", b)
    return f"{d1:08X}-{d2:04X}-{d3:04X}-{b[8:10].hex().upper()}-{b[10:].hex().upper()}"


def guid_from_str(s: str) -> bytes:
    p = s.strip("{}").split("-")
    if len(p) != 5:
        raise GfccError(f"malformed GUID: {s!r}")
    return (struct.pack("<IHH", int(p[0], 16), int(p[1], 16), int(p[2], 16))
            + bytes.fromhex(p[3]) + bytes.fromhex(p[4]))


def device_note(guid_bytes: bytes) -> str | None:
    """Decode VID/PID from DirectInput product GUIDs (trailing 'PIDVID' marker)."""
    if guid_bytes[10:] != b"PIDVID":
        return None
    d1 = struct.unpack_from("<I", guid_bytes)[0]
    vid, pid = d1 & 0xFFFF, d1 >> 16
    name = KNOWN_DEVICES.get((vid, pid), "unknown device")
    return f"VID {vid:04X} PID {pid:04X} - {name}"


def parse(data: bytes) -> dict[str, Any]:
    """Parse controls.cfg bytes into the documented JSON-able dict."""
    if data[:4] != b"GFCC":
        raise GfccError("not a controls.cfg: missing GFCC magic")
    if len(data) < 12:
        raise GfccError("truncated GFCC header")
    gver, gsize = struct.unpack_from("<II", data, 4)
    if 12 + gsize != len(data):
        raise GfccError(f"GFCC size mismatch: header says {12 + gsize}, file is {len(data)}")
    li = data.find(b"LRTC", 12)
    if li < 0:
        raise GfccError("LRTC chunk not found")
    lver, lsize = struct.unpack_from("<II", data, li + 4)
    if li + 12 + lsize > len(data):
        raise GfccError("LRTC payload extends past end of file")
    payload = data[li + 12: li + 12 + lsize]
    trailer = data[li + 12 + lsize:]

    entries: list[dict[str, Any]] = []
    devices: dict[str, str] = {}
    p = 0
    while p < len(payload):
        e = payload.find(b"\x00", p)
        if e < 0:
            raise GfccError(f"unterminated record name at payload offset {p}")
        try:
            name = payload[p:e].decode("ascii")
        except UnicodeDecodeError as exc:
            raise GfccError(f"non-ASCII record name at payload offset {p}") from exc
        body = payload[e + 1: e + 1 + RECORD_BODY_LEN]
        if len(body) != RECORD_BODY_LEN:
            raise GfccError(f"truncated record body at payload offset {p} ({name})")
        unk0, flags, btype, value, mods = struct.unpack_from("<5I", body)
        slots = [body[20:36], body[36:52], body[52:68]]

        entry: dict[str, Any] = {"name": name}
        if unk0:
            entry["unk0"] = unk0
        entry["flags"] = flags
        entry["type"] = TYPE_NAMES.get(btype, btype)
        if value or btype:
            entry["value"] = value
        if mods:
            entry["modifiers"] = f"{mods:#x}"
            note = mods_note(mods)
            if note:
                entry["_mods"] = note
        for i, s in enumerate(slots):
            if s != ZERO16:
                g = guid_to_str(s)
                entry[f"slot{i}"] = g
                dn = device_note(s)
                if dn:
                    devices[g] = dn
        if btype == 4 and value in VK_NAMES:
            entry["_key"] = (entry.get("_mods", "") + "+" if mods else "") + VK_NAMES[value]
        if btype == 2 and value and value & (value - 1) == 0:
            entry["_button"] = f"Btn {value.bit_length() - 1}"
        entries.append(entry)
        p = e + 1 + RECORD_BODY_LEN

    return {
        "_comment": "Decoded iRacing controls.cfg. Keys starting with '_' are "
                    "annotations and are ignored when rebuilding the binary. Omitted "
                    "fields default to 0 / empty. Round trip is byte-identical.",
        "_devices": devices,
        "header": {"magic": "GFCC", "version": gver},
        "global_config_hex": data[12:li].hex(),
        "controls": {"magic": "LRTC", "version": lver, "entries": entries},
        "trailer_hex": trailer.hex(),
    }


def build(doc: dict[str, Any]) -> bytes:
    """Rebuild controls.cfg bytes from the decoded dict (annotations ignored)."""
    recs = b""
    for entry in doc["controls"]["entries"]:
        t = entry.get("type", 0)
        btype = TYPE_IDS[t] if isinstance(t, str) else t
        mods = entry.get("modifiers", 0)
        if isinstance(mods, str):
            mods = int(mods, 0)
        body = struct.pack("<5I", entry.get("unk0", 0), entry.get("flags", 0),
                           btype, entry.get("value", 0), mods)
        for i in range(3):
            s = entry.get(f"slot{i}")
            body += guid_from_str(s) if s else ZERO16
        try:
            recs += entry["name"].encode("ascii") + b"\x00" + body
        except UnicodeEncodeError as exc:
            raise GfccError(f"non-ASCII entry name {entry['name']!r}") from exc

    lrtc = b"LRTC" + struct.pack("<II", doc["controls"]["version"], len(recs)) + recs
    payload = (bytes.fromhex(doc["global_config_hex"]) + lrtc
               + bytes.fromhex(doc["trailer_hex"]))
    return b"GFCC" + struct.pack("<II", doc["header"]["version"], len(payload)) + payload


def decode_bytes(data: bytes) -> dict[str, Any]:
    """Parse with round-trip verification: refuses to emit JSON that would not
    rebuild byte-identically (FR-21)."""
    doc = parse(data)
    if build(doc) != data:
        raise GfccError("rebuilt bytes differ from original; refusing to decode")
    return doc


def verify_roundtrip(data: bytes) -> bool:
    try:
        return build(parse(data)) == data
    except GfccError:
        return False
