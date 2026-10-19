"""macOS audio adapter: ``sounddevice`` capture and ``osascript`` volume."""

import subprocess

from platform_adapters import _sd, common
from platform_adapters.base import AudioController


class MacOSAudioController(AudioController):
    """Microphone capture through sounddevice and the mixer through osascript."""

    volume_backend = "osascript"

    # -- capture (sounddevice) --------------------------------------------- #
    def record_command(self, device=None, *, seconds=None, output=None, fmt="raw"):
        return None                              # no CLI recorder on macOS

    def spawn_recorder(self, device=None):
        return _sd.SoundDeviceRecorder(device)

    def stop_recorder(self, proc):
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=1)
        except Exception:
            pass

    def record(self, seconds=3.0, output=None, device=None):
        if output is None:
            import tempfile
            output = tempfile.NamedTemporaryFile(delete=False, suffix=".wav").name
        return _sd.record_blocking(seconds, output, device)

    # -- playback (afplay) -------------------------------------------------- #
    def play(self, path):
        try:
            return subprocess.run(
                ["afplay", str(path)], timeout=120).returncode == 0
        except Exception:
            return False

    # -- volume (osascript) ------------------------------------------------- #
    def _osascript(self, script):
        rc, out = common.run(["osascript", "-e", script])
        return out.strip() if rc == 0 else None

    def get_volume(self):
        out = self._osascript("output volume of (get volume settings)")
        try:
            return int(out)
        except (TypeError, ValueError):
            return None

    def set_volume(self, percent):
        percent = max(0, min(100, int(percent)))
        return self._osascript(f"set volume output volume {percent}") is not None

    def adjust_volume(self, delta):
        current = self.get_volume()
        if current is None:
            return False
        return self.set_volume(current + int(delta))

    def set_mute(self, muted):
        value = "true" if muted else "false"
        return self._osascript(f"set volume output muted {value}") is not None

    def volume_control(self, action, value=None):
        if not common.has("osascript"):
            print("[platform] Volume control needs the macOS 'osascript' tool")
            return "osascript is not installed."
        return super().volume_control(action, value)
