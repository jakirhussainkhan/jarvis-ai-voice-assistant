"""Render the single canonical JARVIS Arc Reactor app icon.

The SVG in assets/jarvis-reactor-source.svg is the only supported icon artwork.
If it is missing or invalid, fail loudly instead of resurrecting a legacy logo.
"""

import sys
from pathlib import Path

from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import QImage, QPainter
from PyQt5.QtSvg import QSvgRenderer


def _source_art_path():
    """Find the canonical SVG in a source checkout or a PyInstaller bundle."""
    candidates = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(Path(bundle_root) / "assets" / "jarvis-reactor-source.svg")
    candidates.append(
        Path(__file__).resolve().parent / "assets" / "jarvis-reactor-source.svg"
    )
    for path in candidates:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def render_icon(size):
    """Render the canonical Arc Reactor SVG at the requested pixel size."""
    size = max(1, int(size))
    source_path = _source_art_path()
    if source_path is None:
        raise FileNotFoundError(
            "Required JARVIS Arc Reactor artwork is missing: "
            "assets/jarvis-reactor-source.svg. Refusing to display a legacy icon."
        )

    renderer = QSvgRenderer(str(source_path))
    if not renderer.isValid():
        raise ValueError(
            f"Invalid JARVIS Arc Reactor SVG artwork: {source_path}. "
            "Refusing to display a legacy icon."
        )

    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        renderer.render(painter, QRectF(0, 0, size, size))
    finally:
        painter.end()
    return image
