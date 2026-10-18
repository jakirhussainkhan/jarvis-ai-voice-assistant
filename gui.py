"""Floating 'Arc Reactor' desktop widget for Jarvis (PyQt5).

A borderless, always-on-top, drag-anywhere widget. Click the reactor to talk;
the audio + agent work runs on a QThread so the UI never freezes.

Controls:  left-click = talk   ·   drag = move   ·   right-click / Esc = quit
"""

import math
import sys

from PyQt5.QtCore import QPointF, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QPainter, QPen, QRadialGradient
from PyQt5.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from agent import run_agent
from memory import Memory
from router import try_local
from voice import listen, speak

IDLE = "Idle"
LISTENING = "Listening..."
THINKING = "Thinking..."
SPEAKING = "Speaking..."

# glow colour per state: (outer glow, core)
_COLORS = {
    IDLE:      (QColor(0, 140, 255), QColor(120, 205, 255)),
    LISTENING: (QColor(0, 220, 140), QColor(140, 255, 210)),
    THINKING:  (QColor(255, 170, 0), QColor(255, 225, 150)),
    SPEAKING:  (QColor(170, 90, 255), QColor(220, 180, 255)),
}


class Reactor(QWidget):
    """The circular arc-reactor button. Click to talk, drag to move."""

    clicked = pyqtSignal()

    def __init__(self, diameter=150, parent=None):
        super().__init__(parent)
        self.setFixedSize(diameter, diameter)
        self._state = IDLE
        self._phase = 0.0
        self._press = None
        self._dragged = False
        self.setCursor(Qt.PointingHandCursor)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._pulse)
        self._timer.start(40)              # ~25 fps glow pulse

    def _pulse(self):
        self._phase = (self._phase + 0.06) % (2 * math.pi)
        self.update()

    def set_state(self, state):
        self._state = state
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        center = QPointF(self.width() / 2.0, self.height() / 2.0)
        radius = min(center.x(), center.y()) - 2
        pulse = 1.0 + 0.035 * math.sin(self._phase)
        glow, core = _COLORS.get(self._state, _COLORS[IDLE])

        # 1. outer glow
        grad = QRadialGradient(center, radius * pulse)
        grad.setColorAt(0.00, QColor(glow.red(), glow.green(), glow.blue(), 235))
        grad.setColorAt(0.55, QColor(glow.red(), glow.green(), glow.blue(), 120))
        grad.setColorAt(1.00, QColor(glow.red(), glow.green(), glow.blue(), 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(grad))
        painter.drawEllipse(center, radius * pulse, radius * pulse)

        # 2. concentric rings
        ring_pen = QPen(QColor(150, 235, 255, 150))
        ring_pen.setWidthF(1.5)
        painter.setPen(ring_pen)
        painter.setBrush(Qt.NoBrush)
        for frac in (0.86, 0.72):
            painter.drawEllipse(center, radius * frac, radius * frac)

        # 3. bright core
        core_r = radius * 0.55 * pulse
        core_grad = QRadialGradient(center, core_r)
        core_grad.setColorAt(0.0, QColor(255, 255, 255, 245))
        core_grad.setColorAt(0.45, core)
        core_grad.setColorAt(1.0, core.darker(190))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(core_grad))
        painter.drawEllipse(center, core_r, core_r)
        painter.end()

    # -- click vs drag ---------------------------------------------------- #
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press = event.globalPos()
            self._dragged = False
        event.accept()

    def mouseMoveEvent(self, event):
        if self._press is not None and event.buttons() & Qt.LeftButton:
            delta = event.globalPos() - self._press
            if not self._dragged and delta.manhattanLength() > 6:
                self._dragged = True
            if self._dragged:
                window = self.window()
                window.move(window.pos() + delta)
                self._press = event.globalPos()
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and not self._dragged:
            self.clicked.emit()
        self._press = None
        self._dragged = False
        event.accept()


class JarvisWorker(QThread):
    """Runs listen -> try_local/run_agent -> speak off the GUI thread."""

    state_changed = pyqtSignal(str)
    message = pyqtSignal(str)

    def __init__(self, memory, parent=None):
        super().__init__(parent)
        self.memory = memory

    def run(self):
        try:
            self.state_changed.emit(LISTENING)
            user = listen()
            if not user:
                # Nothing heard: return straight away so the finally block
                # below resets the widget to Idle. Never spin here.
                return
            # listen() already prints the "👤 <user>" line to stdout -- emitting
            # it here too printed every recognised phrase twice. Same for speak()
            # below. Keep the status signals, drop the duplicate logs.

            reply = try_local(user)
            if not reply:
                self.state_changed.emit(THINKING)
                reply, _tokens = run_agent(user, self.memory)

            self.state_changed.emit(SPEAKING)
            speak(reply)          # prints "🤖 <reply>" exactly once
        except Exception as exc:                      # never kill the UI
            self.message.emit(f"⚠️ {exc}")
        finally:
            self.state_changed.emit(IDLE)


class JarvisWidget(QWidget):
    """The floating window: reactor + status label."""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        # Transparent top-level window. WA_TranslucentBackground asks the
        # compositor for an ARGB surface; WA_NoSystemBackground stops Qt from
        # erasing that surface with an opaque system colour (the usual cause of
        # the black box on X11/Cinnamon in Linux Mint). The stylesheet makes
        # the widget and its children paint nothing behind the reactor.
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent;")
        self.setWindowTitle("Jarvis")

        self.memory = Memory()
        self.worker = None
        self._drag_offset = None

        self.reactor = Reactor(150)
        self.reactor.clicked.connect(self.trigger)

        self.status = QLabel(IDLE)
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setStyleSheet(
            "color:#dff2ff; background:transparent;"
            "font-size:13px; font-weight:600;"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(4)
        layout.addWidget(self.reactor, alignment=Qt.AlignCenter)
        layout.addWidget(self.status, alignment=Qt.AlignCenter)

        self.resize(self.sizeHint())
        self._center()

    def _center(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.center() - self.rect().center())

    def set_state(self, state):
        self.status.setText(state)
        self.reactor.set_state(state)

    def trigger(self):
        """Start one listen/process/speak cycle (ignored if already busy)."""
        if self.worker is not None and self.worker.isRunning():
            return
        self.worker = JarvisWorker(self.memory, self)
        self.worker.state_changed.connect(self.set_state)
        self.worker.message.connect(self._log)
        self.worker.start()

    @staticmethod
    def _log(text):
        print(text, flush=True)

    # -- window drag (background area) ------------------------------------ #
    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self.close()
            return
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag_offset)
        event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        # listen()/speak() block, so give the worker a moment to finish before
        # the Qt object is torn down (avoids "QThread destroyed while running").
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait(5000)
        event.accept()


def main():
    app = QApplication(sys.argv)
    widget = JarvisWidget()
    widget.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
