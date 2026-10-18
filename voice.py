"""Speech recognition and text-to-speech I/O for Jarvis."""

import os

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

if os.path.exists(os.environ.get("XDG_RUNTIME_DIR", "") + "/pulse/native"):
    os.environ.setdefault("SDL_AUDIODRIVER", "pulseaudio")

import array
import asyncio
import contextlib
import ctypes
import tempfile
import time

import edge_tts
import pygame
import speech_recognition as sr

from config import STT_LANG, TTS_VOICE

# Hard bounds so a silent, missing, busy or blocked microphone can never stall
# the caller (and therefore the worker loop) indefinitely.
_LISTEN_TIMEOUT = 4        # max seconds to wait for speech to *start*
_PHRASE_TIME_LIMIT = 8     # max seconds of a single utterance
_RECOGNIZE_TIMEOUT = 8     # max seconds for the speech-to-text HTTP round-trip

# Fixed RMS gate: no ambient auto-calibration and no dynamic threshold.
# Measured on this machine's onboard mic (ALC245): idle ambient RMS is ~40,
# while speech lands in the thousands, so 300 separates the two cleanly.
_ENERGY_THRESHOLD = 300

# The onboard mic emits a loud, clipped transient every time the capture stream
# opens, and it re-appears whenever the device has been idle for a few seconds
# (e.g. while TTS plays or the agent runs). Left alone it trips the energy gate
# and gets mistaken for speech. We discard warm-up chunks until the input
# settles, bounded by _WARMUP_MAX_SECONDS.
_WARMUP_MAX_SECONDS = 1.5

# Set JARVIS_DEBUG=1 to print capture / recognition diagnostics.
_DEBUG = os.getenv("JARVIS_DEBUG", "").strip().lower() not in ("", "0", "false", "no")


def _dbg(*parts):
    if _DEBUG:
        print("[voice]", *parts, flush=True)


def _samples(buf, width=2):
    """Raw signed-16-bit mono bytes -> array of ints (empty if not 16-bit)."""
    if width != 2 or not buf:
        return array.array("h")
    out = array.array("h")
    out.frombytes(buf[: len(buf) - (len(buf) % 2)])
    return out


def _rms(buf, width=2):
    samples = _samples(buf, width)
    if not samples:
        return 0
    return (sum(x * x for x in samples) / len(samples)) ** 0.5


def _peak(buf, width=2):
    samples = _samples(buf, width)
    return max((abs(x) for x in samples), default=0)


# Suppress ALSA errors in PyAudio
ERROR_HANDLER_FUNC = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p)
def py_error_handler(filename, line, function, err, fmt):
    pass
c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)
try:
    asound = ctypes.cdll.LoadLibrary('libasound.so')
    asound.snd_lib_error_set_handler(c_error_handler)
except OSError:
    pass

# Suppress JACK errors in PortAudio (libjack prints these itself, so the
# libasound handler above cannot reach them).
try:
    jack = ctypes.cdll.LoadLibrary('libjack.so.0')
    JACK_ERROR_FUNC = ctypes.CFUNCTYPE(None, ctypes.c_char_p)
    jack_error_handler = JACK_ERROR_FUNC(lambda msg: None)
    jack.jack_set_error_function(jack_error_handler)
except OSError:
    pass


def _pick_input_device():
    """Index of the input device Jarvis should capture from.

    Priority order:

    1. ``JARVIS_MIC_INDEX`` env var (explicit index override), if set.
    2. The system default input device (named ``default``).
    3. The PipeWire/Pulse gateway (named ``pipewire`` / ``pulse``).
    4. Onboard analog capture (``ALC245`` / ``hw:1,0``) as a last resort.

    Direct ALSA access to the onboard codec (``hw:1,0``) returns a loud, clipped
    signal while PipeWire owns the device, which trips the energy gate and makes
    every ``listen()`` time out. The system default / PipeWire device captures
    the voice correctly, so it is preferred.
    """
    override = os.getenv("JARVIS_MIC_INDEX", "").strip()
    if override:
        try:
            index = int(override)
            _dbg(f"JARVIS_MIC_INDEX override -> device {index}")
            return index
        except ValueError:
            _dbg(f"JARVIS_MIC_INDEX={override!r} is not an integer; ignoring it")

    try:
        import pyaudio
    except ImportError:
        return None
    try:
        pa = pyaudio.PyAudio()
    except Exception:
        return None
    try:
        devices = []
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            devices.append((
                i,
                (info.get("name") or "").lower(),
                info.get("maxInputChannels", 0),
            ))
        # Highest priority first; first input channel matching wins.
        for hint in ("default", "pipewire", "pulse", "alc245", "hw:1,0"):
            for i, name, channels in devices:
                if channels > 0 and hint in name:
                    return i
    except Exception:
        return None
    finally:
        pa.terminate()
    return None


def _flush_warmup(src):
    """Discard the mic's stream-open transient until the input has settled.

    Returns ``(chunks_discarded, seconds_discarded)``.
    """
    deadline = time.monotonic() + _WARMUP_MAX_SECONDS
    discarded = 0
    while time.monotonic() < deadline:
        buf = src.stream.read(src.CHUNK)
        if not buf:
            break
        if _rms(buf, src.SAMPLE_WIDTH) < _ENERGY_THRESHOLD:
            break
        discarded += 1
    return discarded, discarded * src.CHUNK / src.SAMPLE_RATE


def listen():
    """Capture one utterance from the microphone.

    Returns the recognised text in lower case, or "" when nothing usable was
    heard (timeout, silence, or no microphone). Bounded by the *_TIMEOUT
    constants above so it always returns instead of blocking forever.
    """
    rec = sr.Recognizer()
    rec.energy_threshold = _ENERGY_THRESHOLD
    rec.dynamic_energy_threshold = False
    rec.operation_timeout = _RECOGNIZE_TIMEOUT

    idx = _pick_input_device()
    _dbg(f"device={idx} threshold={rec.energy_threshold} "
         f"dynamic={rec.dynamic_energy_threshold}")

    try:
        print("🎤 Listening...", flush=True)
        with sr.Microphone(device_index=idx) as src:
            dropped, secs = _flush_warmup(src)
            _dbg(f"warm-up: discarded {dropped} chunk(s) (~{secs:.2f}s)")
            audio = rec.listen(
                src,
                timeout=_LISTEN_TIMEOUT,
                phrase_time_limit=_PHRASE_TIME_LIMIT,
            )
    except sr.WaitTimeoutError:
        _dbg(f"TIMEOUT: no speech above threshold within {_LISTEN_TIMEOUT}s")
        return ""
    except Exception as exc:
        _dbg(f"capture error: {type(exc).__name__}: {exc}")
        return ""

    rms = 0
    if _DEBUG:
        raw = audio.get_raw_data(convert_rate=16000, convert_width=2)
        rms = _rms(raw)
        peak = _peak(raw)
        _dbg(f"captured {len(audio.frame_data)} bytes, rms={rms:.0f} peak={peak}")
        if peak >= 32767:
            _dbg("WARNING: peak at full scale (32768) -> input is CLIPPING; "
                 "lower the mic gain in the system mixer")

    try:
        text = (rec.recognize_google(audio, language=STT_LANG) or "").strip()
    except sr.UnknownValueError:
        _dbg(f"UNKNOWN_VALUE: audio captured (rms={rms:.0f}) but no words recognised")
        return ""
    except sr.RequestError as exc:
        # Network / API error: surface it loudly, never swallow it silently.
        print(f"[voice] REQUEST_ERROR from Google STT: {type(exc).__name__}: {exc}",
              flush=True)
        return ""
    except Exception as exc:
        _dbg(f"recognition error: {type(exc).__name__}: {exc}")
        return ""

    _dbg(f"recognised: {text!r}")
    if not text:
        return ""
    print(f"👤 {text}", flush=True)
    return text.lower()


def speak(text):
    """Synthesise `text` with edge-tts and play it through pygame."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3").name
    try:
        asyncio.run(edge_tts.Communicate(text, voice=TTS_VOICE).save(tmp))

        pygame.mixer.init()
        pygame.mixer.music.load(tmp)
        pygame.mixer.music.play()
        clock = pygame.time.Clock()
        while pygame.mixer.music.get_busy():
            clock.tick(10)
        pygame.mixer.music.unload()
        pygame.mixer.quit()
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)

    print(f"🤖 {text}", flush=True)
