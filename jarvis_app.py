"""Entry point for packaged (PyInstaller) builds.

Double-clicking the packaged application has no command-line arguments, and
``main.py`` would then start the terminal voice loop instead of the GUI. This
entry point adds ``--gui`` for that case only, so the packaged app opens the
floating widget while explicit flags (``--autostart-enable``, ``--check``, ...)
still work.
"""

import sys

if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.append("--gui")
    from main import main

    raise SystemExit(main())
