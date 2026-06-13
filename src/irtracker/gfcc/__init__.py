"""GFCC codec for iRacing controls.cfg: decode to JSON, byte-identical rebuild,
and keyboard-binding patch mode."""

from irtracker.gfcc.codec import GfccError, parse, build, decode_bytes, verify_roundtrip

__all__ = ["GfccError", "parse", "build", "decode_bytes", "verify_roundtrip"]
