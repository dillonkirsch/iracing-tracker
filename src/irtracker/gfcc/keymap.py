"""Windows virtual-key and modifier tables for the GFCC codec.

Decode annotations use VK_NAMES; the bindings-JSON encoder resolves key names
through NAME_TO_VK (verified codes only -- the 197-208 "F-row?" hypothesis is
decode-only and never encoded).
"""
from __future__ import annotations

# Solid Windows virtual-key names. 197-208 look like the F-row in iRacing's
# own table (BlackBoxF1..F12 are sequential there) - unverified, decode-only.
VK_NAMES: dict[int, str] = {
    8: "Backspace", 9: "Tab", 13: "Enter", 19: "Pause", 20: "CapsLock",
    27: "Esc", 32: "Space", 33: "PageUp", 34: "PageDown", 35: "End",
    36: "Home", 37: "Left", 38: "Up", 39: "Right", 40: "Down",
    44: "PrintScreen", 45: "Insert", 46: "Delete",
    106: "Numpad*", 107: "Numpad+", 109: "Numpad-", 110: "Numpad.",
    111: "Numpad/", 144: "NumLock", 186: ";", 187: "=", 188: ",",
    189: "-", 190: ".", 191: "/", 192: "`", 219: "[", 220: "\\",
    221: "]", 222: "'",
}
_UNVERIFIED_VKS: set[int] = set()
for _i in range(10):
    VK_NAMES[48 + _i] = str(_i)
    VK_NAMES[96 + _i] = f"Numpad{_i}"
for _i in range(26):
    VK_NAMES[65 + _i] = chr(65 + _i)
for _i in range(12):
    VK_NAMES[112 + _i] = f"F{_i + 1}"
    VK_NAMES[197 + _i] = f"F{_i + 1}?"
    _UNVERIFIED_VKS.add(197 + _i)

# Reverse map for encoding: verified codes only, lowercase names.
NAME_TO_VK: dict[str, int] = {
    name.lower(): vk for vk, name in VK_NAMES.items() if vk not in _UNVERIFIED_VKS
}
NAME_TO_VK.update({
    "escape": 27, "return": 13, "spacebar": 32, "del": 46, "ins": 45,
    "pgup": 33, "pgdn": 34, "pgdown": 34, "caps": 20, "bksp": 8,
})

# Modifier mask uses bit pairs (hypothesis: left/right variants).
MOD_SHIFT = 0x30000
MOD_CTRL = 0xC0000
MOD_ALT = 0x300000
MOD_BITS: list[tuple[int, str]] = [(MOD_SHIFT, "Shift"), (MOD_CTRL, "Ctrl"), (MOD_ALT, "Alt")]
MOD_BY_NAME: dict[str, int] = {"shift": MOD_SHIFT, "ctrl": MOD_CTRL, "control": MOD_CTRL, "alt": MOD_ALT}


def vk_for_name(name: str) -> int | None:
    return NAME_TO_VK.get(name.strip().lower())


def mods_mask(names: list[str]) -> int:
    """Combine modifier names ("shift"/"ctrl"/"alt") into the GFCC mask."""
    mask = 0
    for n in names:
        bits = MOD_BY_NAME.get(n.strip().lower())
        if bits is None:
            raise ValueError(f"unknown modifier {n!r} (expected shift/ctrl/alt)")
        mask |= bits
    return mask


def mods_note(m: int) -> str | None:
    names = [n for bits, n in MOD_BITS if m & bits]
    leftover = m & ~sum(b for b, _ in MOD_BITS)
    if leftover:
        names.append(f"+{leftover:#x}")
    return "+".join(names) if names else None
