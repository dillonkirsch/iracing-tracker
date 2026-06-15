"""PyInstaller entry point for the standalone .exe.

No arguments -> open the GUI (the normal double-click case). Any arguments are
treated as CLI commands, so the one packaged binary serves both roles — e.g. the
background watcher and autostart launch "<exe> watcher run --quiet".
"""
import sys

if __name__ == "__main__":
    if sys.argv[1:]:
        from irtracker.cli import main
        sys.exit(main(sys.argv[1:]))
    from irtracker.gui import launch
    sys.exit(launch())
