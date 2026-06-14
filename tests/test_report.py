"""Comparison PDF report generation."""
from irtracker import report


def test_friendly_name():
    assert report.friendly_name("app.ini") == "Graphics & Display"
    assert report.friendly_name("rendererDX11Monitor.ini").startswith("Monitor")
    assert report.friendly_name("whatever.txt") == "whatever.txt"


def test_build_comparison_pdf_returns_pdf_bytes():
    files = [
        {"name": "app.ini", "body": "[Graphics]\n  width: 1920 -> 2560\n  newKey = 1  (added)"},
        {"name": "controls.cfg", "body": "  Throttle: axis 2 -> axis 3"},
    ]
    pdf = report.build_comparison_pdf("v0.1.0", "Now (live folder)", files)
    assert isinstance(pdf, (bytes, bytearray))
    assert bytes(pdf[:5]) == b"%PDF-"
    assert len(pdf) > 800


def test_build_comparison_pdf_handles_no_changes():
    pdf = report.build_comparison_pdf("A", "B", [])
    assert bytes(pdf[:5]) == b"%PDF-"
