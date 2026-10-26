"""Instrument-panel widgets for the JARVIS command HUD.

The main window (``gui.py``) owns the voice pipeline, the agent worker and the
tray; this module owns only presentation.  Every panel is painted with the same
small design system (deep obsidian surfaces, cyan hairlines, amber annotations)
so the HUD stays coherent as panels are shown or folded away on smaller windows.

The widgets here never invent data:

* SYSTEM reads ``psutil`` directly and shows ``—``/``UNAVAILABLE`` when a
  metric cannot be read.
* GITHUB reads the local repository with ``git`` and only calls the GitHub API
  when ``JARVIS_GITHUB_TOKEN``/``GITHUB_TOKEN`` is present.
* INBOX reports that no mail source is configured rather than displaying
  fictitious messages.
* TRANSCRIPT and ACTIVITY are fed by the real ``Memory`` store and the real
  event stream from ``gui.JarvisWidget``.

No backend module imports this file.
"""

from __future__ import annotations

import html
import json
import math
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

from PyQt5.QtCore import (QEasingCurve, QObject, QPointF, QPropertyAnimation,
                          QRectF, Qt, QTimer, pyqtProperty,
                          pyqtSignal)
from PyQt5.QtGui import (QColor, QFont, QLinearGradient, QPainter,
                         QPainterPath, QPen, QRadialGradient)
from PyQt5.QtWidgets import (QAbstractScrollArea, QFrame, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QScrollArea, QSizePolicy,
                             QTextEdit, QVBoxLayout, QWidget)

import config

try:                                         # optional telemetry dependency
    import psutil
except Exception:                            # pragma: no cover - optional
    psutil = None


# -- design tokens ---------------------------------------------------------- #
BG = "#03070A"
BG_DEEP = "#020507"
PANEL_TOP = QColor(8, 19, 27, 238)
PANEL_BOTTOM = QColor(3, 9, 13, 246)
SURFACE = "#07121A"
BORDER = QColor(20, 74, 86, 170)
BORDER_SOFT = QColor(20, 74, 86, 92)
CYAN = QColor(34, 230, 255)
CYAN_SOFT = QColor(120, 240, 255)
AMBER = QColor(255, 181, 71)
AMBER_SOFT = QColor(255, 210, 140)
TEXT = QColor(217, 246, 250)
TEXT_SECONDARY = QColor(127, 179, 191)
TEXT_MUTED = QColor(69, 107, 117)
ROSE = QColor(255, 92, 108)
GREEN = QColor(0, 255, 136)
VIOLET = QColor(190, 120, 255)

MONO = "DejaVu Sans Mono"
SANS = "DejaVu Sans"

# State names are the exact strings used by gui.py.  Keeping the mapping here
# lets panels tint their LEDs without importing the GUI module (no cycle).
IDLE = "Ready"
LISTENING = "Listening..."
THINKING = "Thinking..."
SPEAKING = "Speaking..."
MUTED = "Microphone muted"
ERROR = "Something went wrong"

STATE_COLORS = {
    IDLE: CYAN,
    LISTENING: GREEN,
    THINKING: AMBER,
    SPEAKING: VIOLET,
    MUTED: QColor(100, 116, 139),
    ERROR: ROSE,
}


def state_color(state):
    """Return the accent for an assistant state (cyan when unknown)."""
    return STATE_COLORS.get(state, CYAN)


# -- small helpers ---------------------------------------------------------- #

def hud_font(size, weight=QFont.Normal, spacing=1.2, family=MONO):
    """A tracked monospace font (Qt ignores CSS letter-spacing)."""
    font = QFont(family)
    font.setPixelSize(max(1, int(round(size))))
    font.setWeight(weight)
    if spacing:
        font.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    return font


def _rgba(color, alpha):
    return QColor(color.red(), color.green(), color.blue(),
                  max(0, min(255, int(alpha))))


def _short(text, limit):
    text = " ".join(str(text or "").split())
    if limit <= 0:
        return ""
    return text if len(text) <= limit else text[:max(1, limit - 1)] + "…"


def _cut_path(rect, cut=10.0, notch=0.0):
    """A rectangle with clipped corners; the signature HUD frame shape."""
    cut = max(0.0, min(cut, min(rect.width(), rect.height()) / 2.0))
    path = QPainterPath()
    path.moveTo(rect.left() + cut, rect.top())
    if notch:
        path.lineTo(rect.center().x() - notch, rect.top())
        path.lineTo(rect.center().x(), rect.top() + notch * 0.55)
        path.lineTo(rect.center().x() + notch, rect.top())
    path.lineTo(rect.right() - cut, rect.top())
    path.lineTo(rect.right(), rect.top() + cut)
    path.lineTo(rect.right(), rect.bottom() - cut)
    path.lineTo(rect.right() - cut, rect.bottom())
    path.lineTo(rect.left() + cut, rect.bottom())
    path.lineTo(rect.left(), rect.bottom() - cut)
    path.lineTo(rect.left(), rect.top() + cut)
    path.closeSubpath()
    return path


def _draw_ticks(painter, x, y, width, count, color, alpha=90):
    """A row of tiny measurement ticks along the top of a bar."""
    if count <= 1:
        return
    painter.setPen(QPen(_rgba(color, alpha), 1.0))
    step = width / float(count - 1)
    for index in range(count):
        height = 3.0 if index % 5 == 0 else 1.6
        px = x + index * step
        painter.drawLine(QPointF(px, y), QPointF(px, y - height))


def _format_bytes(rate):
    try:
        rate = max(0.0, float(rate))
    except (TypeError, ValueError):
        return "—"
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if rate < 1024.0 or unit == "GB/s":
            return f"{rate:.1f} {unit}" if rate < 100 else f"{rate:.0f} {unit}"
        rate /= 1024.0
    return "—"


def _format_uptime(seconds):
    try:
        seconds = max(0, int(seconds))
    except (TypeError, ValueError):
        return "—"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours:02d}:{minutes:02d}"
    return f"{hours:02d}:{minutes:02d}"


# -- frame + header --------------------------------------------------------- #

class HudPanel(QFrame):
    """A clipped-corner plate with a hairline border and amber corner tick."""

    def __init__(self, parent=None, cut=9.0, notch=0.0):
        super().__init__(parent)
        self._cut = cut
        self._notch = notch
        self._accent = QColor(CYAN)
        self._alert = False
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setFrameShape(QFrame.NoFrame)

    def set_accent(self, color):
        self._accent = QColor(color)
        self.update()

    def set_alert(self, on):
        self._alert = bool(on)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.7, 0.7, -0.7, -0.7)
        path = _cut_path(rect, self._cut, self._notch)

        fill = QLinearGradient(rect.topLeft(), rect.bottomRight())
        fill.setColorAt(0.0, PANEL_TOP)
        fill.setColorAt(0.55, QColor(5, 13, 19, 242))
        fill.setColorAt(1.0, PANEL_BOTTOM)
        painter.fillPath(path, fill)

        # A faint inner scan pattern keeps large plates from looking flat.
        painter.save()
        painter.setClipPath(path)
        painter.setPen(QPen(QColor(34, 230, 255, 5), 1.0))
        y = rect.top() + 7
        while y < rect.bottom():
            painter.drawLine(QPointF(rect.left(), y),
                             QPointF(rect.right(), y))
            y += 7
        painter.restore()

        border = 96 if self._alert else 52
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(_rgba(self._accent, border), 1.0))
        painter.drawPath(path)

        # Corner brackets: cyan at top-left/bottom-right, amber at top-right.
        painter.setPen(QPen(_rgba(self._accent, 190), 1.3))
        length = 13.0
        painter.drawLine(QPointF(rect.left() + 1, rect.top() + 1),
                         QPointF(rect.left() + length, rect.top() + 1))
        painter.drawLine(QPointF(rect.left() + 1, rect.top() + 1),
                         QPointF(rect.left() + 1, rect.top() + length))
        painter.drawLine(QPointF(rect.right() - length, rect.bottom() - 1),
                         QPointF(rect.right() - 1, rect.bottom() - 1))
        painter.drawLine(QPointF(rect.right() - 1, rect.bottom() - length),
                         QPointF(rect.right() - 1, rect.bottom() - 1))

        painter.setPen(QPen(_rgba(AMBER, 180), 1.3))
        painter.drawLine(QPointF(rect.right() - 8, rect.top() + 1),
                         QPointF(rect.right() - 1, rect.top() + 1))
        painter.drawLine(QPointF(rect.right() - 1, rect.top() + 1),
                         QPointF(rect.right() - 1, rect.top() + 8))

        # Hairline sheen along the top edge.
        sheen = QLinearGradient(rect.left(), rect.top(),
                                rect.right(), rect.top())
        sheen.setColorAt(0.0, _rgba(self._accent, 0))
        sheen.setColorAt(0.18, _rgba(self._accent, 92))
        sheen.setColorAt(0.62, _rgba(self._accent, 18))
        sheen.setColorAt(1.0, _rgba(AMBER, 46))
        painter.setPen(QPen(sheen, 1.0))
        painter.drawLine(QPointF(rect.left() + 10, rect.top() + 1),
                         QPointF(rect.right() - 10, rect.top() + 1))
        painter.end()


class PanelHeader(QWidget):
    """Title / live tag / technical index row used by every panel."""

    def __init__(self, title, tag="", parent=None, accent=CYAN):
        super().__init__(parent)
        self._accent = QColor(accent)
        self.setFixedHeight(30)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(7)
        self.title = QLabel(str(title).upper())
        self.title.setStyleSheet("background:transparent;")
        self.title.setFont(hud_font(9.6, QFont.Bold, 2.1))
        self.title.setStyleSheet(
            f"color:{TEXT.name()}; background:transparent;"
        )
        row.addWidget(self.title)
        row.addStretch(1)
        self.tag = QLabel(str(tag).upper())
        self.tag.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.tag.setFont(hud_font(7.0, QFont.DemiBold, 1.0))
        self.tag.setStyleSheet(
            f"color:{CYAN_SOFT.name()}; background:transparent;"
        )
        row.addWidget(self.tag)

    def set_tag(self, text, color=None):
        self.tag.setText(str(text).upper())
        if color is not None:
            self.tag.setStyleSheet(
                f"color:{QColor(color).name()}; background:transparent;"
            )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        y = self.height() - 1.5
        painter.setPen(QPen(QColor(20, 74, 86, 130), 1.0))
        painter.drawLine(QPointF(0, y), QPointF(self.width(), y))
        painter.setPen(QPen(_rgba(self._accent, 190), 1.4))
        painter.drawLine(QPointF(0, y), QPointF(22, y))
        for index in range(5):
            x = self.width() - 4 - index * 6
            painter.setPen(QPen(QColor(255, 181, 71, 90 if index % 2 else 140),
                                1.0))
            painter.drawLine(QPointF(x, y), QPointF(x, y - 4))
        painter.end()


# -- system metrics --------------------------------------------------------- #

class HudMetric(QWidget):
    """Label + live value + segmented bar; no invented values."""

    def __init__(self, label, accent=CYAN, parent=None):
        super().__init__(parent)
        self.label = str(label).upper()
        self._accent = QColor(accent)
        self._value = 0.0
        self._target = 0.0
        self._display = "—"
        self.setMinimumHeight(38)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._anim = QPropertyAnimation(self, b"value", self)
        self._anim.setDuration(620)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def _get_value(self):
        return self._value

    def _set_value(self, value):
        self._value = max(0.0, min(1.0, float(value)))
        self.update()

    value = pyqtProperty(float, fget=_get_value, fset=_set_value)

    def set_value(self, value, display=None, animate=True):
        try:
            target = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            target = 0.0
        self._target = target
        self._display = "—" if display is None else str(display)
        if not animate or abs(target - self._value) < 0.004:
            self._anim.stop()
            self._value = target
            self.update()
            return
        self._anim.stop()
        self._anim.setStartValue(self._value)
        self._anim.setEndValue(target)
        self._anim.start()

    def set_unavailable(self, text="UNAVAILABLE"):
        self._anim.stop()
        self._value = 0.0
        self._display = str(text).upper()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width, height = self.width(), self.height()
        if width <= 2 or height <= 2:
            painter.end()
            return

        painter.setFont(hud_font(7.4, QFont.DemiBold, 1.25))
        painter.setPen(QColor(TEXT_SECONDARY))
        painter.drawText(1, 11, self.label)

        painter.setFont(hud_font(9.0, QFont.Bold, 0.7))
        painter.setPen(QColor(TEXT))
        painter.drawText(QRectF(0, 0, width - 1, 14),
                         Qt.AlignRight | Qt.AlignVCenter, self._display)

        text_width = painter.fontMetrics().horizontalAdvance(self._display)
        if text_width > 2:
            painter.setPen(QPen(_rgba(AMBER, 110), 1.0))
            painter.drawLine(QPointF(width - text_width - 3, 12),
                             QPointF(width - 2, 12))

        track_y = height - 11.0
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(7, 17, 23))
        painter.drawRoundedRect(QRectF(1, track_y, width - 2, 3.0), 1.5, 1.5)

        fill_w = max(0.0, (width - 2) * self._value)
        if fill_w > 0.6:
            grad = QLinearGradient(QPointF(1, track_y),
                                   QPointF(1 + fill_w, track_y))
            grad.setColorAt(0.0, _rgba(self._accent, 110))
            grad.setColorAt(1.0, _rgba(self._accent, 245))
            painter.setBrush(grad)
            painter.drawRoundedRect(QRectF(1, track_y, fill_w, 3.0), 1.5, 1.5)

        _draw_ticks(painter, 1, track_y - 2.0, width - 2, 13,
                    self._accent, 60)
        painter.end()


class HudSparkline(QWidget):
    """A real CPU-load history; it never draws a synthetic waveform."""

    def __init__(self, samples=60, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumHeight(56)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._values = deque(maxlen=max(2, int(samples)))

    def set_value(self, percent):
        try:
            value = max(0.0, min(100.0, float(percent)))
        except (TypeError, ValueError):
            return
        self._values.append(value)
        self.update()

    def set_unavailable(self):
        self._values.clear()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if rect.width() <= 4 or rect.height() <= 4:
            painter.end()
            return

        painter.fillRect(rect, QColor(4, 13, 18, 170))
        painter.setPen(QPen(QColor(20, 74, 86, 90), 1.0))
        painter.drawRect(rect)
        for fraction in (0.25, 0.50, 0.75):
            y = rect.top() + rect.height() * fraction
            painter.setPen(QPen(QColor(20, 74, 86, 34), 1.0))
            painter.drawLine(QPointF(rect.left(), y),
                             QPointF(rect.right(), y))
        # 80% load reference, an amber warning datum.
        warn_y = rect.top() + rect.height() * 0.20
        painter.setPen(QPen(QColor(255, 181, 71, 58), 1.0, Qt.DashLine))
        painter.drawLine(QPointF(rect.left(), warn_y),
                         QPointF(rect.right(), warn_y))

        values = list(self._values)
        if len(values) < 2:
            painter.setFont(hud_font(6.2, QFont.DemiBold, 0.9))
            painter.setPen(QColor(TEXT_MUTED))
            painter.drawText(rect, Qt.AlignCenter, "AWAITING SAMPLES")
            painter.end()
            return

        step = rect.width() / float(len(values) - 1)
        points = [
            QPointF(rect.left() + index * step,
                    rect.bottom() - (value / 100.0) * rect.height())
            for index, value in enumerate(values)
        ]
        area = QPainterPath()
        area.moveTo(points[0].x(), rect.bottom())
        for point in points:
            area.lineTo(point)
        area.lineTo(points[-1].x(), rect.bottom())
        area.closeSubpath()
        fill = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        fill.setColorAt(0.0, QColor(34, 230, 255, 70))
        fill.setColorAt(1.0, QColor(34, 230, 255, 4))
        painter.fillPath(area, fill)

        line = QPainterPath()
        line.moveTo(points[0])
        for point in points[1:]:
            line.lineTo(point)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(CYAN), 1.2))
        painter.drawPath(line)

        last = points[-1]
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(CYAN))
        painter.drawEllipse(last, 1.8, 1.8)
        painter.end()


class SystemPanel(HudPanel):
    """Real CPU / memory / storage / network telemetry."""

    def __init__(self, parent=None):
        super().__init__(parent, cut=10.0)
        self._state = IDLE
        self._voice = "—"
        self._wake = False
        self._mode = True
        self.setMinimumHeight(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 12, 15, 12)
        layout.setSpacing(0)
        self.header = PanelHeader("System", "Live")
        layout.addWidget(self.header)
        layout.addSpacing(7)

        self.cpu = HudMetric("Core load")
        self.memory = HudMetric("Memory")
        self.disk = HudMetric("Storage")
        self.network = HudMetric("Network")
        for metric in (self.cpu, self.memory, self.disk, self.network):
            layout.addWidget(metric)
        layout.addSpacing(7)

        rule = QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet("background:rgba(20,74,86,110); border:none;")
        layout.addWidget(rule)
        layout.addSpacing(9)

        self.readout = QWidget()
        self.readout.setAttribute(Qt.WA_TranslucentBackground, True)
        grid = QHBoxLayout(self.readout)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)
        self.uptime_value = self._readout(grid, "UPTIME")
        self.proc_value = self._readout(grid, "PROCS")
        self.power_value = self._readout(grid, "POWER")
        self.latency_value = self._readout(grid, "LATENCY")
        layout.addWidget(self.readout)
        layout.addSpacing(10)

        self.load_caption = QLabel("CORE LOAD  //  LAST 60 SAMPLES")
        self.load_caption.setFont(hud_font(6.2, QFont.DemiBold, 1.1))
        self.load_caption.setStyleSheet(
            f"color:{TEXT_MUTED.name()}; background:transparent;"
        )
        layout.addWidget(self.load_caption)
        layout.addSpacing(3)
        self.spark = HudSparkline()
        layout.addWidget(self.spark)
        layout.addSpacing(9)

        self.link = QLabel("")
        self.link.setFont(hud_font(7.0, QFont.DemiBold, 1.1))
        self.link.setStyleSheet(
            f"color:{TEXT_MUTED.name()}; background:transparent;"
        )
        self.link.setWordWrap(True)
        layout.addWidget(self.link)
        layout.addStretch(1)

        self._uptime = 0
        self._latency = None
        self._refresh_link()

    @staticmethod
    def _readout(parent_layout, label):
        box = QWidget()
        box.setAttribute(Qt.WA_TranslucentBackground, True)
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(1)
        name = QLabel(label)
        name.setFont(hud_font(6.4, QFont.DemiBold, 1.2))
        name.setStyleSheet(
            f"color:{TEXT_MUTED.name()}; background:transparent;"
        )
        value = QLabel("—")
        value.setFont(hud_font(9.2, QFont.Bold, 0.5))
        value.setStyleSheet(
            f"color:{TEXT.name()}; background:transparent;"
        )
        column.addWidget(name)
        column.addWidget(value)
        parent_layout.addWidget(box, 1)
        return value

    def _refresh_link(self):
        wake = "WAKE ON" if self._wake else "WAKE OFF"
        mode = "AUTO ON" if self._mode else "AUTO OFF"
        self.link.setText(
            f"{wake}   ·   {mode}   ·   VOICE {self._voice}"
        )

    def set_mode(self, on):
        self._mode = bool(on)
        self._refresh_link()

    def set_wake(self, on):
        self._wake = bool(on)
        self._refresh_link()

    def set_voice(self, name):
        self._voice = str(name or "—")
        self._refresh_link()

    def note_state(self, state):
        self._state = state
        color = state_color(state)
        self.header.set_tag(
            "Live" if state in (LISTENING, THINKING, SPEAKING) else "Idle",
            color,
        )
        self.set_alert(state == ERROR)

    def note_energy(self, _energy):
        # The system panel does not need a per-frame repaint; the reactor and
        # waveform carry the live signal. Kept as a no-op compatibility seam.
        return

    def update_system(self, cpu=None, memory=None, disk=None, net_rate=None,
                      uptime=None, processes=None, battery=None,
                      latency_ms=None, temp_c=None):
        self._uptime = uptime
        self._latency = latency_ms
        if cpu is None:
            self.cpu.set_unavailable()
            self.spark.set_unavailable()
        else:
            self.cpu.set_value(float(cpu) / 100.0, f"{float(cpu):.0f}%")
            self.spark.set_value(cpu)
        if memory is None:
            self.memory.set_unavailable()
        else:
            self.memory.set_value(float(memory) / 100.0, f"{float(memory):.0f}%")
        if disk is None:
            self.disk.set_unavailable()
        else:
            self.disk.set_value(float(disk) / 100.0, f"{float(disk):.0f}%")
        if net_rate is None:
            self.network.set_value(0.0, "—")
        else:
            # 2 MB/s fills the rail; the numeric value stays honest.
            self.network.set_value(min(1.0, float(net_rate) / (2 * 1024 * 1024)),
                                   _format_bytes(net_rate))
        self.uptime_value.setText(_format_uptime(uptime))
        self.proc_value.setText(
            "—" if processes is None else f"{int(processes)}"
        )
        if battery is None:
            self.power_value.setText("AC" if psutil is not None else "—")
        else:
            self.power_value.setText(str(battery))
        self.latency_value.setText(
            "—" if latency_ms is None else f"{float(latency_ms):.0f} ms"
        )
        if temp_c is not None:
            self.setToolTip(f"CPU temperature: {float(temp_c):.1f} °C")
        self.update()

    # Compatibility with the previous TelemetryPanel call sites.  Only the
    # three metrics those callers actually supply are touched, so a disk,
    # network or battery reading from update_system() is never clobbered.
    def update_metrics(self, cpu, ram, state, energy, uptime):
        self.note_state(state)
        self.note_energy(energy)
        if cpu is None:
            self.cpu.set_unavailable()
        else:
            self.cpu.set_value(float(cpu) / 100.0, f"{float(cpu):.0f}%")
        if ram is None:
            self.memory.set_unavailable()
        else:
            self.memory.set_value(float(ram) / 100.0, f"{float(ram):.0f}%")
        self.uptime_value.setText(_format_uptime(uptime))
        self.update()


class InboxPanel(HudPanel):
    """Honest 'not connected' state for a source the backend does not have."""

    def __init__(self, parent=None):
        super().__init__(parent, cut=9.0)
        self.setMinimumHeight(126)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 12, 15, 12)
        layout.setSpacing(0)

        self.header = PanelHeader("Inbox", "No source", accent=AMBER)
        self.header.set_tag("No source", AMBER)
        layout.addWidget(self.header)
        layout.addSpacing(9)

        self.count = QLabel("—")
        self.count.setFont(hud_font(24, QFont.Bold, 1.0))
        self.count.setStyleSheet(
            f"color:{TEXT.name()}; background:transparent;"
        )
        layout.addWidget(self.count)

        self.note = QLabel("MAIL LINK NOT CONFIGURED")
        self.note.setFont(hud_font(7.4, QFont.Bold, 1.0))
        self.note.setStyleSheet(
            f"color:{AMBER.name()}; background:transparent;"
        )
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        layout.addSpacing(4)

        self.detail = QLabel(
            "Connect an email MCP server to stream real messages."
        )
        self.detail.setFont(hud_font(6.8, QFont.Normal, 0.5, SANS))
        self.detail.setStyleSheet(
            f"color:{TEXT_MUTED.name()}; background:transparent;"
        )
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        layout.addStretch(1)

    def set_unavailable(self, reason="No mail source configured"):
        self.count.setText("—")
        self.note.setText(reason.upper())
        self.header.set_tag("Offline", AMBER)

    def set_messages(self, messages):
        """Future-facing hook: only real messages may be passed here."""
        if not messages:
            self.set_unavailable()
            return
        self.count.setText(str(len(messages)))
        self.note.setText("UNREAD")
        self.header.set_tag("Live", CYAN)


class _GithubProbe(QObject):
    """Read local git state and, when a token is present, GitHub notifications.

    The work runs on a daemon ``threading.Thread`` rather than a QThread: a
    stuck network probe then can never abort application shutdown with
    "QThread: Destroyed while thread is still running".  The result signal is
    emitted from that thread and Qt delivers it to the GUI thread.
    """

    result = pyqtSignal(dict)

    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.root = str(root)
        self._thread = None

    def start(self):
        if self.isRunning():
            return
        self._thread = threading.Thread(
            target=self._run, name="jarvis-github-probe", daemon=True
        )
        self._thread.start()

    def isRunning(self):
        return self._thread is not None and self._thread.is_alive()

    def stop(self):
        # A daemon thread is reaped by process exit; dropping the reference is
        # enough and never blocks the quit path on a slow network call.
        self._thread = None

    @staticmethod
    def _git(root, *args):
        try:
            proc = subprocess.run(
                ["git", "-C", root, *args],
                capture_output=True, text=True, timeout=4,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        if proc.returncode != 0:
            return ""
        return proc.stdout.strip()

    def _local(self):
        info = {
            "repo": Path(self.root).name or "repository",
            "branch": "—",
            "dirty": None,
            "commit": "—",
            "subject": "",
            "when": "",
            "remote": "",
            "live": False,
            "notifications": None,
            "stars": None,
            "issues": None,
            "error": "",
        }
        if not shutil.which("git"):
            info["error"] = "git not installed"
            return info
        info["branch"] = self._git(
            self.root, "rev-parse", "--abbrev-ref", "HEAD"
        ) or "—"
        status = self._git(self.root, "status", "--porcelain")
        info["dirty"] = len([line for line in status.splitlines() if line])
        last = self._git(
            self.root, "log", "-1", "--format=%h%x1f%s%x1f%cr"
        )
        if last:
            parts = last.split("\x1f")
            info["commit"] = parts[0] if parts else "—"
            info["subject"] = parts[1] if len(parts) > 1 else ""
            info["when"] = parts[2] if len(parts) > 2 else ""
        remote = self._git(self.root, "remote", "get-url", "origin")
        info["remote"] = remote
        owner = repo = ""
        if "github.com" in remote:
            tail = remote.split("github.com", 1)[1].lstrip(":/")
            tail = tail[:-4] if tail.endswith(".git") else tail
            bits = [part for part in tail.split("/") if part]
            if len(bits) >= 2:
                owner, repo = bits[0], bits[1]
        info["owner"], info["repo_name"] = owner, repo
        return info

    @staticmethod
    def _api(url, token):
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "JARVIS-HUD",
            },
        )
        with urllib.request.urlopen(request, timeout=6) as response:
            return json.loads(response.read().decode("utf-8", "replace"))

    def _run(self):
        info = self._local()
        token = (os.getenv("JARVIS_GITHUB_TOKEN")
                 or os.getenv("GITHUB_TOKEN") or "").strip()
        owner = info.get("owner") or ""
        repo = info.get("repo_name") or ""
        if token and owner and repo:
            try:
                notes = self._api(
                    "https://api.github.com/notifications?per_page=50", token
                )
                info["notifications"] = len(notes) if isinstance(notes, list) else 0
                meta = self._api(
                    f"https://api.github.com/repos/{owner}/{repo}", token
                )
                if isinstance(meta, dict):
                    info["stars"] = meta.get("stargazers_count")
                    info["issues"] = meta.get("open_issues_count")
                info["live"] = True
            except urllib.error.HTTPError as exc:
                info["error"] = (
                    "token rejected" if exc.code in (401, 403)
                    else f"HTTP {exc.code}"
                )
            except (urllib.error.URLError, TimeoutError, ValueError):
                info["error"] = "remote unreachable"
            except Exception:
                info["error"] = "remote unavailable"
        elif token and not (owner and repo):
            info["error"] = "no GitHub origin"
        try:
            self.result.emit(info)
        except RuntimeError:
            # The HUD was torn down while the probe was still in flight.
            pass


class GithubPanel(HudPanel):
    """Local repository state, plus remote notifications when configured."""

    def __init__(self, root=None, parent=None):
        super().__init__(parent, cut=9.0)
        self.setMinimumHeight(190)
        self._root = Path(root or Path(__file__).resolve().parent)
        self._probe = None
        self._last = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 12, 15, 11)
        layout.setSpacing(0)
        self.header = PanelHeader("GitHub", "Scanning")
        layout.addWidget(self.header)
        layout.addSpacing(7)

        self.repo = QLabel("—")
        self.repo.setFont(hud_font(10.2, QFont.Bold, 0.4, SANS))
        self.repo.setStyleSheet(
            f"color:{TEXT.name()}; background:transparent;"
        )
        self.repo.setWordWrap(True)
        layout.addWidget(self.repo)

        self.branch = QLabel("BRANCH —")
        self.branch.setFont(hud_font(7.0, QFont.DemiBold, 1.0))
        self.branch.setStyleSheet(
            f"color:{CYAN_SOFT.name()}; background:transparent;"
        )
        layout.addWidget(self.branch)
        layout.addSpacing(6)

        self.commit = QLabel("No commit data")
        self.commit.setFont(hud_font(6.8, QFont.Normal, 0.4, SANS))
        self.commit.setStyleSheet(
            f"color:{TEXT_SECONDARY.name()}; background:transparent;"
        )
        self.commit.setWordWrap(True)
        layout.addWidget(self.commit)
        layout.addSpacing(5)

        self.remote = QLabel("REMOTE // TOKEN ABSENT")
        self.remote.setFont(hud_font(6.4, QFont.DemiBold, 0.9))
        self.remote.setStyleSheet(
            f"color:{AMBER.name()}; background:transparent;"
        )
        self.remote.setWordWrap(True)
        layout.addWidget(self.remote)
        layout.addStretch(1)

        self._timer = QTimer(self)
        self._timer.setInterval(90_000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        QTimer.singleShot(350, self.refresh)

    def refresh(self):
        if self._probe is not None and self._probe.isRunning():
            return
        self.header.set_tag("Scanning", CYAN)
        self._probe = _GithubProbe(self._root, self)
        self._probe.result.connect(self.apply)
        self._probe.start()

    def apply(self, info):
        self._last = dict(info or {})
        repo_name = self._last.get("repo") or "repository"
        self.repo.setText(_short(repo_name, 28))
        branch = self._last.get("branch") or "—"
        dirty = self._last.get("dirty")
        if dirty is None:
            dirt = "?"
        elif dirty:
            dirt = f"+{dirty} modified"
        else:
            dirt = "clean"
        self.branch.setText(f"BRANCH {branch.upper()}  ·  {dirt.upper()}")
        commit = self._last.get("commit") or "—"
        subject = _short(self._last.get("subject") or "", 44)
        when = _short(self._last.get("when") or "", 18)
        if subject:
            self.commit.setText(f"{commit}  {subject}\n{when}")
        else:
            self.commit.setText("No commit data")
        if self._last.get("live"):
            notes = self._last.get("notifications")
            stars = self._last.get("stars")
            issues = self._last.get("issues")
            bits = []
            if notes is not None:
                bits.append(f"{notes} NOTIFICATIONS")
            if issues is not None:
                bits.append(f"{issues} OPEN")
            if stars is not None:
                bits.append(f"{stars} STARS")
            self.remote.setText("REMOTE // " + ("  ·  ".join(bits) or "LIVE"))
            self.header.set_tag("Live", CYAN)
            self.remote.setStyleSheet(
                f"color:{CYAN_SOFT.name()}; background:transparent;"
            )
        else:
            reason = self._last.get("error") or "token absent"
            self.remote.setText(f"REMOTE // {reason.upper()}")
            self.header.set_tag("Local", AMBER)
            self.remote.setStyleSheet(
                f"color:{AMBER.name()}; background:transparent;"
            )
        # The probe has emitted its only result and its thread has finished;
        # release the worker object instead of accumulating one per refresh.
        probe = self._probe
        self._probe = None
        if probe is not None:
            probe.deleteLater()
        self.update()

    def stop(self):
        self._timer.stop()
        probe = self._probe
        self._probe = None
        if probe is not None:
            probe.stop()


class LeftRail(QWidget):
    """The left instrument column: system, inbox and GitHub."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedWidth(284)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(11)
        self.system = SystemPanel()
        self.inbox = InboxPanel()
        self.github = GithubPanel()
        layout.addWidget(self.system, 1)
        layout.addWidget(self.inbox, 0)
        layout.addWidget(self.github, 0)

    def set_expanded(self, expanded):
        """Fold the inbox/GitHub plates away on shorter desktops."""
        expanded = bool(expanded)
        self.inbox.setVisible(expanded)
        self.github.setVisible(expanded)

    def update_metrics(self, cpu, ram, state, energy, uptime):
        self.system.note_state(state)
        self.system.note_energy(energy)
        self.system.update_metrics(cpu, ram, state, energy, uptime)

    def update_system(self, **kwargs):
        self.system.update_system(**kwargs)

    def note_state(self, state):
        self.system.note_state(state)

    def note_energy(self, energy):
        self.system.note_energy(energy)

    def set_mode(self, on):
        self.system.set_mode(on)

    def set_wake(self, on):
        self.system.set_wake(on)

    def set_voice(self, name):
        self.system.set_voice(name)

    def stop(self):
        self.github.stop()


# -- transcript ------------------------------------------------------------- #

class TranscriptRow(QWidget):
    """One speaker turn with a hairline gutter and a timestamp."""

    def __init__(self, speaker, text, stamp="", is_user=False, parent=None):
        super().__init__(parent)
        self._accent = CYAN if not is_user else AMBER
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)

        gutter = QFrame()
        gutter.setFixedWidth(2)
        gutter.setStyleSheet(
            f"background:{_rgba(self._accent, 170).name()}; border:none;"
        )
        gutter.setMinimumHeight(26)
        layout.addWidget(gutter, 0, Qt.AlignTop)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        tag = QLabel("YOU" if is_user else "JARVIS")
        tag.setFont(hud_font(6.9, QFont.Bold, 1.15))
        tag.setStyleSheet(
            f"color:{self._accent.name()}; background:transparent;"
        )
        head.addWidget(tag)
        head.addStretch(1)
        if stamp:
            clock = QLabel(stamp)
            clock.setFont(hud_font(6.4, QFont.Normal, 0.4))
            clock.setStyleSheet(
                f"color:{TEXT_MUTED.name()}; background:transparent;"
            )
            head.addWidget(clock)
        column.addLayout(head)

        body = QLabel(text or "")
        body.setWordWrap(True)
        body.setTextFormat(Qt.PlainText)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.setFont(hud_font(8.4, QFont.Normal, 0.25, SANS))
        body.setStyleSheet(
            f"color:{'#C9EDF3' if not is_user else '#DCEFF2'};"
            "background:transparent;"
        )
        column.addWidget(body)
        layout.addLayout(column, 1)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(20, 74, 86, 62), 1.0))
        painter.drawLine(QPointF(0, self.height() - 1),
                         QPointF(self.width(), self.height() - 1))
        painter.end()


class TranscriptPanel(HudPanel):
    """Live conversation transcript, seeded from the real SQLite history."""

    def __init__(self, parent=None):
        super().__init__(parent, cut=10.0)
        self._history_seeded = False
        self._message_count = 0
        self._facts = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 12, 15, 11)
        layout.setSpacing(0)
        self.header = PanelHeader("Transcript", "Live")
        layout.addWidget(self.header)
        layout.addSpacing(6)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("hudTranscript")
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setStyleSheet(
            "QScrollArea { background:transparent; border:none; }"
            "QScrollArea > QWidget > QWidget { background:transparent; }"
            "QScrollBar:vertical { background:transparent; width:4px; }"
            "QScrollBar::handle:vertical { background:#1B4A54;"
            " border-radius:2px; min-height:24px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical"
            " { height:0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical"
            " { background:transparent; }"
        )
        self._host = QWidget()
        self._host.setAttribute(Qt.WA_TranslucentBackground, True)
        self._messages = QVBoxLayout(self._host)
        self._messages.setContentsMargins(0, 7, 0, 7)
        self._messages.setSpacing(7)
        self._messages.addStretch(1)
        self.scroll.setWidget(self._host)
        layout.addWidget(self.scroll, 1)

        self.empty = QLabel(
            "AWAITING INPUT\nSay “Hey JARVIS”, tap the reactor, or type below."
        )
        self.empty.setFont(hud_font(7.2, QFont.Normal, 0.5, SANS))
        self.empty.setStyleSheet(
            f"color:{TEXT_MUTED.name()}; background:transparent;"
        )
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._messages.insertWidget(0, self.empty)

        self.facts = QLabel("")
        self.facts.setFont(hud_font(6.4, QFont.Normal, 0.35))
        self.facts.setStyleSheet(
            f"color:{TEXT_MUTED.name()}; background:transparent;"
        )
        self.facts.setWordWrap(True)
        layout.addWidget(self.facts)

    def append_turn(self, speaker, text):
        is_user = str(speaker).upper() == "YOU"
        self._add("You" if is_user else "JARVIS", text,
                  is_user=is_user, stamp=time.strftime("%H:%M"))

    def append_system(self, text):
        self._hide_empty()
        note = QLabel(_short(text, 240))
        note.setFont(hud_font(6.8, QFont.Normal, 0.4, SANS))
        note.setStyleSheet(
            f"color:{AMBER.name()}; background:transparent;"
        )
        note.setWordWrap(True)
        self._messages.insertWidget(max(0, self._messages.count() - 1), note)
        self._scroll_to_bottom()

    def _add(self, tag, text, is_user=False, stamp=None):
        row = TranscriptRow(tag, _short(text, 360), stamp or "",
                            is_user=is_user)
        self._messages.insertWidget(max(0, self._messages.count() - 1), row)
        self._message_count += 1
        self._hide_empty()
        self.header.set_tag(f"Live · {self._message_count}", CYAN)
        self._scroll_to_bottom()

    def _hide_empty(self):
        empty = getattr(self, "empty", None)
        if empty is not None:
            empty.setVisible(self._message_count == 0)

    def _scroll_to_bottom(self):
        def scroll():
            bar = self.scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
        QTimer.singleShot(0, scroll)

    def set_history(self, entries):
        """Seed once from the real SQLite transcript, newest last."""
        if self._history_seeded or self._message_count or not entries:
            return
        for speaker, text in entries[-6:]:
            is_user = str(speaker).upper() == "YOU"
            self._add("You" if is_user else "JARVIS", text,
                      is_user=is_user, stamp=None)
        self._history_seeded = True

    def set_facts(self, facts):
        self._facts = list(facts or [])
        if not self._facts:
            self.facts.setText("MEMORY // no saved facts")
            return
        joined = "  ·  ".join(_short(fact, 24) for fact in self._facts[:3])
        self.facts.setText(f"MEMORY // {joined.upper()}")

    def clear(self):
        while self._messages.count() > 1:
            item = self._messages.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not getattr(self, "empty", None):
                widget.deleteLater()
        self._message_count = 0
        self._history_seeded = False
        if getattr(self, "empty", None) is not None:
            self.empty.show()


# -- terminal / activity ---------------------------------------------------- #

class ActivityPanel(HudPanel):
    """A real event stream; never a canned terminal transcript."""

    LEVELS = {
        "info": TEXT_SECONDARY,
        "state": CYAN_SOFT,
        "user": AMBER_SOFT,
        "reply": QColor(190, 240, 248),
        "net": AMBER,
        "warn": AMBER,
        "error": ROSE,
    }

    def __init__(self, parent=None):
        super().__init__(parent, cut=10.0)
        self.setMinimumHeight(190)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 12, 15, 11)
        layout.setSpacing(0)
        self.header = PanelHeader("Terminal", "Activity")
        layout.addWidget(self.header)
        layout.addSpacing(6)

        self.view = QTextEdit()
        self.view.setObjectName("hudActivity")
        self.view.setReadOnly(True)
        self.view.setFrameShape(QFrame.NoFrame)
        self.view.setLineWrapMode(QTextEdit.NoWrap)
        self.view.setFont(hud_font(6.9, QFont.Normal, 0.25))
        self.view.setStyleSheet(
            "QTextEdit#hudActivity { background:transparent; border:none;"
            f" color:{TEXT_SECONDARY.name()};"
            " selection-background-color:rgba(34,230,255,0.22); }"
            "QScrollBar:vertical { background:transparent; width:4px; }"
            "QScrollBar::handle:vertical { background:#1B4A54;"
            " border-radius:2px; min-height:24px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical"
            " { height:0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical"
            " { background:transparent; }"
        )
        self.view.document().setMaximumBlockCount(160)
        layout.addWidget(self.view, 1)

    def log(self, text, level="info"):
        text = " ".join(str(text or "").split())
        if not text:
            return
        bar = self.view.verticalScrollBar()
        follow = bar.value() >= bar.maximum() - 3
        color = self.LEVELS.get(level, TEXT_SECONDARY)
        stamp = time.strftime("%H:%M:%S")
        safe = html.escape(text)
        self.view.append(
            f'<span style="color:#335C66">{stamp}</span> '
            f'<span style="color:#4E8F9C">›</span> '
            f'<span style="color:{color.name()}">{safe}</span>'
        )
        if follow:
            bar.setValue(bar.maximum())

    def note_state(self, state):
        color = state_color(state)
        label = str(state).replace("...", "").upper()
        self.header.set_tag(label, color)
        self.set_accent(color)


class RightRail(QWidget):
    """The right instrument column: transcript + activity."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedWidth(374)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(11)
        self.transcript = TranscriptPanel()
        self.activity = ActivityPanel()
        layout.addWidget(self.transcript, 1)
        layout.addWidget(self.activity, 0)

    def append_turn(self, speaker, text):
        self.transcript.append_turn(speaker, text)

    def append_system(self, text):
        self.transcript.append_system(text)

    def set_history(self, entries):
        self.transcript.set_history(entries)

    def set_facts(self, facts):
        self.transcript.set_facts(facts)

    def log(self, text, level="info"):
        self.activity.log(text, level)

    def note_state(self, state):
        self.activity.note_state(state)

    def set_state(self, state):
        self.note_state(state)


# -- command bar ------------------------------------------------------------ #

class CommandBar(HudPanel):
    """Full-width command console at the bottom of the HUD."""

    submitted = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent, cut=8.0, notch=8.0)
        self.setFixedHeight(64)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 9, 16, 9)
        layout.setSpacing(10)

        prompt = QLabel(">_")
        prompt.setFont(hud_font(10.5, QFont.Bold, 1.2))
        prompt.setStyleSheet(
            f"color:{AMBER.name()}; background:transparent;"
        )
        prompt.setFixedWidth(24)
        layout.addWidget(prompt)

        prefix = QLabel("CMD")
        prefix.setFont(hud_font(7.0, QFont.Bold, 1.35))
        prefix.setStyleSheet(
            f"color:{TEXT_MUTED.name()}; background:transparent;"
        )
        layout.addWidget(prefix)

        self.input = QLineEdit()
        self.input.setObjectName("hudCommandInput")
        self.input.setPlaceholderText(
            "Type a command, a question, or “open youtube”…"
        )
        self.input.setFont(hud_font(9.0, QFont.Normal, 0.35))
        self.input.setFrame(False)
        self.input.setStyleSheet(
            "QLineEdit#hudCommandInput { background:transparent; border:none;"
            f" color:{TEXT.name()}; padding:2px 4px;"
            " selection-background-color:rgba(34,230,255,0.24); }"
            "QLineEdit#hudCommandInput::placeholder { color:#3E6570; }"
        )
        self.input.returnPressed.connect(self._emit)
        layout.addWidget(self.input, 1)

        self.hint = QLabel("ENTER  EXECUTE   ·   CTRL+K  FOCUS   ·   ESC  HIDE")
        self.hint.setFont(hud_font(6.2, QFont.DemiBold, 0.9))
        self.hint.setStyleSheet(
            f"color:{TEXT_MUTED.name()}; background:transparent;"
        )
        layout.addWidget(self.hint)

        self.send = QPushButton("EXECUTE")
        self.send.setCursor(Qt.PointingHandCursor)
        self.send.setFocusPolicy(Qt.NoFocus)
        self.send.setFixedSize(86, 32)
        self.send.setFont(hud_font(7.0, QFont.Bold, 1.4))
        self.send.setStyleSheet(
            "QPushButton { background:rgba(255,181,71,0.07);"
            " border:1px solid rgba(255,181,71,0.40);"
            " border-radius:2px; color:#FFD79A; padding:0; }"
            "QPushButton:hover { background:rgba(255,181,71,0.15);"
            " border-color:rgba(255,181,71,0.75); color:#FFF0D0; }"
            "QPushButton:disabled { color:#456B75;"
            " border-color:rgba(69,107,117,0.30); background:transparent; }"
        )
        self.send.clicked.connect(self._emit)
        layout.addWidget(self.send)

        self._busy = False

    def _emit(self):
        if self._busy:
            return
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self.submitted.emit(text)

    def set_busy(self, busy):
        self._busy = bool(busy)
        self.input.setEnabled(not self._busy)
        self.send.setEnabled(not self._busy)
        if self._busy:
            self.input.setPlaceholderText("JARVIS is processing…")
        else:
            self.input.setPlaceholderText(
                "Type a command, a question, or “open youtube”…"
            )

    def focus_input(self):
        if self.input.isEnabled():
            self.input.setFocus(Qt.ShortcutFocusReason)
            self.input.selectAll()

    def set_state(self, state):
        color = state_color(state)
        self.set_accent(color)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.hint.setVisible(self.width() >= 900)


# -- header ----------------------------------------------------------------- #

class HudMark(QWidget):
    """Small reactor glyph for the title bar."""

    def __init__(self, size=38, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = QPointF(self.width() / 2.0, self.height() / 2.0)
        radius = min(self.width(), self.height()) / 2.0
        glow = QRadialGradient(center, radius)
        glow.setColorAt(0.0, QColor(34, 230, 255, 150))
        glow.setColorAt(0.45, QColor(34, 230, 255, 38))
        glow.setColorAt(1.0, QColor(34, 230, 255, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(center, radius, radius)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(34, 230, 255, 105), 1.0))
        painter.drawEllipse(center, radius * 0.78, radius * 0.78)
        painter.setPen(QPen(QColor(255, 181, 71, 170), 1.2))
        for index in range(8):
            angle = index * math.pi / 4.0
            inner = radius * 0.54
            outer = radius * 0.70
            painter.drawLine(
                QPointF(center.x() + math.cos(angle) * inner,
                        center.y() + math.sin(angle) * inner),
                QPointF(center.x() + math.cos(angle) * outer,
                        center.y() + math.sin(angle) * outer),
            )
        core = QRadialGradient(center, radius * 0.44)
        core.setColorAt(0.0, QColor(255, 255, 255, 255))
        core.setColorAt(0.45, QColor(160, 250, 255, 250))
        core.setColorAt(1.0, QColor(34, 230, 255, 40))
        painter.setPen(Qt.NoPen)
        painter.setBrush(core)
        painter.drawEllipse(center, radius * 0.34, radius * 0.34)
        painter.end()


def _window_button(text, name, tooltip):
    button = QPushButton(text)
    button.setObjectName(name)
    button.setFixedSize(28, 28)
    button.setCursor(Qt.PointingHandCursor)
    button.setFocusPolicy(Qt.NoFocus)
    button.setToolTip(tooltip)
    button.setStyleSheet(
        "QPushButton { color:#4E7A84; background:transparent; border:none;"
        " font-size:13px; padding:0; }"
        "QPushButton:hover { color:#D9F6FA; background:rgba(34,230,255,0.08);"
        " border:1px solid rgba(34,230,255,0.22); }"
        "QPushButton#closeButton:hover { color:#FFD8DC;"
        " background:rgba(255,92,108,0.14);"
        " border-color:rgba(255,92,108,0.35); }"
    )
    return button


class TechStrip(QWidget):
    """Decorative technical markings, without inventing live measurements."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(220)
        self.setFixedHeight(32)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.left_text = "MK VII"
        self.right_text = "NEURAL LINK"
        self.mid_text = "SECURE"

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width = self.width()
        mid = self.height() / 2.0
        painter.setPen(QPen(QColor(20, 74, 86, 150), 1.0))
        painter.drawLine(QPointF(0, mid), QPointF(width * 0.32, mid))
        painter.drawLine(QPointF(width * 0.68, mid), QPointF(width, mid))
        for index in range(7):
            x = width * 0.34 + index * (width * 0.32 / 6.0)
            height = 5 if index in (0, 6) else 3
            painter.drawLine(QPointF(x, mid - height),
                             QPointF(x, mid + height))
        painter.setPen(QPen(QColor(255, 181, 71, 170), 1.0))
        painter.drawLine(QPointF(width * 0.5 - 3, mid),
                         QPointF(width * 0.5 + 3, mid))
        painter.setFont(hud_font(6.2, QFont.DemiBold, 0.9))
        painter.setPen(QColor(TEXT_MUTED))
        painter.drawText(QRectF(0, 0, width * 0.30, self.height()),
                         Qt.AlignLeft | Qt.AlignVCenter, self.left_text)
        painter.setPen(QColor(255, 181, 71, 185))
        painter.drawText(QRectF(width * 0.36, 0, width * 0.28,
                                self.height()),
                         Qt.AlignCenter, self.mid_text)
        painter.setPen(QColor(TEXT_MUTED))
        painter.drawText(QRectF(width * 0.70, 0, width * 0.30,
                                self.height()),
                         Qt.AlignRight | Qt.AlignVCenter, self.right_text)
        painter.end()


class HudTitleBar(QWidget):
    """Frameless header: identity, technical markings, link, clock, controls."""

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self._drag = None
        self._compact = False
        self._full_title = "JARVIS"
        self.setObjectName("hudTitleBar")
        self.setFixedHeight(92)

        row = QHBoxLayout(self)
        row.setContentsMargins(22, 10, 14, 0)
        row.setSpacing(13)
        self._row = row
        self.mark = HudMark(40)
        row.addWidget(self.mark)

        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(0)
        self.title = QLabel(self._full_title)
        self.title.setFont(hud_font(25, QFont.Bold, 7.0, SANS))
        self.title.setStyleSheet(
            f"color:{TEXT.name()}; background:transparent;"
        )
        titles.addWidget(self.title)
        self.subtitle = QLabel("JUST A RATHER VERY INTELLIGENT SYSTEM")
        self.subtitle.setFont(hud_font(6.9, QFont.DemiBold, 1.8))
        self.subtitle.setStyleSheet(
            f"color:{TEXT_SECONDARY.name()}; background:transparent;"
        )
        titles.addWidget(self.subtitle)
        self.build_label = QLabel("MK VII   //   NEURAL COMMAND INTERFACE")
        self.build_label.setFont(hud_font(6.1, QFont.DemiBold, 1.1))
        self.build_label.setStyleSheet(
            f"color:{AMBER.name()}; background:transparent;"
        )
        titles.addWidget(self.build_label)
        row.addLayout(titles)
        row.addStretch(1)

        self.tech = TechStrip()
        row.addWidget(self.tech, 0, Qt.AlignVCenter)
        row.addStretch(1)

        status_box = QVBoxLayout()
        status_box.setContentsMargins(0, 0, 0, 0)
        status_box.setSpacing(1)
        status_line = QHBoxLayout()
        status_line.setContentsMargins(0, 0, 0, 0)
        status_line.setSpacing(6)
        status_line.addStretch(1)
        self.connection_dot = QLabel("●")
        self.connection_dot.setStyleSheet(
            f"color:{GREEN.name()}; background:transparent; font-size:8px;"
        )
        self.connection_label = QLabel("LINK ACTIVE")
        self.connection_label.setFont(hud_font(6.6, QFont.Bold, 1.0))
        self.connection_label.setStyleSheet(
            f"color:{GREEN.name()}; background:transparent;"
        )
        status_line.addWidget(self.connection_dot)
        status_line.addWidget(self.connection_label)
        status_box.addLayout(status_line)

        self.clock = QLabel("00:00:00")
        self.clock.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.clock.setFont(hud_font(15, QFont.DemiBold, 1.2))
        self.clock.setStyleSheet(
            f"color:{TEXT.name()}; background:transparent;"
        )
        self.date_label = QLabel("")
        self.date_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.date_label.setFont(hud_font(6.2, QFont.DemiBold, 0.9))
        self.date_label.setStyleSheet(
            f"color:{TEXT_MUTED.name()}; background:transparent;"
        )
        status_box.addWidget(self.clock)
        status_box.addWidget(self.date_label)
        row.addLayout(status_box)
        row.addSpacing(8)

        self.minimize_button = _window_button("−", "minimizeButton", "Minimise")
        self.maximize_button = _window_button("□", "maximizeButton", "Maximise")
        self.close_button = _window_button("✕", "closeButton", "Hide to tray")
        self.minimize_button.clicked.connect(window._minimize)
        self.maximize_button.clicked.connect(window._toggle_maximize)
        self.close_button.clicked.connect(window._close_to_tray)
        row.addWidget(self.minimize_button)
        row.addWidget(self.maximize_button)
        row.addWidget(self.close_button)

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)
        self._update_clock()

    def _update_clock(self):
        now = time.localtime()
        self.clock.setText(time.strftime("%H:%M:%S", now))
        self.date_label.setText(time.strftime("%a %d %b %Y", now).upper())

    def set_compact(self, compact):
        compact = bool(compact)
        if compact == self._compact:
            return
        self._compact = compact
        self.setFixedHeight(56 if compact else 92)
        self.title.setText("JARVIS")
        self.title.setFont(
            hud_font(14 if compact else 25, QFont.Bold, 2.4 if compact else 7.0,
                     SANS)
        )
        self.mark.setFixedSize(26 if compact else 40, 26 if compact else 40)
        if compact:
            self._row.setContentsMargins(12, 4, 8, 0)
            self._row.setSpacing(7)
        else:
            self._row.setContentsMargins(22, 10, 14, 0)
            self._row.setSpacing(13)
        for widget in (self.subtitle, self.build_label, self.tech,
                       self.connection_dot, self.connection_label,
                       self.clock, self.date_label):
            widget.setVisible(not compact)
        for button in (self.minimize_button, self.maximize_button,
                       self.close_button):
            button.setFixedSize(22 if compact else 28, 22 if compact else 28)
        self.maximize_button.setVisible(not compact)
        self._update_clock()

    def set_link(self, label, color):
        self.connection_label.setText(str(label).upper())
        self.connection_label.setStyleSheet(
            f"color:{QColor(color).name()}; background:transparent;"
        )
        self.connection_dot.setStyleSheet(
            f"color:{QColor(color).name()}; background:transparent;"
            f" font-size:8px;"
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        y = self.height() - 1.0
        painter.setPen(QPen(QColor(20, 74, 86, 155), 1.0))
        painter.drawLine(QPointF(22, y), QPointF(self.width() - 22, y))
        painter.setPen(QPen(QColor(255, 181, 71, 190), 1.2))
        painter.drawLine(QPointF(22, y), QPointF(146, y))
        painter.setPen(QPen(QColor(34, 230, 255, 120), 1.0))
        painter.drawLine(QPointF(self.width() - 260, y),
                         QPointF(self.width() - 22, y))
        painter.end()

    # -- frameless drag ---------------------------------------------------- #
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if getattr(self._window, "_is_maximized", False):
                self._drag = None
            else:
                self._drag = (event.globalPos()
                              - self._window.frameGeometry().topLeft())
        event.accept()

    def mouseMoveEvent(self, event):
        if (self._drag is not None
                and event.buttons() & Qt.LeftButton
                and not getattr(self._window, "_is_maximized", False)):
            self._window.move(event.globalPos() - self._drag)
        event.accept()

    def mouseReleaseEvent(self, event):
        self._drag = None
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._window._toggle_maximize()
        event.accept()
