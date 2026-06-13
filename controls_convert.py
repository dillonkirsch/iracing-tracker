#!/usr/bin/env python3
"""iRacing controls.cfg <-> JSON converter.

File layout (reverse engineered):
  GFCC chunk: magic 'GFCC' + u32 version + u32 payload_size, payload = rest of file
    147-byte global config blob (FFB/calibration, kept as hex)
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
    trailer bytes after LRTC (2 spaces in observed file)

JSON keys starting with '_' are annotations only and are ignored when
rebuilding the binary. Round trip is byte-for-byte identical.

Usage:
  controls_convert.py to-json controls.cfg controls.json
  controls_convert.py to-bin  controls.json controls.cfg
  controls_convert.py verify  controls.cfg
"""
import argparse
import json
import struct
import sys

TYPE_NAMES = {0: "unbound", 1: "axis", 2: "button", 4: "key"}
TYPE_IDS = {v: k for k, v in TYPE_NAMES.items()}
ZERO16 = b"\x00" * 16

KNOWN_DEVICES = {
    (0x046D, 0xC24F): "Logitech G29 Driving Force Racing Wheel",
}

# Solid Windows virtual-key names. 197-208 look like the F-row in
# iRacing's own table (BlackBoxF1..F12 are sequential there) - unverified.
VK_NAMES = {
    8: "Backspace", 9: "Tab", 13: "Enter", 19: "Pause", 20: "CapsLock",
    27: "Esc", 32: "Space", 33: "PageUp", 34: "PageDown", 35: "End",
    36: "Home", 37: "Left", 38: "Up", 39: "Right", 40: "Down",
    44: "PrintScreen", 45: "Insert", 46: "Delete",
    106: "Numpad*", 107: "Numpad+", 109: "Numpad-", 110: "Numpad.",
    111: "Numpad/", 144: "NumLock", 186: ";", 187: "=", 188: ",",
    189: "-", 190: ".", 191: "/", 192: "`", 219: "[", 220: "\\",
    221: "]", 222: "'",
}
for _i in range(10):
    VK_NAMES[48 + _i] = str(_i)
    VK_NAMES[96 + _i] = f"Numpad{_i}"
for _i in range(26):
    VK_NAMES[65 + _i] = chr(65 + _i)
for _i in range(12):
    VK_NAMES[112 + _i] = f"F{_i + 1}"
    VK_NAMES[197 + _i] = f"F{_i + 1}?"

# Hypothesis: modifier mask uses bit pairs (maybe left/right variants).
MOD_BITS = [(0x30000, "Shift"), (0xC0000, "Ctrl"), (0x300000, "Alt")]


def guid_to_str(b):
    d1, d2, d3 = struct.unpack_from("<IHH", b)
    return f"{d1:08X}-{d2:04X}-{d3:04X}-{b[8:10].hex().upper()}-{b[10:].hex().upper()}"


def guid_from_str(s):
    p = s.split("-")
    return (struct.pack("<IHH", int(p[0], 16), int(p[1], 16), int(p[2], 16))
            + bytes.fromhex(p[3]) + bytes.fromhex(p[4]))


def device_note(guid_bytes):
    if guid_bytes[10:] != b"PIDVID":
        return None
    d1 = struct.unpack_from("<I", guid_bytes)[0]
    vid, pid = d1 & 0xFFFF, d1 >> 16
    name = KNOWN_DEVICES.get((vid, pid), "unknown device")
    return f"VID {vid:04X} PID {pid:04X} - {name}"


def mods_note(m):
    names = [n for bits, n in MOD_BITS if m & bits]
    leftover = m & ~sum(b for b, _ in MOD_BITS)
    if leftover:
        names.append(f"+{leftover:#x}")
    return "+".join(names) if names else None


def parse(data):
    if data[:4] != b"GFCC":
        sys.exit("not a controls.cfg: missing GFCC magic")
    gver, gsize = struct.unpack_from("<II", data, 4)
    if 12 + gsize != len(data):
        sys.exit(f"GFCC size mismatch: header says {12 + gsize}, file is {len(data)}")
    li = data.index(b"LRTC")
    lver, lsize = struct.unpack_from("<II", data, li + 4)
    payload = data[li + 12: li + 12 + lsize]
    trailer = data[li + 12 + lsize:]

    entries, devices = [], {}
    p = 0
    while p < len(payload):
        e = payload.index(b"\x00", p)
        name = payload[p:e].decode("ascii")
        body = payload[e + 1: e + 1 + 68]
        if len(body) != 68:
            sys.exit(f"truncated record body at payload offset {p} ({name})")
        unk0, flags, btype, value, mods = struct.unpack_from("<5I", body)
        slots = [body[20:36], body[36:52], body[52:68]]

        entry = {"name": name}
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
        p = e + 1 + 68

    return {
        "_comment": "Decoded iRacing controls.cfg. Keys starting with '_' are "
                    "annotations and are ignored by to-bin. Omitted fields default "
                    "to 0 / empty. Round trip is byte-identical.",
        "_devices": devices,
        "header": {"magic": "GFCC", "version": gver},
        "global_config_hex": data[12:li].hex(),
        "controls": {"magic": "LRTC", "version": lver, "entries": entries},
        "trailer_hex": trailer.hex(),
    }


def build(doc):
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
        recs += entry["name"].encode("ascii") + b"\x00" + body

    lrtc = b"LRTC" + struct.pack("<II", doc["controls"]["version"], len(recs)) + recs
    payload = (bytes.fromhex(doc["global_config_hex"]) + lrtc
               + bytes.fromhex(doc["trailer_hex"]))
    return b"GFCC" + struct.pack("<II", doc["header"]["version"], len(payload)) + payload


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("to-json", "to-bin", "verify"):
        p = sub.add_parser(c)
        p.add_argument("infile")
        if c != "verify":
            p.add_argument("outfile")
    args = ap.parse_args()

    if args.cmd == "to-json":
        data = open(args.infile, "rb").read()
        doc = parse(data)
        if build(doc) != data:
            sys.exit("INTERNAL ERROR: rebuilt bytes differ from original, refusing to write")
        with open(args.outfile, "w") as f:
            json.dump(doc, f, indent=2)
            f.write("\n")
        print(f"wrote {args.outfile} ({len(doc['controls']['entries'])} entries), round trip verified")
    elif args.cmd == "to-bin":
        doc = json.load(open(args.infile))
        out = build(doc)
        open(args.outfile, "wb").write(out)
        print(f"wrote {args.outfile} ({len(out)} bytes)")
    else:
        data = open(args.infile, "rb").read()
        rebuilt = build(parse(data))
        if rebuilt == data:
            print(f"OK: {args.infile} round trips byte-identical ({len(data)} bytes)")
        else:
            sys.exit("FAIL: rebuilt bytes differ from original")


if __name__ == "__main__":
    main()