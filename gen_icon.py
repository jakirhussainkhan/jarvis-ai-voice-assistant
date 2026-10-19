"""Generate the Jarvis application icons in ``assets/``.

The artwork lives in :mod:`icon_art`, the same code the live window and tray use,
so regenerating the PNGs can never make the launcher icon drift from the GUI.

Usage::

    ./venv/bin/python gen_icon.py

Writes ``assets/jarvis-{32,48,64,128,256}.png``. The 48 px size is kept because
some desktop environments look for it on their panel/task switcher.
"""

import os
import sys
from pathlib import Path

# Icon rendering is pure raster work; keep it headless even on a server.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtGui import QGuiApplication  # noqa: E402

from icon_art import render_icon  # noqa: E402

SIZES = (32, 48, 64, 128, 256)
ASSETS = Path(__file__).resolve().parent / "assets"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    app = QGuiApplication.instance() or QGuiApplication(argv or [])
    ASSETS.mkdir(parents=True, exist_ok=True)

    written = 0
    for size in SIZES:
        target = ASSETS / f"jarvis-{size}.png"
        if not render_icon(size).save(str(target), "PNG"):
            print(f"Jarvis: could not write {target}", flush=True)
            return 1
        print(f"wrote {target}", flush=True)
        written += 1

    print(f"Generated {written} icons in {ASSETS}", flush=True)
    # Keep the reference alive for the duration of the run on some platforms.
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
