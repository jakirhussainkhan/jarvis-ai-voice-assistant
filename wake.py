"""Offline wake word ("Hey Jarvis") so you can talk without clicking anything.

Uses `openWakeWord <https://github.com/dscripka/openWakeWord>`_, which is a
*purpose-built* wake-word engine: a small ONNX model that scores 80 ms frames of
16 kHz audio between 0 and 1. It runs entirely on this machine (no network, no
account, no API key) and costs roughly 1-2% of one CPU core.

Nothing here is required for Jarvis to work: if ``openwakeword`` is not
installed, :func:`available` returns False, :func:`install_hint` prints the exact
command to install it, and click-to-talk keeps working exactly as before.

Two pieces, both importable without the dependency:

* :class:`Detector` -- wraps the model. ``feed(pcm_bytes)`` takes raw S16_LE
  mono 16 kHz audio, re-frames it into the 80 ms frames the model wants, and
  returns True when the wake word was heard.
* :class:`Listener` -- a daemon thread that streams the microphone into a
  detector. It *releases the microphone* whenever Jarvis is busy (listening,
  thinking or speaking) so the two capture paths never fight over the device, and
  it will not listen at all while the mic is muted.

The pretrained models are licensed CC-BY-NC-SA 4.0 (non-commercial); the code here
is the project's own, and the user installs the models themselves.
"""

import array
import math
import os
import threading
import time
import wave
from io import BytesIO

import voice

# The model wants audio in multiples of 80 ms; 1280 samples = 80 ms at 16 kHz.
FRAME_SAMPLES = 1280
FRAME_BYTES = FRAME_SAMPLES * 2

#: Default model shipped with openWakeWord that matches our name.
DEFAULT_MODEL = "hey jarvis"


def _truthy(name, default):
    """Read a boolean-ish env var; ``default`` when it is unset."""
    raw = os.getenv(name, "").strip().lower()
    if raw in ("", "none"):
        return default
    return raw not in ("0", "false", "no", "off")


def _env_float(name, default, minimum=None):
    """Read a float env var, falling back to ``default`` when unusable.

    A typo in .env must never stop Jarvis from starting, so anything that does not
    parse (or is below ``minimum``) silently becomes the default.
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if minimum is not None and value < minimum:
        return default
    return value


# Set JARVIS_DEBUG=1 to print wake-word diagnostics.
_DEBUG = _truthy("JARVIS_DEBUG", False)

# A short two-tone chime makes it obvious Jarvis heard you. JARVIS_WAKE_DING=0
# turns it off.
_DING = _truthy("JARVIS_WAKE_DING", True)

# Score (0..1) a frame must reach to count as the wake word. 0.5 is the value the
# models are trained for; raise it if you get false activations, lower it if
# Jarvis misses you.
_THRESHOLD = _env_float("JARVIS_WAKE_THRESHOLD", 0.5, minimum=0.01)

# Opt-in extras: `JARVIS_WAKE_VAD` uses the bundled Silero VAD to reject
# non-speech noise, and `JARVIS_WAKE_SPEEX` enables Speex noise suppression (only
# if the optional speexdsp wheel is installed).
_VAD = _env_float("JARVIS_WAKE_VAD", 0.0)
_SPEEX = _truthy("JARVIS_WAKE_SPEEX", False)


def _dbg(*parts):
    if _DEBUG:
        print("[wake]", *parts, flush=True)


def install_hint():
    """The exact command that installs the wake-word engine for this OS."""
    if os.name == "nt":
        return (r".\venv\Scripts\python.exe -m pip install openwakeword "
                r"onnxruntime")
    return "./venv/bin/pip install openwakeword onnxruntime"


def available():
    """True when openWakeWord can actually be used on this machine."""
    try:
        import openwakeword  # noqa: F401
    except Exception:
        return False
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    return True


def enabled(model=None):
    """True when the wake word is wanted *and* usable.

    On by default when openWakeWord is installed (installing it is the deliberate
    act), and switchable with ``JARVIS_WAKE=0`` or ``JARVIS_WAKE_MODEL=off``.
    """
    if model_name(model) in ("", "off", "none", "0", "false"):
        return False
    return _truthy("JARVIS_WAKE", True) and available()


def model_name(explicit=None):
    """The configured wake phrase, e.g. ``"hey jarvis"``."""
    raw = (explicit or os.getenv("JARVIS_WAKE_MODEL", "")).strip()
    if raw:
        return raw.replace("_", " ").lower()
    return DEFAULT_MODEL


class Detector:
    """Framing + thresholding in front of a wake-word engine.

    All the logic that decides *whether* the phrase was heard lives here, so a
    test can swap in a fake ``backend`` (anything with ``score(bytes)->float``
    and ``reset()``) and never need openWakeWord, numpy or a microphone.
    """

    def __init__(self, model=None, threshold=_THRESHOLD, vad=_VAD, speex=_SPEEX,
                 backend=None):
        self.model = model_name(model)
        self.threshold = float(threshold)
        self._buffer = bytearray()
        self._backend = backend if backend is not None else _OnnxBackend(
            self.model, vad, speex)

    def feed(self, pcm):
        """Feed raw S16_LE mono 16 kHz bytes. True if the wake word was heard."""
        if not pcm:
            return False
        self._buffer.extend(pcm)
        fired = False
        while len(self._buffer) >= FRAME_BYTES:
            frame = bytes(self._buffer[:FRAME_BYTES])
            del self._buffer[:FRAME_BYTES]
            try:
                score = self._backend.score(frame)
            except Exception as exc:                 # a bad frame must not kill us
                _dbg(f"score failed: {type(exc).__name__}: {exc}")
                continue
            if score >= self.threshold:
                _dbg(f"detected {self.model!r} score={score:.2f}")
                fired = True
                # Drop the rest of the buffer so the tail of the phrase cannot
                # trigger a second detection straight away.
                self._buffer.clear()
                break
        return fired

    def reset(self):
        """Clear buffered audio and the engine's internal state."""
        self._buffer.clear()
        self._backend.reset()


class _OnnxBackend:
    """The real openWakeWord model: raw frame bytes in, one score out."""

    def __init__(self, name, vad, speex):
        import numpy as np
        from openwakeword.model import Model
        from openwakeword.utils import download_models

        self._np = np
        self._model = _load(download_models, Model, name, vad, speex)

    def score(self, frame):
        samples = self._np.frombuffer(frame, dtype=self._np.int16)
        scores = self._model.predict(samples)
        return max(scores.values()) if scores else 0.0

    def reset(self):
        try:
            self._model.reset()
        except Exception:
            pass


def _load(download_models, Model, name, vad, speex):
    """Construct the model, downloading it once if necessary. Raises on failure."""
    try:
        # No-op when the models are already on disk; needs the network only the
        # very first time (they ship with openWakeWord's GitHub releases).
        download_models()
    except Exception as exc:
        _dbg(f"download_models skipped: {type(exc).__name__}: {exc}")

    kwargs = {"wakeword_models": [name], "inference_framework": "onnx"}
    if vad:
        kwargs["vad_threshold"] = float(vad)
    if speex:
        kwargs["enable_speex_noise_suppression"] = True
    try:
        return Model(**kwargs)
    except Exception as exc:
        _dbg(f"model load with extras failed ({exc}); retrying plain")
        # A missing optional dependency (e.g. speexdsp) must not disable the
        # whole feature.
        return Model(wakeword_models=[name])


# --------------------------------------------------------------------------- #
# the listening thread
# --------------------------------------------------------------------------- #

class Listener(threading.Thread):
    """Streams the mic into a detector and calls ``on_wake`` when it fires.

    The microphone is held only while Jarvis is idle. :meth:`pause` releases it
    (and blocks until it really is released) so :func:`voice.listen` can take it
    over; :meth:`resume` hands it back. The listener also pauses itself
    immediately on a detection, *before* calling ``on_wake``, so the follow-up
    capture never collides with it.
    """

    def __init__(self, on_wake, detector=None, device=None, chime=True):
        super().__init__(daemon=True, name="jarvis-wake")
        self._on_wake = on_wake
        self._detector = detector
        self._device = device
        self._chime = chime and _DING
        self._stop_event = threading.Event()
        self._resume = threading.Event()
        self._resume.set()
        self._released = threading.Event()
        self._released.set()
        self.status = "starting"
        self.error = None

    # -- lifecycle -------------------------------------------------------- #
    def stop(self):
        """Shut the listener down for good."""
        self._stop_event.set()
        self._resume.set()

    def pause(self, timeout=2.0):
        """Release the microphone and wait until it really is free."""
        self._resume.clear()
        self._released.wait(timeout)

    def resume(self):
        """Allow listening again."""
        if not self._stop_event.is_set():
            self._resume.set()

    @property
    def paused(self):
        return not self._resume.is_set()

    # -- internals -------------------------------------------------------- #
    def _open(self):
        """Start arecord. Returns the Popen, or None."""
        try:
            return voice._spawn_arecord(self._device)
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            _dbg(f"could not open the microphone: {self.error}")
            return None

    def _close(self, proc):
        if proc is not None:
            voice._stop_arecord(proc)
        self._released.set()

    def _read(self, proc):
        """One bounded read (a 0.1 s chunk, or b"" if the stream stalls)."""
        deadline = time.monotonic() + 1.0
        try:
            fd = proc.stdout.fileno()
        except Exception:
            return b""
        try:
            return voice._read_chunk(proc.stdout, fd, deadline)
        except Exception:
            return b""

    def run(self):
        proc = None
        try:
            if self._detector is None:
                try:
                    self._detector = Detector()
                except Exception as exc:
                    self.error = f"{type(exc).__name__}: {exc}"
                    self.status = "unavailable"
                    print(f"[wake] wake word unavailable ({self.error}). "
                          f"Install it with: {install_hint()}", flush=True)
                    return
            self.status = "ready"
            _dbg(f"listening for {self._detector.model!r} "
                 f"(threshold={self._detector.threshold})")

            while not self._stop_event.is_set():
                if not self._resume.is_set() or voice.is_mic_muted():
                    self._close(proc)
                    proc = None
                    self.status = "paused"
                    self._stop_event.wait(0.1)
                    continue
                if proc is None:
                    proc = self._open()
                    if proc is None:
                        self.status = "no-microphone"
                        self._stop_event.wait(1.0)
                        continue
                    self._released.clear()
                    self._detector.reset()
                    self.status = "ready"
                pcm = self._read(proc)
                if not pcm:
                    # Nothing came back: nap briefly so a broken stream cannot
                    # spin this loop, then try again.
                    self._stop_event.wait(0.02)
                    continue
                try:
                    fired = self._detector.feed(pcm)
                except Exception as exc:
                    _dbg(f"detector error: {type(exc).__name__}: {exc}")
                    continue
                if not fired:
                    continue
                # Heard it: give the mic back *before* Jarvis starts recording.
                self._stop_listening()
                self._resume.clear()
                self._close(proc)
                proc = None
                self.status = "heard"
                if self._chime:
                    chime()
                try:
                    self._on_wake()
                except Exception as exc:
                    _dbg(f"on_wake failed: {type(exc).__name__}: {exc}")
                # Wait for the caller to say "done"; never hang forever.
                if not self._resume.wait(90.0):
                    # A wedged caller must not leave the wake word dead.
                    _dbg("no resume() within 90s; listening again anyway")
                    self._resume.set()
        finally:
            self._release_now(proc)

    def _release_now(self, proc):
        self._close(proc)
        # Remember *why* we stopped: a missing engine is a fixable condition the
        # UI reports, not just "stopped".
        if self.status != "unavailable":
            self.status = "stopped"

    def _stop_listening(self):
        """Clear any stale Stop flag so the upcoming listen is not killed."""
        try:
            voice._stop_listening.clear()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# the "I heard you" chime
# --------------------------------------------------------------------------- #

_CHIME = None


def chime():
    """Play a short two-tone chime. Best effort -- never raises.

    The audio is generated here and held in memory, so there is no asset to ship.
    Playback uses sounddevice (the same PortAudio backend as speech) on a daemon
    thread so the wake listener is never blocked.
    """
    global _CHIME
    try:
        import sounddevice as sd

        if _CHIME is None:
            wav = _chime_wav()
            with wave.open(BytesIO(wav), "rb") as handle:
                rate = handle.getframerate()
                frames = handle.readframes(handle.getnframes())
            _CHIME = (rate, frames)

        rate, frames = _CHIME

        def play():
            try:
                with sd.RawOutputStream(
                    samplerate=rate, channels=1, dtype="int16"
                ) as stream:
                    stream.write(frames)
            except Exception as exc:
                _dbg(f"chime playback failed: {type(exc).__name__}: {exc}")

        threading.Thread(target=play, daemon=True, name="jarvis-chime").start()
    except Exception as exc:
        _dbg(f"chime failed: {type(exc).__name__}: {exc}")


def _chime_wav(rate=voice._SAMPLE_RATE):
    """Build a short WAV in memory: a soft rising two-tone ding."""
    frames = array.array("h")
    for freq, seconds in ((660.0, 0.10), (880.0, 0.14)):
        total = int(rate * seconds)
        for i in range(total):
            # Sine wave with a short fade in and out so there is no click.
            env = min(1.0, i / (rate * 0.01), (total - i) / (rate * 0.02))
            value = 0.28 * env * 32767 * math.sin(2 * math.pi * freq * i / rate)
            frames.append(int(value))
    payload = frames.tobytes()
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(payload)
    buffer.seek(0)
    return buffer.getvalue()
