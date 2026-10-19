"""Platform adapter interfaces for Jarvis.

Every OS-specific capability Jarvis needs is expressed here as an abstract base
class. The concrete implementations live in the per-OS subpackages
(``linux``, ``windows``, ``macos``) and are selected at runtime by the factory
in :mod:`platform_adapters`.

The three capabilities are:

* :class:`AudioController`  -- microphone capture, playback and the mixer.
* :class:`WindowController` -- windows, screen automation and the pointer.
* :class:`SystemController` -- apps, media, brightness, power, clipboard, ...

Methods that a user can trigger from speech return a short, spoken reply string
(so the OS-specific wording lives in one place). Lower-level primitives return
booleans or plain values so orchestration code can stay OS-agnostic.
"""

from abc import ABC, abstractmethod


class AudioController(ABC):
    """Microphone capture, audio playback and the output mixer."""

    # The mixer backend name, used in user-facing messages ("pactl is not
    # installed."). Subclasses override it (e.g. "pycaw", "osascript").
    volume_backend = "audio backend"

    # -- capture ------------------------------------------------------------ #
    @abstractmethod
    def record_command(self, device=None, *, seconds=None, output=None, fmt="raw"):
        """Return the argv list that records audio in the shared format.

        ``fmt="raw"`` streams headerless S16_LE mono PCM (used for streaming
        capture); ``fmt="wav"`` writes a self-describing WAV file.
        """

    @abstractmethod
    def spawn_recorder(self, device=None):
        """Start a streaming recorder and return a Popen-like process object."""

    @abstractmethod
    def stop_recorder(self, proc):
        """Terminate a process returned by :meth:`spawn_recorder` (never raises)."""

    @abstractmethod
    def record(self, seconds=3.0, output=None, device=None):
        """Record ``seconds`` of audio to ``output`` (or a temp WAV).

        Returns the path that was written, or ``None`` on failure.
        """

    # -- playback ----------------------------------------------------------- #
    @abstractmethod
    def play(self, path):
        """Play an audio file to completion; return True on success."""

    # -- volume ------------------------------------------------------------- #
    @abstractmethod
    def get_volume(self):
        """Return the master output volume as a percent (0-100), or None."""

    @abstractmethod
    def set_volume(self, percent):
        """Set the master output volume (0-100); return True on success."""

    @abstractmethod
    def adjust_volume(self, delta):
        """Change the master output volume by ``delta`` percent (may be < 0)."""

    @abstractmethod
    def set_mute(self, muted):
        """Mute (True) or unmute (False) the output; return True on success."""

    # -- convenience -------------------------------------------------------- #
    def volume_up(self, step=10):
        return self.adjust_volume(abs(int(step)))

    def volume_down(self, step=10):
        return self.adjust_volume(-abs(int(step)))

    def volume_control(self, action, value=None):
        """Volume up/down/mute/unmute or set to a percent; a spoken reply."""
        key = (action or "").strip().lower()
        if key in ("mute",):
            return "Muted." if self.set_mute(True) else "I couldn't reach the audio system."
        if key in ("unmute",):
            return "Unmuted." if self.set_mute(False) else "I couldn't reach the audio system."
        if key in ("up", "increase"):
            return "Volume up." if self.volume_up() else "I couldn't reach the audio system."
        if key in ("down", "decrease"):
            return "Volume down." if self.volume_down() else "I couldn't reach the audio system."
        if key in ("set", "to", "level") and value is not None:
            percent = max(0, min(100, int(value)))
            if self.set_volume(percent):
                return f"Volume set to {percent} percent."
            return "I couldn't reach the audio system."
        return "I can change the volume up, down, mute, unmute or to a percent."


class WindowController(ABC):
    """Windows, screen automation and the pointer."""

    # -- enumeration / focus ------------------------------------------------ #
    @abstractmethod
    def list_window_titles(self):
        """Return the titles of the open top-level windows (possibly empty)."""

    @abstractmethod
    def get_active_window(self):
        """Return a handle for the focused window, or None."""

    @abstractmethod
    def get_active_window_title(self):
        """Return the title of the focused window, or an empty string."""

    @abstractmethod
    def locate_window(self, name):
        """Return a handle for a visible window whose title matches ``name``."""

    @abstractmethod
    def window_name(self, handle):
        """Return the current title of ``handle`` ("" when unknown)."""

    @abstractmethod
    def activate_window(self, handle):
        """Raise the window to the front; return True on success."""

    @abstractmethod
    def activate_by_title(self, name):
        """Raise the first window whose title matches ``name``; return a reply."""

    @abstractmethod
    def close_by_title(self, name):
        """Close windows whose title matches ``name``; return True if any."""

    @abstractmethod
    def minimize_by_title(self, name):
        """Minimise windows whose title matches ``name``; return a reply."""

    @abstractmethod
    def maximize_by_title(self, name):
        """Maximise windows whose title matches ``name``; return a reply."""

    @abstractmethod
    def has_input_focus(self, handle):
        """True when ``handle`` really owns the keyboard input focus."""

    @abstractmethod
    def force_focus(self, handle):
        """Force input focus onto ``handle`` (bypassing focus stealing prevention)."""

    @abstractmethod
    def send_key(self, handle, key, restore=True):
        """Focus ``handle``, send ``key``, restore focus; True on success."""

    @abstractmethod
    def send_keys(self, keys):
        """Send ``keys`` to the focused window; return True on success."""

    @abstractmethod
    def type_string(self, text):
        """Type ``text`` into the focused window; return True on success."""

    # -- pointer / keyboard ------------------------------------------------- #
    @abstractmethod
    def move_mouse(self, x, y):
        """Move the pointer to absolute ``(x, y)``; return a reply."""

    @abstractmethod
    def click(self, x, y, button=1, double=False):
        """Click at absolute ``(x, y)``; return a reply."""

    @abstractmethod
    def type_text(self, text):
        """Type ``text`` into the focused window; return a reply."""

    @abstractmethod
    def press_keys(self, keys):
        """Press a key/shortcut such as ``ctrl+s``; return a reply."""

    @abstractmethod
    def scroll(self, direction, amount=3):
        """Scroll the focused window; return a reply."""

    @abstractmethod
    def screen_size(self):
        """Return ``(width, height)`` of the display, or None."""

    @abstractmethod
    def can_send_keys(self):
        """True when this platform can focus a window and type keys into it."""

    # -- class-based lookup ------------------------------------------------- #
    def locate_by_class(self, keywords):
        """Handles of windows whose WM_CLASS contains any keyword (else []).

        Needed where the title is unhelpful -- a terminal's title is just the
        shell prompt. Platforms without a window-manager class concept inherit
        this empty result.
        """
        return []

    def close_by_class(self, keywords):
        """Close every window matching :meth:`locate_by_class`; True if any."""
        return False


class SystemController(ABC):
    """Applications, media, brightness, power and the desktop shell."""

    # -- applications ------------------------------------------------------- #
    @abstractmethod
    def launch_app(self, app):
        """Launch a desktop application object; return True on success."""

    @abstractmethod
    def close_app(self, app):
        """Close an application object; return True if anything was closed."""

    # -- media -------------------------------------------------------------- #
    @abstractmethod
    def media_control(self, action, page_fallback=None):
        """Pause/resume/next/previous/stop the active player; a spoken reply.

        ``page_fallback(key)`` may be called for next/previous when the player
        cannot skip tracks (e.g. drive the YouTube page's own shortcuts).
        """

    @abstractmethod
    def media_seek(self, seconds):
        """Seek the active player by ``seconds`` (may be negative); a reply."""

    @abstractmethod
    def now_playing(self):
        """Describe the current track, or report that nothing is playing."""

    @abstractmethod
    def media_players(self):
        """Return a newline/space separated list of active media player names."""

    @abstractmethod
    def media_available(self):
        """True when this platform can control the active media player."""

    @abstractmethod
    def pause_media_if_playing(self):
        """Pause active media; return an opaque resume token or None."""

    @abstractmethod
    def resume_media(self, token):
        """Resume media paused by :meth:`pause_media_if_playing` (never raises)."""

    # -- brightness --------------------------------------------------------- #
    @abstractmethod
    def get_brightness(self):
        """Return the screen brightness as a percent (1-100), or None."""

    @abstractmethod
    def set_brightness(self, percent):
        """Set the screen brightness (1-100); return True on success."""

    @abstractmethod
    def brightness_control(self, action, value=None):
        """Brightness up/down or set to a percent; return a spoken reply."""

    # -- desktop shell ------------------------------------------------------ #
    @abstractmethod
    def screenshot(self, path):
        """Capture the whole screen to ``path``; return the path or None."""

    @abstractmethod
    def lock_screen(self):
        """Lock the session; return a spoken reply."""

    @abstractmethod
    def notify(self, message, title="Jarvis"):
        """Show a desktop notification; return a spoken reply."""

    @abstractmethod
    def open_path(self, path):
        """Open a file, folder or URL; return a spoken reply."""

    @abstractmethod
    def read_clipboard(self):
        """Return the clipboard text, or None when it cannot be read."""

    @abstractmethod
    def find_files(self, query, limit=15):
        """Find files by name; return a spoken reply."""

    @abstractmethod
    def run_in_terminal(self, command):
        """Run ``command`` in a visible terminal; return a spoken reply."""

    def open_terminal(self):
        """Open an empty (default) terminal window; False when unsupported."""
        return False

    @abstractmethod
    def power(self, kind):
        """Run a confirmed power action; return a spoken reply."""
