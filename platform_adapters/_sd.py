"""Shared ``sounddevice`` helpers for the Windows and macOS audio adapters.

``sounddevice`` (PortAudio) is the cross-platform backend both use for raw
microphone capture. It does not exist on the target machine here, so every
entry point raises a clear, actionable error instead of crashing when the
optional dependency is missing.
"""

import os
import threading

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2
CHANNELS = 1


def require_sounddevice():
    """Import and return ``sounddevice``, or raise a clear install hint."""
    try:
        import sounddevice as sd
    except Exception as exc:                              # not installed
        raise RuntimeError(
            "sounddevice is not installed -- run 'pip install sounddevice' "
            "to enable microphone capture on this platform"
        ) from exc
    return sd


class SoundDeviceRecorder:
    """A Popen-like streaming recorder backed by ``sounddevice``.

    It exposes a real binary ``stdout`` of raw S16_LE mono PCM plus
    ``poll``/``terminate``/``wait``/``kill``, so the OS-agnostic gating code in
    :mod:`voice` can read it exactly like the Linux ``arecord`` process.
    """

    def __init__(self, device=None):
        sd = require_sounddevice()
        self._sd = sd
        self._stop = threading.Event()
        self._closed = False
        read_fd, self._write_fd = os.pipe()
        self.stdout = os.fdopen(read_fd, "rb", buffering=0)
        self._thread = threading.Thread(target=self._pump, args=(device,), daemon=True)
        self._thread.start()

    def _pump(self, device):
        chunk_frames = int(SAMPLE_RATE * 0.1)
        try:
            with self._sd.RawInputStream(
                    samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16",
                    device=device) as stream:
                while not self._stop.is_set():
                    data, _overflowed = stream.read(chunk_frames)
                    payload = bytes(data) if not hasattr(data, "tobytes") \
                        else data.tobytes()
                    if payload:
                        try:
                            os.write(self._write_fd, payload)
                        except OSError:
                            break
        except Exception:
            pass
        finally:
            try:
                os.close(self._write_fd)
            except OSError:
                pass

    def poll(self):
        return 0 if self._closed else None

    def terminate(self):
        self._stop.set()

    def kill(self):
        self._stop.set()

    def wait(self, timeout=None):
        self._thread.join(timeout)
        self._closed = True
        return 0


def record_blocking(seconds, output, device=None):
    """Record ``seconds`` to a WAV file via sounddevice; return the path or None."""
    try:
        sd = require_sounddevice()
        import wave

        frames = sd.rec(int(SAMPLE_RATE * float(seconds)), samplerate=SAMPLE_RATE,
                        channels=CHANNELS, dtype="int16", device=device)
        sd.wait()
        with wave.open(str(output), "wb") as wav:
            wav.setnchannels(CHANNELS)
            wav.setsampwidth(SAMPLE_WIDTH)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(frames.tobytes())
        return output
    except Exception:
        return None
