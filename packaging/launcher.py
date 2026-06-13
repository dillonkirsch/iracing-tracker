"""PyInstaller entry point for the standalone .exe.

Kept tiny on purpose: all the real work lives in irtracker.gui.launch(), which
opens the native window (pywebview) or falls back to the browser.
"""
from irtracker.gui import launch

if __name__ == "__main__":
    raise SystemExit(launch())
