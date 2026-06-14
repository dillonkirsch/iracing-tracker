"""PDF report generation (comparison report).

Uses fpdf2 (pure-Python, core fonts only — bundles cleanly into the .exe). The
report leads with a summary of what changed, then per-file colour-coded detail.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

FILE_LABELS = {
    "app.ini": "Graphics & Display",
    "controls.cfg": "Controls & Force Feedback",
    "joyCalib.yaml": "Wheel & Pedal Calibration",
    "core.ini": "Core Game Settings",
    "fueldata.ini": "Fuel Data",
    "camera.ini": "Camera Settings",
}

# RGB
_HEADER = (40, 90, 200)
_ADD = (22, 140, 70)
_DEL = (200, 50, 50)
_NORMAL = (45, 50, 60)
_DIM = (110, 116, 130)


def friendly_name(name: str) -> str:
    if name in FILE_LABELS:
        return FILE_LABELS[name]
    if name.lower().startswith("rendererdx11"):
        return "Monitor / Graphics Renderer"
    return name


def _safe(text: str) -> str:
    """fpdf2 core fonts are latin-1 only; drop anything that won't encode."""
    return text.encode("latin-1", "replace").decode("latin-1")


def _line_color(line: str) -> tuple[int, int, int]:
    t = line.lstrip()
    if t.startswith("["):
        return _HEADER
    if t.startswith("+") or "(added)" in t:
        return _ADD
    if t.startswith("-") or "(removed" in t:
        return _DEL
    return _NORMAL


def build_comparison_pdf(label_a: str, label_b: str,
                         files: list[dict], logo_path: Path | None = None) -> bytes:
    """files: [{"name": str, "body": str}]. Returns PDF bytes."""
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    pdf = FPDF(format="Letter", unit="mm")
    pdf.set_auto_page_break(True, margin=16)
    pdf.set_title("iRacing Config - Comparison Report")
    pdf.add_page()
    nl = dict(new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    if logo_path and Path(logo_path).exists():
        try:
            pdf.image(str(logo_path), x=pdf.l_margin, y=12, w=16)
            pdf.set_x(pdf.l_margin + 20)
        except Exception:
            pass
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(20, 24, 32)
    pdf.cell(0, 9, "iRacing Config - Comparison", **nl)
    pdf.set_x(pdf.l_margin + (20 if logo_path else 0))
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(*_DIM)
    pdf.cell(0, 6, _safe(f"{label_a}   ->   {label_b}"), **nl)
    pdf.set_x(pdf.l_margin + (20 if logo_path else 0))
    pdf.cell(0, 6, "Generated " + datetime.now().strftime("%Y-%m-%d %H:%M"), **nl)
    pdf.ln(6)

    # ---- summary first (the change, at the top) ----
    pdf.set_text_color(20, 24, 32)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "Summary of changes", **nl)
    pdf.set_font("Helvetica", "", 11)
    if not files:
        pdf.set_text_color(*_DIM)
        pdf.cell(0, 7, "No differences between these two backups.", **nl)
    else:
        pdf.set_text_color(*_NORMAL)
        plural = "file" if len(files) == 1 else "files"
        pdf.cell(0, 7, _safe(f"{len(files)} {plural} changed:"), **nl)
        for f in files:
            pdf.set_x(pdf.l_margin + 4)
            pdf.cell(0, 6, _safe(f"- {friendly_name(f['name'])}  ({f['name']})"), **nl)
    pdf.ln(4)

    # ---- per-file detail ----
    for f in files:
        if pdf.will_page_break(24):
            pdf.add_page()
        pdf.set_draw_color(225, 228, 235)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(*_HEADER)
        pdf.cell(0, 8, _safe(f"{friendly_name(f['name'])}  ({f['name']})"), **nl)
        pdf.set_font("Courier", "", 9)
        for line in (f["body"] or "").splitlines() or ["(no detail)"]:
            pdf.set_text_color(*_line_color(line))
            pdf.multi_cell(0, 4.6, _safe(line) or " ", **nl)
        pdf.ln(3)

    return bytes(pdf.output())
