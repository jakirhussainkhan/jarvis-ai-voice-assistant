"""Floating 'Arc Reactor' desktop widget + system-tray for Jarvis (PyQt5).

Click the reactor (or the tray icon) to talk; the audio + agent work runs on a
QThread so the UI never freezes. The app keeps running in the background: closing
the floating widget hides it to the system tray instead of quitting.

Controls:
  floating widget   left-click = talk   ·   drag = move
                    right-click = menu   ·   Esc = hide to tray
  tray icon         left-click = talk   ·   right-click = menu
                    menu: Talk / Show-Hide / Quit
"""

import math
import sys

from PyQt5.QtCore import QPointF, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import (QBrush, QColor, QIcon, QPainter, QPen, QPixmap,
                         QRadialGradient)
from PyQt5.QtWidgets import (QApplication, QLabel, QMenu, QSystemTrayIcon,
                             QVBoxLayout, QWidget)

from main import handle
from memory import Memory
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


def make_icon(size=64):
    """Paint the arc-reactor as a QIcon (used for the window and tray)."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    center = QPointF(size / 2.0, size / 2.0)
    radius = size / 2.0 - 1.5

    glow = QRadialGradient(center, radius)
    glow.setColorAt(0.0, QColor(120, 205, 255))
    glow.setColorAt(1.0, QColor(0, 80, 190))
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(glow))
    painter.drawEllipse(center, radius, radius)

    ring = QPen(QColor(185, 240, 255))
    ring.setWidthF(max(1.0, size * 0.03))
    painter.setPen(ring)
    painter.setBrush(Qt.NoBrush)
    painter.drawEllipse(center, radius * 0.82, radius * 0.82)

    core_radius = radius * 0.5
    core = QRadialGradient(center, core_radius)
    core.setColorAt(0.0, QColor(255, 255, 255))
    core.setColorAt(1.0, QColor(90, 185, 255))
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(core))
    painter.drawEllipse(center, core_radius, core_radius)
    painter.end()
    return QIcon(pixmap)


class Reactor(QWidget):
    """The circular arc-reactor button. Click to talk, drag to move."""

    clicked = pyqtSignal()
    menu_requested = pyqtSignal(object)

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
        if event.button() == Qt.RightButton:
            self.menu_requested.emit(event.globalPos())
        elif event.button() == Qt.LeftButton and not self._dragged:
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
            # listen() already prints the "👤 <user>" line to stdout and speak()
            # prints "🤖 <reply>" exactly once, so we only drive the state
            # colours here.

            self.state_changed.emit(THINKING)
            # main.handle() is the single dispatch path: it resolves any pending
            # confirmation, then tries the router and finally the agent.
            reply, _kind, _tokens = handle(user, self.memory)

            self.state_changed.emit(SPEAKING)
            speak(reply)          # prints "🤖 <reply>" exactly once
        except Exception as exc:                      # never kill the UI
            self.message.emit(f"⚠️ {exc}")
        finally:
            self.state_changed.emit(IDLE)


class JarvisTray(QSystemTrayIcon):
    """Tray icon so Jarvis can live in the background."""

    def __init__(self, widget):
        super().__init__(make_icon(64), widget)
        self.widget = widget
        self.setToolTip("Jarvis — click to talk")

        menu = QMenu()
        talk = menu.addAction("Talk")
        talk.triggered.connect(widget.trigger)
        self.toggle_action = menu.addAction("Hide window")
        self.toggle_action.triggered.connect(widget.toggle_visible)
        menu.addSeparator()
        quit_action = menu.addAction("Quit")
        quit_action.triggered.connect(widget.quit)
        self.setContextMenu(menu)

        self.activated.connect(self._on_activated)

    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.widget.trigger()

    def sync_toggle(self, visible):
        self.toggle_action.setText("Hide window" if visible else "Show window")


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
        self.setWindowIcon(make_icon(64))

        self.memory = Memory()
        self.worker = None
        self._drag_offset = None
        self._quitting = False
        self._menu = QMenu(self)

        self.reactor = Reactor(150)
        self.reactor.clicked.connect(self.trigger)
        self.reactor.menu_requested.connect(self._show_menu)

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

        self._build_menu()
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = JarvisTray(self)
            self.tray.show()

        self.resize(self.sizeHint())
        self._center()

    def _build_menu(self):
        talk = self._menu.addAction("Talk")
        talk.triggered.connect(self.trigger)
        self.toggle_action = self._menu.addAction("Hide to tray")
        self.toggle_action.triggered.connect(self.toggle_visible)
        self._menu.addSeparator()
        quit_action = self._menu.addAction("Quit")
        quit_action.triggered.connect(self.quit)

    def _show_menu(self, global_pos):
        self._menu.exec_(global_pos)

    def _center(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.center() - self.rect().center())

    def set_state(self, state):
        self.status.setText(state)
        self.reactor.set_state(state)
        if self.tray is not None:
            self.tray.setToolTip(f"Jarvis — {state}")

    def trigger(self):
        """Start one listen/process/speak cycle (ignored if already busy)."""
        if self.worker is not None and self.worker.isRunning():
            return
        if not self.isVisible():
            self.show()
        self.worker = JarvisWorker(self.memory, self)
        self.worker.state_changed.connect(self.set_state)
        self.worker.message.connect(self._log)
        self.worker.start()

    def toggle_visible(self):
        """Show or hide the floating widget (staying alive in the tray)."""
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def quit(self):
        """Really exit: stop the tray and the Qt event loop."""
        self._quitting = True
        if self.tray is not None:
            self.tray.hide()
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait(5000)
        QApplication.quit()

    @staticmethod
    def _log(text):
        print(text, flush=True)

    # -- window drag (background area) ------------------------------------ #
    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self._show_menu(event.globalPos())
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
            self.hide()                      # hide to tray, don't quit
        else:
            super().keyPressEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if self.tray is not None:
            self.tray.sync_toggle(True)

    def hideEvent(self, event):
        super().hideEvent(event)
        if self.tray is not None:
            self.tray.sync_toggle(False)

    def closeEvent(self, event):
        """Closing the widget hides it to the tray instead of quitting."""
        if self._quitting or self.tray is None:
            if self.worker is not None and self.worker.isRunning():
                self.worker.wait(5000)
            event.accept()
            return
        event.ignore()
        self.hide()
        self.tray.showMessage(
            "Jarvis", "Still running in the tray. Click the icon to talk.",
            QSystemTrayIcon.Information, 3000,
        )


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Jarvis")
    app.setWindowIcon(make_icon(64))
    # Keep running after the floating window is hidden/closed -> tray lives on.
    app.setQuitOnLastWindowClosed(False)
    widget = JarvisWidget()
    widget.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
