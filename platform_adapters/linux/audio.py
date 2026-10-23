"""Linux audio adapter: ``arecord`` capture and ``pactl`` mixer control."""

import contextlib
import math
import os
import subprocess
import tempfile

from platform_adapters import common
from platform_adapters.base import AudioController

# Capture format shared with voice.py: headerless S16_LE mono PCM at 16 kHz.
_SAMPLE_RATE = 16000
_CHANNELS = 1
_SAMPLE_WIDTH = 2

_DEFAULT_SINK = "@DEFAULT_SINK@"


def _mic_device():
    """ALSA PCM name to capture from (``arecord -D``); default is ``default``."""
    return os.getenv("JARVIS_MIC_DEVICE", "").strip() or "default"


class LinuxAudioController(AudioController):
    """Recording through arecord and volume through pactl (PulseAudio/PipeWire)."""

    volume_backend = "pactl"

    # -- capture ------------------------------------------------------------ #
    def record_command(self, device=None, *, seconds=None, output=None, fmt="raw"):
        cmd = [
            "arecord",
            "-D", device or _mic_device(),
            "-f", "S16_LE",
            "-r", str(_SAMPLE_RATE),
            "-c", str(_CHANNELS),
            "-q",
            "-t", fmt,
        ]
        if seconds is not None:
            # arecord's -d takes whole seconds only.
            cmd += ["-d", str(max(1, math.ceil(seconds)))]
        if output is not None:
            cmd.append(str(output))
        return cmd

    def spawn_recorder(self, device=None):
        return subprocess.Popen(
            self.record_command(device),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )

    def stop_recorder(self, proc):
        if proc is None:
            return
        with contextlib.suppress(Exception):
            if proc.stdout:
                proc.stdout.close()
        if proc.poll() is None:
            with contextlib.suppress(Exception):
                proc.terminate()
            try:
                proc.wait(timeout=1)
            except Exception:
                with contextlib.suppress(Exception):
                    proc.kill()
                    proc.wait(timeout=1)

    def record(self, seconds=3.0, output=None, device=None):
        """Capture ``seconds`` of audio to a WAV file; return its path or None."""
        target = output or tempfile.NamedTemporaryFile(
            delete=False, suffix=".wav").name
        cmd = self.record_command(device, seconds=seconds, output=target, fmt="wav")
        try:
            proc = subprocess.run(
                cmd, stdin=common.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=max(2.0, float(seconds) + 3.0),
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None
        try:
            return target if os.path.getsize(target) > 0 else None
        except OSError:
            return None

    # -- playback ----------------------------------------------------------- #
    def play(self, path):
        """Play an audio file with the first available desktop player.

        Pygame is intentionally not used: it is no longer a dependency. ``ffplay``
        ships with ffmpeg, which Jarvis already requires for media and TTS.
        """
        candidates = (
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
            ["paplay", str(path)],
            ["aplay", "-q", str(path)],
        )
        for command in candidates:
            if not common.has(command[0]):
                continue
            rc, _ = common.run(command, timeout=120)
            return rc == 0
        return False

    # -- volume (pactl) ----------------------------------------------------- #
    def get_volume(self):
        rc, out = common.run(["pactl", "get-sink-volume", _DEFAULT_SINK])
        if rc == 0 and out:
            import re

            match = re.search(r"(\d+)%", out)
            if match:
                return int(match.group(1))
        return None

    def set_volume(self, percent):
        rc, _ = common.run(
            ["pactl", "set-sink-volume", _DEFAULT_SINK, f"{int(percent)}%"])
        return rc == 0

    def adjust_volume(self, delta):
        sign = "+" if int(delta) >= 0 else "-"
        rc, _ = common.run(
            ["pactl", "set-sink-volume", _DEFAULT_SINK, f"{sign}{abs(int(delta))}%"])
        return rc == 0

    def set_mute(self, muted):
        rc, _ = common.run(
            ["pactl", "set-sink-mute", _DEFAULT_SINK, "1" if muted else "0"])
        return rc == 0

    # Linux names the missing-tool message after pactl, as the user expects.
    def volume_control(self, action, value=None):
        if not common.has("pactl"):
            return "pactl is not installed."
        return super().volume_control(action, value)
