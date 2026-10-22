"""Shared ``sounddevice`` helpers for the Windows and macOS audio adapters.

``sounddevice`` (PortAudio) is the cross-platform backend both use for raw
microphone capture. It does not exist on the target machine here, so every
entry point raises a clear, actionable error instead of crashing when the
optional dependency is missing.

Two Windows details are handled here, because both used to make voice mode look
like "nothing was heard" while typed text still worked:

* PortAudio has no device called ``"default"`` -- that is an ALSA PCM name --
  so ALSA-style names are translated by :func:`normalize_device`.
* ``select()`` on Windows only accepts sockets, never pipes. The recorder's
  stdout must be readable by :func:`voice._read_chunk`, so on Windows the pipe
  is a ``socketpair`` (a real socket) instead of ``os.pipe()``.
"""

import array
import os
import socket
import sys
import threading

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2
CHANNELS = 1

#: WASAPI exclusive mode has to use the endpoint's native format.
CAPTURE_RATE = 48000

#: Windows ``select()`` is socket-only, so capture uses a socketpair there.
_IS_WINDOWS = os.name == "nt"

#: ALSA/PulseAudio pseudo-device names that mean "whatever the system picked".
_ALIASES = frozenset({"default", "sysdefault", "pulse", "pipewire", "auto"})


def _debug(message):
    """Print a capture warning only when ``JARVIS_DEBUG`` is enabled."""
    flag = os.getenv("JARVIS_DEBUG", "").strip().lower()
    if flag not in ("", "0", "false", "no", "off"):
        print(f"[audio] {message}", flush=True)


def normalize_device(device):
    """Translate a device override into something PortAudio accepts.

    ``voice._mic_device()`` defaults to ``"default"`` because that is a valid
    ``arecord -D`` PCM name, but PortAudio raises
    ``ValueError: No input device matching 'default'`` -- which silently killed
    microphone capture on Windows. ``None`` means "use the system default",
    which is the PortAudio equivalent. A numeric index (``JARVIS_MIC_DEVICE=9``)
    selects that device, and a plain name is passed through unchanged.
    """
    if device is None:
        return None
    value = str(device).strip()
    if not value or value.lower() in _ALIASES:
        return None
    if value.isdigit():
        return int(value)
    return value


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


def _capture_channels(sd, device):
    """How many channels to open the input stream with.

    Some Realtek/AMD Windows drivers deliver **digital silence** on a mono
    stream while the exact same endpoint records perfectly in stereo (seen on an
    HP EliteBook 845 G10: mono int16 -> rms 0, stereo int16 -> rms 5000+). So
    capture in stereo whenever the device supports it and fold it down to mono
    here; a mono-only device still gets a one-channel stream.
    """
    try:
        info = sd.query_devices(device, "input")
        return 2 if int(info.get("max_input_channels", 1)) >= 2 else 1
    except Exception:
        return 1


def _resample_to_16k(payload, rate):
    """Convert a mono S16_LE block at ``rate`` down to :data:`SAMPLE_RATE`.

    WASAPI exclusive mode only opens at the endpoint's native 48 kHz, while the
    gating/STT code expects 16 kHz. A 3:1 box average is used, which also acts
    as a crude anti-alias filter.
    """
    if rate == SAMPLE_RATE or not payload:
        return payload
    samples = array.array("h")
    samples.frombytes(payload)
    if sys.byteorder == "big":
        samples.byteswap()
    out = array.array("h")
    step = rate // SAMPLE_RATE
    if rate % SAMPLE_RATE == 0 and step > 1:
        for i in range(0, len(samples) - step + 1, step):
            out.append(int(sum(samples[i:i + step]) / step))
    else:
        count = int(len(samples) * SAMPLE_RATE / rate)
        for i in range(count):
            pos = i * rate / SAMPLE_RATE
            lo = int(pos)
            hi = min(lo + 1, len(samples) - 1)
            frac = pos - lo
            out.append(int(samples[lo] * (1 - frac) + samples[hi] * frac))
    if sys.byteorder == "big":
        out.byteswap()
    return out.tobytes()


def _wasapi_input(sd, device):
    """Return the WASAPI device index for ``device`` (matched by name)."""
    try:
        name = sd.query_devices(device, "input")["name"]
        apis = sd.query_hostapis()
        for index, info in enumerate(sd.query_devices()):
            if (info["max_input_channels"] > 0
                    and info["name"] == name
                    and "WASAPI" in apis[info["hostapi"]]["name"]):
                return index
    except Exception:
        return None
    return None


def _open_capture(sd, device, channels):
    """Open the best capture stream; returns ``(rate, channels, stream)``.

    On this Realtek/AMD Windows endpoint the shared-mode stream (MME,
    DirectSound and WASAPI shared alike) delivers ~0.2 s of audio and then
    digital silence forever, which looks exactly like "Jarvis cannot hear me".
    WASAPI **exclusive** mode bypasses the shared audio engine (and its APOs)
    and keeps delivering audio, so it is tried first. Anything else falls back
    to the old 16 kHz default-device path for machines where exclusive mode is
    unavailable (device busy, no WASAPI, other OS).
    """
    exclusive = os.getenv("JARVIS_MIC_EXCLUSIVE", "1").strip().lower() not in (
        "0", "false", "no", "off")
    if _IS_WINDOWS and exclusive:
        index = _wasapi_input(sd, device)
        if index is not None:
            for mode in (True, False):
                try:
                    settings = sd.WasapiSettings(exclusive=mode)
                except Exception:
                    break
                for ch in (channels, 1):
                    try:
                        stream = sd.RawInputStream(
                            device=index, samplerate=CAPTURE_RATE,
                            channels=ch, dtype="int16",
                            extra_settings=settings)
                    except Exception:
                        continue
                    _debug(f"capture path: WASAPI exclusive={mode} rate={CAPTURE_RATE}")
                    return CAPTURE_RATE, ch, stream

    for ch in (channels, 1):
        try:
            stream = sd.RawInputStream(
                samplerate=SAMPLE_RATE, channels=ch, dtype="int16",
                device=device)
        except Exception:
            continue
        _debug(f"capture path: default rate={SAMPLE_RATE}")
        return SAMPLE_RATE, ch, stream
    raise RuntimeError("no usable microphone input stream")


def _downmix(payload, channels):
    """Fold an interleaved S16_LE block down to mono.

    The louder channel wins: some mic arrays put the capsules in inverted phase,
    so averaging the two would cancel the voice out.
    """
    if channels <= 1 or not payload:
        return payload
    samples = array.array("h")
    samples.frombytes(payload[: len(payload) - len(payload) % (2 * channels)])
    if sys.byteorder == "big":
        samples.byteswap()
    left = samples[0::channels]
    right = samples[1::channels]
    if sum(x * x for x in right) > sum(x * x for x in left):
        mono = right
    else:
        mono = left
    if sys.byteorder == "big":
        mono.byteswap()
    return mono.tobytes()


class AutomaticGain:
    """Keep the captured level usable whatever the Windows mic volume is.

    A low system mic volume used to make Jarvis deaf: normal speech arrived at
    RMS ~50 while the start gate expects a few hundred. This is a small, bounded
    AGC: every 0.1 s block is scaled towards ``target_peak`` (gain between 1x and
    ``max_gain``), attacking instantly so a loud syllable never clips and
    releasing slowly so the level does not pump. Silence keeps the current gain,
    which only lifts the room noise by a few units -- still far below any gate.
    """

    def __init__(self, target_peak=12000, max_gain=6.0):
        self.target_peak = target_peak
        self.max_gain = max_gain
        self.gain = 1.0

    def apply(self, payload):
        if not payload:
            return payload
        samples = array.array("h")
        samples.frombytes(payload)
        if sys.byteorder == "big":
            samples.byteswap()
        peak = max((abs(x) for x in samples), default=0)
        if peak:
            desired = min(self.max_gain, self.target_peak / peak)
            if peak * self.gain > 30000:
                self.gain = 30000 / peak            # instant attack: never clip
            elif desired < self.gain:
                self.gain = max(desired, self.gain * 0.8)
            else:
                self.gain = min(desired, self.gain * 1.15)
        if self.gain <= 1.001:
            return payload
        scaled = array.array(
            "h", (max(-32768, min(32767, int(x * self.gain))) for x in samples))
        if sys.byteorder == "big":
            scaled.byteswap()
        return scaled.tobytes()


#: Extra automatic gain is opt-in (JARVIS_MIC_AGC=1): with the working WASAPI
#: exclusive path the room noise already sits at rms ~70, and 6x gain would push
#: it over the start gate.
_AGC_ENABLED = os.getenv("JARVIS_MIC_AGC", "0").strip().lower() not in (
    "0", "false", "no", "off")


class SoundDeviceRecorder:
    """A Popen-like streaming recorder backed by ``sounddevice``.

    It exposes a real binary ``stdout`` of raw S16_LE mono PCM plus
    ``poll``/``terminate``/``wait``/``kill``, so the OS-agnostic gating code in
    :mod:`voice` can read it exactly like the Linux ``arecord`` process.
    """

    def __init__(self, device=None):
        sd = require_sounddevice()
        self._sd = sd
        self._device = normalize_device(device)
        self._stop = threading.Event()
        self._closed = False
        self._agc = AutomaticGain() if _AGC_ENABLED else None

        # ``voice`` waits for data with select(), so the reader end has to be a
        # socket on Windows (select() rejects pipes with WinError 10038).
        self._writer_sock = None
        self._write_fd = None
        if _IS_WINDOWS:
            self._reader_sock, self._writer_sock = socket.socketpair()
            self.stdout = self._reader_sock.makefile("rb", buffering=0)
        else:
            self._reader_sock = None
            read_fd, self._write_fd = os.pipe()
            self.stdout = os.fdopen(read_fd, "rb", buffering=0)

        self._thread = threading.Thread(target=self._pump, daemon=True,
                                        name="jarvis-mic")
        self._thread.start()

    # -- plumbing ----------------------------------------------------------- #
    def _write(self, payload):
        """Hand one PCM block to the reader end (a no-op once it is closed)."""
        if self._writer_sock is not None:
            self._writer_sock.sendall(payload)
        elif self._write_fd is not None:
            os.write(self._write_fd, payload)

    def _close_writer(self):
        """Close the writer end so a blocked reader sees EOF. Idempotent."""
        if self._writer_sock is not None:
            sock, self._writer_sock = self._writer_sock, None
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        if self._write_fd is not None:
            fd, self._write_fd = self._write_fd, None
            try:
                os.close(fd)
            except OSError:
                pass

    def _pump(self, device=None):
        com = _co_initialize()
        channels = _capture_channels(self._sd, self._device)
        try:
            rate, channels, stream = _open_capture(self._sd, self._device,
                                                   channels)
            chunk_frames = int(rate * 0.1)
            _debug(f"capture open: device={self._device} channels={channels} "
                   f"rate={rate}")
            blocks = 0
            with stream:
                while not self._stop.is_set():
                    data, _overflowed = stream.read(chunk_frames)
                    raw = bytes(data) if not hasattr(data, "tobytes") \
                        else data.tobytes()
                    payload = _downmix(raw, channels)
                    payload = _resample_to_16k(payload, rate)
                    if payload and self._agc is not None:
                        payload = self._agc.apply(payload)
                    blocks += 1
                    if self._agc is not None and blocks % 10 == 0:
                        _debug(f"agc gain={self._agc.gain:.2f}")
                    if payload:
                        self._write(payload)
        except Exception as exc:
            # Never fail silently: a missing/blocked/busy microphone is the
            # difference between "voice mode works" and "it hears nothing".
            if not self._stop.is_set():
                print(f"[audio] microphone capture failed: "
                      f"{type(exc).__name__}: {exc}", flush=True)
            _debug(f"capture stopped: {type(exc).__name__}: {exc}")
        finally:
            self._close_writer()
            if com is not None:
                try:
                    com.CoUninitialize()
                except Exception:
                    pass

    # -- Popen-compatible surface ------------------------------------------- #
    def poll(self):
        return 0 if self._closed else None

    def terminate(self):
        self._stop.set()
        # Unblock a reader waiting for the next chunk as well as the pump.
        self._close_writer()

    def kill(self):
        self.terminate()

    def wait(self, timeout=None):
        self._thread.join(timeout)
        self._closed = True
        self._close_writer()
        try:
            self.stdout.close()
        except Exception:
            pass
        if self._reader_sock is not None:
            try:
                self._reader_sock.close()
            except OSError:
                pass
            self._reader_sock = None
        return 0


def _co_initialize():
    """Initialise COM on the calling thread; WASAPI streams need it.

    PortAudio sets up its WASAPI backend on the thread that first touched it
    (the main thread). The recorder opens the stream from its own worker
    thread, where COM is missing -- there every WASAPI device is "invalid" and
    even the default device is "unavailable". Returns the ``ole32`` handle to
    balance with ``CoUninitialize`` (or ``None`` when COM is already in another
    apartment, which is still fine for capture).
    """
    if not _IS_WINDOWS:
        return None
    try:
        import ctypes

        ole32 = ctypes.WinDLL("ole32")
        result = ole32.CoInitializeEx(None, 0x2)   # COINIT_MULTITHREADED
    except Exception:
        return None
    return ole32 if result >= 0 else None


def record_blocking(seconds, output, device=None):
    """Record ``seconds`` to a WAV file via sounddevice; return the path or None."""
    com = _co_initialize()
    try:
        sd = require_sounddevice()
        import wave

        target = normalize_device(device)
        channels = _capture_channels(sd, target)
        rate, channels, stream = _open_capture(sd, target, channels)
        blocks = max(1, int(round(float(seconds) / 0.1)))
        mono = bytearray()
        with stream:
            for _ in range(blocks):
                data, _overflowed = stream.read(int(rate * 0.1))
                raw = bytes(data) if not hasattr(data, "tobytes") \
                    else data.tobytes()
                mono += _resample_to_16k(_downmix(raw, channels), rate)
        with wave.open(str(output), "wb") as wav:
            wav.setnchannels(CHANNELS)
            wav.setsampwidth(SAMPLE_WIDTH)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(bytes(mono))
        return output
    except Exception as exc:
        _debug(f"recording failed: {type(exc).__name__}: {exc}")
        return None
    finally:
        if com is not None:
            try:
                com.CoUninitialize()
            except Exception:
                pass
