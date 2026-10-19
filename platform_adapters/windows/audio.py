"""Windows audio adapter: ``sounddevice`` capture and ``pycaw`` volume."""

import os

from platform_adapters import _sd
from platform_adapters.base import AudioController


class WindowsAudioController(AudioController):
    """Microphone capture through sounddevice and the mixer through pycaw."""

    volume_backend = "pycaw"

    # -- capture (sounddevice) --------------------------------------------- #
    def record_command(self, device=None, *, seconds=None, output=None, fmt="raw"):
        # Windows has no arecord-style CLI; this is provided for interface
        # compatibility only and is never used by the capture path.
        return None

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

    # -- playback ----------------------------------------------------------- #
    def play(self, path):
        path = str(path)
        try:
            if path.lower().endswith(".wav"):
                import winsound
                winsound.PlaySound(path, winsound.SND_FILENAME)
                return True
        except Exception:
            pass
        try:
            os.startfile(path)                     # noqa: S606 (open with default app)
            return True
        except Exception:
            return False

    # -- volume (pycaw) ----------------------------------------------------- #
    def _endpoint(self):
        from ctypes import POINTER, cast

        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        speakers = AudioUtilities.GetSpeakers()
        try:
            interface = speakers.Activate(
                IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            return cast(interface, POINTER(IAudioEndpointVolume))
        except AttributeError:
            return speakers.EndpointVolume

    def get_volume(self):
        try:
            return round(self._endpoint().GetMasterVolumeLevelScalar() * 100)
        except Exception:
            return None

    def set_volume(self, percent):
        try:
            self._endpoint().SetMasterVolumeLevelScalar(
                max(0.0, min(1.0, int(percent) / 100.0)), None)
            return True
        except Exception:
            return False

    def adjust_volume(self, delta):
        current = self.get_volume()
        if current is None:
            return False
        return self.set_volume(max(0, min(100, current + int(delta))))

    def set_mute(self, muted):
        try:
            self._endpoint().SetMute(bool(muted), None)
            return True
        except Exception:
            return False

    def volume_control(self, action, value=None):
        if not _has_pycaw():
            print("[platform] Volume control needs pycaw: pip install pycaw comtypes")
            return "pycaw is not installed."
        return super().volume_control(action, value)


def _has_pycaw():
    try:
        import comtypes  # noqa: F401
        import pycaw  # noqa: F401
    except Exception:
        return False
    return True
