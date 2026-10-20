"""Shared ``sounddevice`` helpers for the Windows and macOS audio adapters.

``sounddevice`` (PortAudio) is the cross-platform backend both use for raw
microphone capture. Windows has no system IANA timezone/audio-server plumbing
and, importantly, ``select.select`` cannot wait on pipes there. The recorder
therefore exposes a small condition-variable reader instead of an OS pipe.
"""

import os
import threading
import time

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2
CHANNELS = 1

_DEFAULT_DEVICE_NAMES = {"", "default", "system", "auto"}


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


def _is_input(info):
    """True when a PortAudio device description can capture audio."""
    try:
        return int(info.get("max_input_channels") or 0) > 0
    except (AttributeError, TypeError, ValueError):
        return False


def _hostapi_names(sd):
    """Return host-API names by index, tolerating partial/no enumeration."""
    try:
        return [str(api.get("name") or "?") for api in sd.query_hostapis()]
    except Exception:
        return []


def _all_devices(sd):
    """Return the PortAudio device list, raising a useful error on failure."""
    return list(sd.query_devices())


def default_input_index(sd=None, devices=None):
    """Return the PortAudio index of the default input, or ``None``.

    ``query_devices()`` is called explicitly, as PortAudio's "default" alias is
    not a valid ``InputStream(device=...)`` on every Windows host API. The
    ``sd.default.device`` tuple is preferred; if it is unset we ask PortAudio
    for its default input description and locate the matching index.
    """
    sd = sd or require_sounddevice()
    devices = _all_devices(sd) if devices is None else list(devices)

    index = None
    try:
        default = sd.default.device
        index = default[0] if isinstance(default, (list, tuple)) else default
        index = int(index)
    except Exception:
        index = None
    if index is not None and 0 <= index < len(devices) and _is_input(devices[index]):
        return index

    try:
        info = sd.query_devices(kind="input")
        name = info.get("name")
        hostapi = info.get("hostapi")
        for position, device in enumerate(devices):
            if (_is_input(device) and device.get("name") == name
                    and device.get("hostapi") == hostapi):
                return position
    except Exception:
        pass

    for position, device in enumerate(devices):
        if _is_input(device):
            return position
    return None


def list_input_devices(sd=None):
    """Return every capture-capable device as plain dictionaries.

    Each entry has ``index``, ``name``, ``hostapi``, ``hostapi_name``,
    ``channels``, ``default_samplerate`` and ``default``. Used by
    ``main.py --list-mics`` and by tests that do not need PortAudio.
    """
    sd = sd or require_sounddevice()
    devices = _all_devices(sd)
    hostapis = _hostapi_names(sd)
    default_index = default_input_index(sd, devices)
    result = []
    for index, info in enumerate(devices):
        if not _is_input(info):
            continue
        hostapi = info.get("hostapi", -1)
        try:
            hostapi = int(hostapi)
        except (TypeError, ValueError):
            hostapi = -1
        result.append({
            "index": index,
            "name": str(info.get("name") or f"device {index}"),
            "hostapi": hostapi,
            "hostapi_name": (hostapis[hostapi]
                             if 0 <= hostapi < len(hostapis) else "unknown"),
            "channels": int(info.get("max_input_channels") or 0),
            "default_samplerate": info.get("default_samplerate"),
            "default": index == default_index,
        })
    return result


def _configured_device():
    """Return the mic preference from the environment or config."""
    value = os.getenv("JARVIS_MIC_DEVICE", "").strip()
    if value:
        return value
    try:
        import config
        return getattr(config, "MIC_DEVICE", None)
    except Exception:
        return None


def resolve_input_device(preference=None, sd=None):
    """Resolve a configured index/name to a PortAudio input index.

    ``JARVIS_MIC_DEVICE`` may be a number (``0``, ``1``, ...) or a
    case-insensitive substring of a device name. Anything invalid falls back
    to the system default input with a warning instead of opening the wrong
    device (or failing silently).
    """
    sd = sd or require_sounddevice()
    devices = _all_devices(sd)
    default = default_input_index(sd, devices)
    raw = _configured_device() if preference is None else preference

    if isinstance(raw, int):
        text = str(raw)
    else:
        text = str(raw or "").strip()
    if not text or text.lower() in _DEFAULT_DEVICE_NAMES:
        return default

    try:
        index = int(text)
    except (TypeError, ValueError):
        index = None

    if index is not None:
        if 0 <= index < len(devices) and _is_input(devices[index]):
            return index
        print(f"[audio] JARVIS_MIC_DEVICE={text!r} is not an input device; "
              "using the default microphone.", flush=True)
        return default

    inputs = [(position, device) for position, device in enumerate(devices)
              if _is_input(device)]
    exact = [(position, device) for position, device in inputs
             if str(device.get("name") or "").casefold() == text.casefold()]
    matches = exact or [(position, device) for position, device in inputs
                        if text.casefold() in
                        str(device.get("name") or "").casefold()]
    if matches:
        if len(matches) > 1 and not exact:
            print(f"[audio] JARVIS_MIC_DEVICE={text!r} matches "
                  f"{len(matches)} microphones; using "
                  f"{matches[0][1].get('name')!r}.", flush=True)
        return matches[0][0]

    print(f"[audio] No microphone name contains {text!r}; using the default "
          "microphone.", flush=True)
    return default


class _ChunkReader:
    """A binary-stream-like reader that tolerates silence and Windows.

    The capture pump feeds complete PCM chunks here. ``read_chunk`` waits at
    most until ``deadline`` and returns b"" on timeout/EOF, so a dead device can
    never block ``voice.listen`` forever. Unlike an OS pipe, this works with
    ``select`` unavailable on Windows.
    """

    def __init__(self):
        self._condition = threading.Condition()
        self._buffer = bytearray()
        self._eof = False

    @property
    def eof(self):
        with self._condition:
            return self._eof and not self._buffer

    def feed(self, data):
        if not data:
            return
        with self._condition:
            if self._eof:
                return
            self._buffer.extend(data)
            self._condition.notify_all()

    def close(self):
        with self._condition:
            self._eof = True
            self._condition.notify_all()

    def read_chunk(self, deadline, size):
        """Read up to ``size`` bytes, returning early at ``deadline``/EOF."""
        out = bytearray()
        while len(out) < size:
            with self._condition:
                while not self._buffer and not self._eof:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return bytes(out)
                    self._condition.wait(remaining)
                if self._buffer:
                    take = min(size - len(out), len(self._buffer))
                    out.extend(self._buffer[:take])
                    del self._buffer[:take]
                elif self._eof:
                    break
            if time.monotonic() >= deadline:
                break
        return bytes(out)


class SoundDeviceRecorder:
    """A Popen-like streaming recorder backed by ``sounddevice``.

    It exposes ``stdout.read_chunk(deadline, size)`` plus
    ``poll``/``terminate``/``wait``/``kill``, so the OS-agnostic gating code in
    :mod:`voice` can read it without ``select`` (which does not support pipes
    on Windows) and without ever blocking indefinitely.
    """

    def __init__(self, device=None):
        sd = require_sounddevice()
        self._sd = sd
        self._requested_device = device
        self.device = None
        self.error = None
        self.opened = False
        self._stop = threading.Event()
        self._closed = False
        self.stdout = _ChunkReader()
        self._thread = threading.Thread(target=self._pump, daemon=True,
                                        name="jarvis-sounddevice")
        self._thread.start()

    def _pump(self):
        chunk_frames = int(SAMPLE_RATE * 0.1)
        try:
            device = resolve_input_device(self._requested_device, sd=self._sd)
            self.device = device
            with self._sd.RawInputStream(
                    samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16",
                    device=device) as stream:
                self.opened = True
                while not self._stop.is_set():
                    data, _overflowed = stream.read(chunk_frames)
                    payload = bytes(data) if not hasattr(data, "tobytes") \
                        else data.tobytes()
                    if payload:
                        self.stdout.feed(payload)
        except Exception as exc:
            self.error = exc
            if not self._stop.is_set():
                if self.opened:
                    print("[audio] Microphone stream stopped unexpectedly.",
                          flush=True)
                else:
                    print("Failed to open microphone. Try a different device "
                          "index using JARVIS_MIC_DEVICE.", flush=True)
                print(f"[audio] {type(exc).__name__}: {exc}", flush=True)
        finally:
            self.stdout.close()

    def poll(self):
        return 0 if self._closed or self.stdout.eof else None

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

        resolved = resolve_input_device(device, sd=sd)
        frames = sd.rec(int(SAMPLE_RATE * float(seconds)), samplerate=SAMPLE_RATE,
                        channels=CHANNELS, dtype="int16", device=resolved)
        sd.wait()
        with wave.open(str(output), "wb") as wav:
            wav.setnchannels(CHANNELS)
            wav.setsampwidth(SAMPLE_WIDTH)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(frames.tobytes())
        return output
    except Exception as exc:
        print("Failed to open microphone. Try a different device index using "
              "JARVIS_MIC_DEVICE.", flush=True)
        print(f"[audio] {type(exc).__name__}: {exc}", flush=True)
        return None
