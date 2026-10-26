"""Generate shared JARVIS PNG icons and a multi-resolution Windows ICO.

The Arc Reactor SVG is rendered through icon_art.py, which is also used by the
live GUI and system tray. Running this script refreshes every packaged icon.
"""

import os
import sys
from pathlib import Path

# Icon rendering is pure raster work; keep it headless even on a server.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtGui import QGuiApplication  # noqa: E402
from PIL import Image  # noqa: E402

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

    ico_path = ASSETS / "jarvis.ico"
    with Image.open(ASSETS / "jarvis-256.png") as source:
        source.convert("RGBA").save(
            ico_path,
            format="ICO",
            sizes=[
                (16, 16), (24, 24), (32, 32), (48, 48),
                (64, 64), (128, 128), (256, 256),
            ],
        )
    print(f"wrote {ico_path}", flush=True)
    print(f"Generated {written} PNG icons and one ICO in {ASSETS}", flush=True)
    # Keep the reference alive for the duration of the run on some platforms.
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
