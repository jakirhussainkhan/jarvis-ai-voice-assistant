"""Shared Arc Reactor artwork for the JARVIS window, tray, and generated icons.

The SVG source is used by the live GUI and by gen_icon.py so every platform uses
the same icon. The previous painter drawing stays available as a fallback.
"""

import math
import sys
from pathlib import Path

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtGui import (QBrush, QColor, QImage, QLinearGradient, QPainter,
                         QPainterPath, QPen, QRadialGradient)

# Palette: a deep charcoal tile with a cyan/azure glow.
_TILE_TOP = QColor("#25282E")
_TILE_MID = QColor("#181A1F")
_TILE_BOTTOM = QColor("#0B0C0F")
_GLOW = QColor(56, 189, 248)          # sky-400
_GLOW_DEEP = QColor(14, 165, 233)     # sky-500
_CORE = QColor(186, 230, 253)         # sky-200
_HOT = QColor(248, 252, 255)


def _source_art_path():
    """Find the shared SVG in a source checkout or a PyInstaller bundle."""
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
    """Render the shared Arc Reactor source at the requested pixel size."""
    size = max(1, int(size))
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)

    source_path = _source_art_path()
    if source_path is not None:
        try:
            renderer = QSvgRenderer(str(source_path))
            if renderer.isValid():
                renderer.render(painter, QRectF(0, 0, size, size))
                painter.end()
                return image
        except Exception:
            # Keep icon creation available for incomplete/older distributions.
            pass

    # Compatibility fallback for source packages without the SVG asset.
    draw_icon(painter, float(size))
    painter.end()
    return image

def draw_icon(painter, size):
    """Paint the icon into ``painter`` on a ``size`` x ``size`` canvas."""
    s = float(size)
    painter.setRenderHint(QPainter.Antialiasing, True)

    # --- rounded charcoal tile -------------------------------------------- #
    margin = s * 0.02
    tile = QRectF(margin, margin, s - 2 * margin, s - 2 * margin)
    radius = s * 0.235
    tile_path = QPainterPath()
    tile_path.addRoundedRect(tile, radius, radius)

    background = QLinearGradient(tile.topLeft(), tile.bottomRight())
    background.setColorAt(0.0, _TILE_TOP)
    background.setColorAt(0.5, _TILE_MID)
    background.setColorAt(1.0, _TILE_BOTTOM)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(background))
    painter.drawPath(tile_path)

    # A hairline highlight so the tile reads as glass rather than a flat square.
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QColor(125, 211, 252, 48), max(1.0, s * 0.006)))
    painter.drawPath(tile_path)
    painter.setPen(Qt.NoPen)

    center = QPointF(s / 2.0, s / 2.0)

    # --- ambient bloom behind the orb ------------------------------------- #
    bloom_radius = s * 0.47
    bloom = QRadialGradient(center, bloom_radius)
    bloom.setColorAt(0.0, QColor(_GLOW.red(), _GLOW.green(), _GLOW.blue(), 170))
    bloom.setColorAt(0.45, QColor(_GLOW_DEEP.red(), _GLOW_DEEP.green(),
                                  _GLOW_DEEP.blue(), 70))
    bloom.setColorAt(1.0, QColor(_GLOW_DEEP.red(), _GLOW_DEEP.green(),
                                 _GLOW_DEEP.blue(), 0))
    painter.setBrush(QBrush(bloom))
    painter.drawEllipse(center, bloom_radius, bloom_radius)

    # --- two faint orbit rings (the "neural" hint) ------------------------ #
    for scale, alpha in ((0.335, 95), (0.400, 48)):
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(125, 211, 252, alpha), max(1.0, s * 0.004)))
        painter.drawEllipse(center, s * scale, s * scale)
    painter.setPen(Qt.NoPen)

    # --- the glowing orb -------------------------------------------------- #
    orb_radius = s * 0.275
    orb = QRadialGradient(QPointF(s * 0.46, s * 0.44), orb_radius * 1.20)
    orb.setColorAt(0.0, _CORE)
    orb.setColorAt(0.50, _GLOW)
    orb.setColorAt(1.0, QColor("#0C4A6E"))
    painter.setBrush(QBrush(orb))
    painter.drawEllipse(center, orb_radius, orb_radius)

    # A glass glint on the upper-left of the orb.
    glint = QRadialGradient(QPointF(s * 0.40, s * 0.37), s * 0.13)
    glint.setColorAt(0.0, QColor(255, 255, 255, 150))
    glint.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.setBrush(QBrush(glint))
    painter.drawEllipse(center, orb_radius, orb_radius)

    # --- the stylised "J" ------------------------------------------------- #
    j = QPainterPath()
    j.moveTo(s * 0.590, s * 0.315)
    j.lineTo(s * 0.590, s * 0.625)
    j.cubicTo(s * 0.590, s * 0.700, s * 0.525, s * 0.742, s * 0.458, s * 0.735)
    j.cubicTo(s * 0.392, s * 0.728, s * 0.352, s * 0.682, s * 0.352, s * 0.625)

    # Soft cyan halo, then the bright stroke: the letter looks lit from within.
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QColor(186, 230, 253, 110), max(1.5, s * 0.120),
                        Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.drawPath(j)
    painter.setPen(QPen(_HOT, max(1.0, s * 0.062),
                        Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.drawPath(j)
    painter.setPen(Qt.NoPen)

    # --- tiny orbit satellites -------------------------------------------- #
    for angle, dot_scale in ((math.radians(-38), 0.020),
                             (math.radians(146), 0.014)):
        dot = QPointF(center.x() + s * 0.400 * math.cos(angle),
                      center.y() + s * 0.400 * math.sin(angle))
        painter.setBrush(QBrush(QColor(224, 242, 254, 235)))
        painter.drawEllipse(dot, s * dot_scale, s * dot_scale)
