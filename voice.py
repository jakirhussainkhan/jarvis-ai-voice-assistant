"""Speech recognition and text-to-speech I/O for Jarvis."""

import os

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

if os.path.exists(os.environ.get("XDG_RUNTIME_DIR", "") + "/pulse/native"):
    os.environ.setdefault("SDL_AUDIODRIVER", "pulseaudio")

import array
import asyncio
import contextlib
import queue
import re
import select
import subprocess
import sys
import tempfile
import threading
import time
import wave
from collections import deque

import edge_tts
import pygame
import speech_recognition as sr

from config import BARGE_HOLD, BARGE_THRESHOLD, STT_LANG, TTS_VOICE
from platform_adapters import Audio

def _env_int(name, default, minimum):
    """Read an int from the environment, clamped and never raising."""
    try:
        return max(minimum, int(os.getenv(name, "").strip() or default))
    except ValueError:
        return max(minimum, default)


def _env_float(name, default, minimum):
    """Read a float from the environment, clamped and never raising."""
    try:
        return max(minimum, float(os.getenv(name, "").strip() or default))
    except ValueError:
        return max(minimum, default)


# Hard bounds so a silent, missing, busy or blocked microphone can never stall
# the caller (and therefore the worker loop) indefinitely. All of them are tunable
# because the right values depend on how you speak and how noisy the room is.
_LISTEN_TIMEOUT = _env_int("JARVIS_LISTEN_TIMEOUT", 30, 5)
# ^ max seconds to wait for speech to *start* after you trigger Jarvis. Generous
#   by default (30 s) so you can think, or say a long name, without it giving up.
_PHRASE_TIME_LIMIT = _env_int("JARVIS_PHRASE_LIMIT", 15, 3)
# ^ max seconds of a single utterance (was 6 s): long enough for a whole sentence
#   so a wordy request is never truncated mid-way.
_RECOGNIZE_TIMEOUT = 15    # max seconds for the speech-to-text HTTP round-trip

# Bias phrases fed to faster-whisper as ``initial_prompt``. Whisper uses the
# prompt as preceding context, so it favours these spellings instead of guessing
# from the acoustics alone. Without it, "Jakir" is regularly transcribed as
# "Jahan" or "Zaaakir".
STT_INITIAL_PROMPT = (
    "My name is Jakir. Jarvis is an AI assistant. Dhaka, Bangladesh."
)

# Google STT has no prompt/biasing option, so the same corrections are applied
# to its output. "Jahan" is only rewritten in a name-introduction context (it is
# a real word elsewhere); the "Zaaakir"-style spellings are unambiguous.
_MISHEARD_NAME = re.compile(
    r"\b(my name is|i am|i'm|this is|call me)\s+(?:jahan|zaa+kir|zaakir)\b",
    re.I,
)
_SPELLED_NAME = re.compile(r"\bzaa+kir\b", re.I)


def _fix_misheard_name(text):
    """Correct known STT mishearings of the user's name in ``text``."""
    text = _SPELLED_NAME.sub("Jakir", text or "")
    return _MISHEARD_NAME.sub(lambda match: f"{match.group(1)} Jakir", text)


# Fixed RMS gate: no ambient auto-calibration and no dynamic threshold.
# Measured on this machine's onboard mic (ALC245): idle ambient RMS is ~40-80,
# while speech lands in the thousands, so 300 separates the two cleanly.
_ENERGY_THRESHOLD = 300

# Capture format. The Linux adapter streams headerless S16_LE mono PCM from
# arecord (Windows/macOS use sounddevice); either way we wrap it in
# sr.AudioData(data, _SAMPLE_RATE, _SAMPLE_WIDTH) before Google STT.
_SAMPLE_RATE = 16000
_SAMPLE_WIDTH = 2                       # bytes per sample (S16_LE)
_CHANNELS = 1

# We read (and RMS-gate) fixed 0.1 s chunks ourselves instead of letting PyAudio
# / PortAudio own the stream, so the platform adapter only has to deliver bytes.
_CHUNK_SECONDS = 0.1
_CHUNK_FRAMES = int(_SAMPLE_RATE * _CHUNK_SECONDS)   # 1600
_CHUNK_BYTES = _CHUNK_FRAMES * _SAMPLE_WIDTH         # 3200

# Speech starts on a chunk above _ENERGY_THRESHOLD (300). The level that counts
# as "still speaking" is auto-calibrated on every listen: we measure the room's
# background noise first and set the stop floor to 1.5x its median, clamped to
# [_SILENCE_THRESHOLD, _MAX_SILENCE_THRESHOLD] (150..300). Hysteresis then keeps
# recording through brief dips between words; the phrase only ends after
# _SILENCE_SECONDS (2 s) below that floor.
_SILENCE_THRESHOLD = 150        # minimum floor (also the fallback)
_MAX_SILENCE_THRESHOLD = 300    # maximum floor
_SILENCE_FACTOR = 1.5
_SILENCE_SECONDS = _env_float("JARVIS_SILENCE_END", 2.0, 0.5)
# ^ how long a pause may last before the phrase is treated as finished (was 1.2 s).
#   Longer by default so a thinking pause mid-sentence does not cut you off. The
#   chunk count is derived at capture time, so a changed value always applies.

# Warm-up: the very first chunk after the stream opens is a spike (it reads
# ~6000 and decays). We keep it out of the background estimate and skip its
# decaying tail for at most _WARMUP_SECONDS (0.5 s), so calibration measures
# real room noise. The bound is short, so a phrase that starts immediately is
# still captured.
_WARMUP_SECONDS = 0.5
_WARMUP_CHUNKS = max(1, round(_WARMUP_SECONDS / _CHUNK_SECONDS))  # 5

# Keep the last ~0.3 s of audio from *before* the gate trips: word onsets are
# often quieter than 300, so without this the first syllable gets clipped.
_PRE_ROLL_SECONDS = 0.3
_PRE_ROLL_CHUNKS = max(1, round(_PRE_ROLL_SECONDS / _CHUNK_SECONDS))  # 3

# Set JARVIS_DEBUG=1 to print capture / recognition diagnostics.
_DEBUG = os.getenv("JARVIS_DEBUG", "").strip().lower() not in ("", "0", "false", "no")

# Set JARVIS_PTT=1 for push-to-talk: press Enter, speak, then press Enter again
# (or just stop talking) to end the phrase. Useful when music or noise keeps the
# RMS above the silence floor so the automatic gate never closes.
_PTT = os.getenv("JARVIS_PTT", "").strip().lower() not in ("", "0", "false", "no")

# Set JARVIS_MUTE=1 to skip text-to-speech entirely (replies are still printed).
# Handy with JARVIS_TEXT=1 for fast, silent testing.
_MUTE = os.getenv("JARVIS_MUTE", "").strip().lower() not in ("", "0", "false", "no")

# A background median above this suggests speakers/music are leaking into the mic.
_LOUD_BACKGROUND = 200


# --------------------------------------------------------------------------- #
# runtime mic mute + live level (driven by the GUI)
# --------------------------------------------------------------------------- #
# The GUI's mic button toggles this at runtime. It is deliberately separate from
# _MUTE above: that one silences the *reply*, this one stops Jarvis *listening*.
_mic_muted = False


def set_mic_muted(flag):
    """Mute/unmute Jarvis's own listening and return the new state.

    This is a *Jarvis-internal* mute only: it flips a boolean, cuts off any
    in-flight capture, and pauses the barge-in monitor, so Jarvis stops
    listening. The physical system microphone is deliberately left untouched --
    Jarvis never issues an OS-level mute (``pactl set-source-mute`` or similar),
    which would light up the keyboard mute LED and silence other applications.

    Barge-in is explicitly paused while muted so its capture thread cannot keep
    the audio device open. Unmuting resumes normal barge-in detection.
    """
    global _mic_muted
    _mic_muted = bool(flag)
    if _mic_muted:
        stop_listening()          # cut off any in-flight capture immediately
        pause_barge_in()          # keep barge-in from opening the mic again
    else:
        resume_barge_in()
    return _mic_muted


def is_mic_muted():
    """True while the microphone is muted from the GUI."""
    return _mic_muted


def toggle_mic_mute():
    """Flip the mic mute and return the new state."""
    return set_mic_muted(not _mic_muted)


# Optional callback (set by the GUI) fed a 0..1 loudness for the live animation.
# None means nobody is watching, so the capture path stays at zero overhead.
_level_sink = None

# RMS that maps to full scale ("1.0") in the animation; speech lands well below.
_LEVEL_CEILING = 3000.0


def set_level_sink(fn):
    """Register a callback receiving live loudness (0..1); None removes it."""
    global _level_sink
    _level_sink = fn


def _emit_value(value):
    """Push a 0..1 loudness to the level sink, swallowing any sink error."""
    sink = _level_sink
    if sink is None:
        return
    try:
        sink(max(0.0, min(1.0, float(value))))
    except Exception:
        pass


def _emit_level(rms):
    """Normalise a raw chunk RMS to 0..1 (sqrt curve) and push it."""
    _emit_value(min(1.0, max(0.0, float(rms) / _LEVEL_CEILING)) ** 0.5)


# Set by stop_speaking() to cut the current reply short (the GUI Stop button).
_stop_speaking = threading.Event()

# The active streaming output and its block queue. stop_speaking() uses these
# to abort the device stream immediately and release a reader blocked on a full
# queue; _play_tts_stream() clears them in its finally block.
_tts_playback_lock = threading.Lock()
_active_tts_stream = None
_active_tts_queue = None


def _set_active_tts_playback(stream=None, blocks=None):
    """Register the current output stream and queue for stop_speaking()."""
    global _active_tts_stream, _active_tts_queue
    with _tts_playback_lock:
        _active_tts_stream = stream
        _active_tts_queue = blocks


def _drain_queue(blocks):
    """Discard every queued block without blocking."""
    if blocks is None:
        return
    while True:
        try:
            blocks.get_nowait()
        except queue.Empty:
            return


def stop_speaking():
    """Interrupt the reply currently being spoken, if any."""
    _stop_speaking.set()
    with _tts_playback_lock:
        stream = _active_tts_stream
        blocks = _active_tts_queue
    if stream is not None:
        with contextlib.suppress(Exception):
            stream.abort()
    _drain_queue(blocks)


# Set by stop_listening() to abandon a listen that is still waiting for speech
# (the GUI Stop button), instead of waiting out the full window.
_stop_listening = threading.Event()


def stop_listening():
    """Give up on a listen that has not started recording yet."""
    _stop_listening.set()


# --- barge-in (talking over a reply) ---------------------------------------- #
# While Jarvis speaks, a monitor thread watches the microphone. If the user's
# voice stays above this threshold for _BARGE_HOLD seconds, the reply is cut
# short and the GUI goes back to listening. 1200 is normal conversational
# volume; raise it via JARVIS_BARGE_THRESHOLD if speaker echo triggers false
# interruptions. JARVIS_PTT=1 disables barge-in entirely, since push-to-talk
# already gives the user explicit control of the microphone.
_BARGE_ENABLED = os.getenv("JARVIS_BARGE_IN", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
_BARGE_THRESHOLD = BARGE_THRESHOLD
_BARGE_HOLD = BARGE_HOLD

# Explicit media/telephony pause. This is separate from ignoring the live audio:
# the monitor does not even open the capture device while it is set.
_barge_paused = threading.Event()


def pause_barge_in():
    """Keep barge-in from opening the microphone (e.g. while Jarvis is muted)."""
    _barge_paused.set()


def resume_barge_in():
    """Allow barge-in to watch the microphone again."""
    _barge_paused.clear()


def is_barge_in_paused():
    """True while barge-in is explicitly paused."""
    return _barge_paused.is_set()


# A short, unambiguous interruption spoken *after* barge-in has already cut the
# reply. This is intentionally stricter than "contains stop": commands such as
# "stop listening" or "stop the music" must still reach the router. Besides
# "stop", the ChatGPT-style interruptions "wait", "listen" and "hold on" cut the
# reply without ending the session or reaching the LLM.
_STOP_PHRASE = re.compile(
    r"^\s*(?:(?:please|okay|ok|hey|jarvis|just|can\s+you|could\s+you|"
    r"would\s+you)[\s,]+)*"
    r"(?:"
    r"stop|shut\s+up|quiet|be\s+quiet|silence"
    r"|wait(?:\s+(?:a\s+(?:sec|second|moment|minute)|up|please))?"
    r"|hold\s+on|hang\s+on"
    r"|listen(?:\s+up)?"
    r")"
    r"(?:\s+(?:it|now|please|talking|speaking|that|this|a\s+"
    r"(?:sec|second|moment|minute)))*"
    r"\s*[.!?]*$",
    re.I,
)


def is_stop_phrase(text):
    """True for a pure barge-in interruption like "stop" or "shut up"."""
    return bool(_STOP_PHRASE.match((text or "").strip()))


def _ptt_active():
    """Push-to-talk only makes sense on an interactive terminal."""
    try:
        return _PTT and sys.stdin is not None and sys.stdin.isatty()
    except (ValueError, OSError):
        return False


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


def _median(samples):
    """Median of a list of ints (0 for an empty list). Robust to outliers."""
    if not samples:
        return 0
    ordered = sorted(samples)
    return ordered[len(ordered) // 2]


def _calibrate(samples):
    """Stop floor from background RMS: 1.5x the median, clamped to 150..300.

    The median ignores the odd outlier, and the clamps keep the floor sane even
    in a very quiet (or very noisy) room.
    """
    background = _median(samples)
    return int(min(_MAX_SILENCE_THRESHOLD,
                   max(_SILENCE_THRESHOLD, round(_SILENCE_FACTOR * background))))


def _mic_device():
    """ALSA PCM name to capture from (passed to ``arecord -D``).

    Defaults to ``default`` (the system/PipeWire input), which is the device
    that records this machine's voice correctly. Override with
    ``JARVIS_MIC_DEVICE=plughw:1,0`` (any name from ``arecord -L``) if needed.
    The old PyAudio-index override is obsolete now that capture goes through
    arecord.
    """
    return os.getenv("JARVIS_MIC_DEVICE", "").strip() or "default"


def arecord_command(device=None, *, seconds=None, output=None, fmt="raw"):
    """Build the platform recorder command for the shared capture format.

    ``fmt="raw"`` streams headerless PCM (used by :func:`listen`); ``fmt="wav"``
    writes a self-describing WAV (used by ``test_mic.py``). ``seconds`` limits
    the recording and ``output`` names a file (default: stdout). The actual
    command is built by the platform adapter (``arecord`` on Linux).
    """
    return Audio.record_command(device, seconds=seconds, output=output, fmt=fmt)


def _spawn_arecord(device=None):
    """Start a streaming recorder through the platform adapter."""
    return Audio.spawn_recorder(device)


def _stop_arecord(proc):
    """Terminate a recorder process started by :func:`_spawn_arecord`."""
    Audio.stop_recorder(proc)


def _read_chunk(stream, fd, deadline):
    """Read one full 0.1 s chunk before ``deadline``; b"" on timeout / EOF.

    ``select`` bounds the read even if arecord stalls and produces no data, so
    a wedged capture can never block past the deadline.
    """
    buf = bytearray()
    while len(buf) < _CHUNK_BYTES:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            ready, _, _ = select.select([fd], [], [], remaining)
        except (OSError, ValueError):
            break
        if not ready:
            break
        try:
            piece = stream.read(_CHUNK_BYTES - len(buf))
        except (OSError, ValueError):
            break
        if not piece:
            break
        buf.extend(piece)
    return bytes(buf)


class BargeInMonitor:
    """Watch the microphone while Jarvis speaks and detect the user talking over it.

    Detection is deliberately conservative: ``hold`` seconds of *consecutive*
    chunks must stay above ``threshold`` before firing. That is long enough for a
    real spoken interruption and short enough to feel immediate. The default
    threshold is tuned for normal conversational volume; raise it if speaker
    echo from Jarvis's own voice triggers false interruptions.

    :meth:`feed` is split out from :meth:`_run` so the detection rule can be
    unit-tested with synthetic PCM, without a microphone or a real thread.
    """

    def __init__(self, on_barge=None, threshold=None, hold=None):
        self._on_barge = on_barge
        self.threshold = _BARGE_THRESHOLD if threshold is None else int(threshold)
        hold = _BARGE_HOLD if hold is None else float(hold)
        # Convert the hold time to whole chunks so detection is deterministic.
        self.required = max(1, int(round(hold / _CHUNK_SECONDS)))
        self._loud = 0
        self._stop = threading.Event()
        self._thread = None
        self.detected = False

    def start(self):
        """Start watching the mic. Returns False when disabled or unavailable."""
        if (not _BARGE_ENABLED or _ptt_active() or _mic_muted
                or _barge_paused.is_set()):
            return False
        self._stop.clear()
        self._loud = 0
        self.detected = False
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="jarvis-barge-in")
        self._thread.start()
        return True

    def stop(self, timeout=0.6):
        """Stop watching and wait briefly for the capture thread to finish."""
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        self._thread = None

    def feed(self, data):
        """Process one PCM chunk; return True once barge-in is confirmed."""
        if not data:
            return False
        rms = _rms(data, _SAMPLE_WIDTH)
        if rms < self.threshold:
            self._loud = 0
            return False
        self._loud += 1
        if self._loud >= self.required:
            self.detected = True
            return True
        return False

    def _run(self):
        """Capture loop: reads chunks until stopped or a barge-in is confirmed."""
        proc = None
        try:
            proc = _spawn_arecord(_mic_device())
            stream = proc.stdout
            fd = stream.fileno()
            # Discard the stream-open spike and let the room settle, exactly as
            # the normal listen path does.
            for _ in range(_WARMUP_CHUNKS):
                if self._stop.is_set():
                    return
                _read_chunk(stream, fd,
                            time.monotonic() + _CHUNK_SECONDS * 2)
            while not self._stop.is_set():
                data = _read_chunk(stream, fd,
                                   time.monotonic() + _CHUNK_SECONDS * 2)
                if not data:
                    break
                if self.feed(data):
                    if self._on_barge is not None:
                        with contextlib.suppress(Exception):
                            self._on_barge()
                    return
        except Exception as exc:
            _dbg(f"barge-in monitor stopped: {type(exc).__name__}: {exc}")
        finally:
            if proc is not None:
                _stop_arecord(proc)


def _capture_from_stream(stream, fd=None, ptt=False, stop_event=None,
                         wait_timeout=None):
    """Gate a raw S16_LE stream on RMS and return the captured utterance.

    Reads ``_CHUNK_SECONDS`` chunks. The arecord stream-open spike is discarded
    and the "still speaking" floor is auto-calibrated from the background noise
    (1.5x its median, clamped to 150..300). Speech starts on RMS >
    ``_ENERGY_THRESHOLD``; recording then continues through brief dips (any chunk
    above the calibrated floor resets the silence run) until
    ~``_SILENCE_SECONDS`` below that floor, the phrase hits ``_PHRASE_TIME_LIMIT``,
    or the stream ends. A ~0.3 s pre-roll is kept so a quiet onset is never
    clipped.

    With ``ptt=True`` the caller already signalled start (Enter was pressed), so
    recording begins immediately after the open spike, and it also ends as soon
    as ``stop_event`` is set (Enter pressed again). Returns the raw bytes
    captured (possibly empty).
    """
    if fd is None:
        fd = stream.fileno()

    pre_roll = deque(maxlen=_PRE_ROLL_CHUNKS)
    chunks = []
    bg_samples = []
    started = False
    silent_run = 0
    stop_threshold = _SILENCE_THRESHOLD
    warm_left = _WARMUP_CHUNKS
    spike_rms = None
    first_chunk = True
    wait_deadline = time.monotonic() + (wait_timeout or _LISTEN_TIMEOUT)
    phrase_deadline = None
    reason = "ptt" if ptt else "timeout"
    reported = False
    # Cap by chunk count as well as wall-clock, so a stream that delivers faster
    # than realtime (e.g. a file) still honours the phrase limit.
    max_phrase_chunks = max(1, round(_PHRASE_TIME_LIMIT / _CHUNK_SECONDS))
    silence_chunks_to_stop = max(1, round(_SILENCE_SECONDS / _CHUNK_SECONDS))

    def report_calibration():
        nonlocal reported
        if reported or not _DEBUG or not started:
            return
        reported = True
        background = _median(bg_samples)
        _dbg(f"calibration: background_median={background} "
             f"floor={stop_threshold} samples={len(bg_samples)}")
        if background > _LOUD_BACKGROUND:
            _dbg(f"WARNING: background median {background} > {_LOUD_BACKGROUND} - "
                 "speakers or music are probably leaking into the mic")

    def begin_recording():
        nonlocal started, phrase_deadline, stop_threshold
        started = True
        phrase_deadline = time.monotonic() + _PHRASE_TIME_LIMIT
        stop_threshold = _calibrate(bg_samples)
        # Seed the utterance with the ~0.3 s leading up to the trigger.
        chunks.extend(pre_roll)
        pre_roll.clear()
        report_calibration()
        if _DEBUG:
            _dbg(f"state=recording floor={stop_threshold}")

    if ptt:
        # The first chunk is the stream-open spike: drop it, then record at once.
        _read_chunk(stream, fd, wait_deadline)
        begin_recording()

    while True:
        if _mic_muted:
            # The GUI mic button was pressed mid-capture: drop what we have.
            reason = "muted"
            chunks = []
            break
        if _stop_listening.is_set():
            # The GUI Stop button was pressed while still waiting for speech.
            reason = "stopped"
            chunks = []
            break
        if stop_event is not None and stop_event.is_set():
            reason = "ptt"
            break
        deadline = phrase_deadline if started else wait_deadline
        chunk = _read_chunk(stream, fd, deadline)
        if not chunk:
            reason = "stream_end" if started else "timeout"
            break

        rms = _rms(chunk, _SAMPLE_WIDTH)
        _emit_level(rms)          # live loudness for the GUI animation

        if not started:
            pre_roll.append(chunk)

            # The first chunk is the stream-open spike: never let it start the
            # phrase, and keep it out of the background estimate.
            if first_chunk:
                first_chunk = False
                if rms > _ENERGY_THRESHOLD:
                    spike_rms = rms
                    if _DEBUG:
                        _dbg(f"chunk rms={rms:.0f} gate={_ENERGY_THRESHOLD} "
                             f"state=warmup spike=discarded")
                    continue

            if rms > _ENERGY_THRESHOLD:
                # A decaying tail of the open spike (bounded by the warm-up
                # window) is discarded; anything else is speech and starts now,
                # so a phrase that begins immediately is never cut off.
                if spike_rms is not None and warm_left > 0 and rms < spike_rms * 0.6:
                    spike_rms = rms
                    warm_left -= 1
                    if _DEBUG:
                        _dbg(f"chunk rms={rms:.0f} gate={_ENERGY_THRESHOLD} "
                             f"state=warmup spike=decay left={warm_left}")
                    continue
                spike_rms = None
                begin_recording()
                continue

            # Quiet chunk: this is background. Re-estimate the floor each time.
            spike_rms = None
            warm_left = max(0, warm_left - 1)
            bg_samples.append(rms)
            stop_threshold = _calibrate(bg_samples)
            if _DEBUG:
                state = "warmup" if warm_left > 0 else "waiting"
                _dbg(f"chunk rms={rms:.0f} gate={_ENERGY_THRESHOLD} "
                     f"state={state} floor={stop_threshold}")
            continue

        chunks.append(chunk)
        if rms > stop_threshold:
            silent_run = 0
        else:
            silent_run += 1
        if _DEBUG:
            _dbg(f"chunk rms={rms:.0f} gate={_ENERGY_THRESHOLD} "
                 f"state=recording floor={stop_threshold} "
                 f"silence={silent_run}/{silence_chunks_to_stop}")

        if silent_run >= silence_chunks_to_stop:
            reason = "silence"
            break
        if len(chunks) >= max_phrase_chunks:
            reason = "max_length"
            break
        if time.monotonic() >= phrase_deadline:
            reason = "max_time"
            break

    if _DEBUG:
        total = sum(len(c) for c in chunks)
        _dbg(f"state=ended reason={reason} chunks={len(chunks)} bytes={total}")
    _emit_value(0.0)
    return b"".join(chunks)


def _ptt_wait_for_enter():
    """Block until Enter is pressed (push-to-talk start). False on EOF/^C."""
    try:
        input("🎤 Press Enter to talk... ")
    except (EOFError, KeyboardInterrupt):
        return False
    return True


def _ptt_stop_reader(stop_event):
    """Set ``stop_event`` when Enter is pressed again (daemon thread)."""
    def run():
        try:
            for _line in sys.stdin:
                break
        except Exception:
            pass
        stop_event.set()

    threading.Thread(target=run, daemon=True).start()


# --- offline speech-to-text fallback (faster-whisper) ------------------------ #
# Google STT needs the network. When it is unreachable, Jarvis transcribes the
# already-captured audio on the machine instead, so voice input keeps working
# offline. The model is small ("base" int8 by default) and only built on first
# use, so a machine without faster-whisper installed pays nothing for it.
_WHISPER_SIZE = os.getenv("JARVIS_WHISPER_MODEL", "base")
_WHISPER_COMPUTE = os.getenv("JARVIS_WHISPER_COMPUTE", "int8")
_whisper_model = None
_whisper_unavailable = False


def _local_transcribe(audio):
    """Transcribe captured ``audio`` on-device with faster-whisper, or return "".

    Lazy and best-effort: if faster-whisper (or the model) is missing, this
    reports once and returns an empty string so the caller can fall back to a
    plain "I didn't catch that".
    """
    global _whisper_model, _whisper_unavailable

    if _whisper_unavailable:
        return ""

    if _whisper_model is None:
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:
            _whisper_unavailable = True
            _dbg(f"offline STT unavailable (faster-whisper not installed): {exc}")
            return ""
        try:
            _dbg(f"loading offline STT model '{_WHISPER_SIZE}' ({_WHISPER_COMPUTE})")
            _whisper_model = WhisperModel(
                _WHISPER_SIZE, device="cpu", compute_type=_WHISPER_COMPUTE
            )
        except Exception as exc:
            _whisper_unavailable = True
            _dbg(f"offline STT model failed to load: {exc}")
            return ""

    path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            path = tmp.name
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(audio.sample_width)
            wf.setframerate(audio.sample_rate)
            wf.writeframes(audio.get_raw_data())
        language = (STT_LANG or "en").split("-")[0]
        segments, _info = _whisper_model.transcribe(
            path,
            language=language,
            initial_prompt=STT_INITIAL_PROMPT,
        )
        return " ".join(segment.text for segment in segments).strip()
    except Exception as exc:
        _dbg(f"offline transcription failed: {type(exc).__name__}: {exc}")
        return ""
    finally:
        if path:
            with contextlib.suppress(OSError):
                os.remove(path)


def listen(wait=None, *, label="", barge=False):
    """Capture one utterance from the microphone via the platform adapter.

    Returns the recognised text in lower case, or "" when nothing usable was
    heard (timeout, silence, or no microphone). Bounded by the *_TIMEOUT
    constants above so it always returns instead of blocking forever.

    ``wait`` overrides how long it waits for you to *start* speaking (seconds).
    Continuous-chat mode passes a longer one: Jarvis then keeps the mic open
    after each reply instead of going to sleep. Pass ``label`` to tag debug
    output (``listen`` vs ``chat``).

    Set ``barge=True`` for the turn immediately after the user talked over a
    reply. If that turn is a pure interruption ("stop", "shut up", "quiet"), it
    cuts speech/listening and returns an empty string instead of dispatching a
    command.

    With ``JARVIS_PTT=1`` this becomes push-to-talk: it waits for Enter, records
    until Enter is pressed again or silence is detected, then transcribes.

    Background media is never paused: Jarvis listens while YouTube or music
    plays (so "pause music" / "next song" still work), relying on the RMS gate
    and barge-in threshold to ignore the audio leaking in from the speakers.
    """
    rec = sr.Recognizer()
    rec.operation_timeout = _RECOGNIZE_TIMEOUT

    if _mic_muted:
        _dbg("microphone muted; not listening")
        return ""

    _stop_listening.clear()          # a stale Stop must not kill this listen

    device = _mic_device()
    ptt = _ptt_active()
    _dbg(f"{label or 'listen'} device={device} gate={_ENERGY_THRESHOLD} "
         f"chunk={_CHUNK_SECONDS}s wait={wait or _LISTEN_TIMEOUT}s "
         f"phrase_max={_PHRASE_TIME_LIMIT}s ptt={ptt}")

    if ptt and not _ptt_wait_for_enter():
        return ""

    try:
        print("🎤 Listening...", flush=True)
        proc = _spawn_arecord(device)
    except Exception as exc:
        _dbg(f"capture error: {type(exc).__name__}: {exc}")
        return ""

    stop_event = threading.Event() if ptt else None
    if ptt:
        _ptt_stop_reader(stop_event)

    try:
        try:
            data = _capture_from_stream(proc.stdout, ptt=ptt, stop_event=stop_event,
                                        wait_timeout=wait)
        except Exception as exc:
            _dbg(f"capture error: {type(exc).__name__}: {exc}")
            return ""
    finally:
        _stop_arecord(proc)

    if not data:
        _dbg(f"TIMEOUT:{label or 'listen'} no speech above threshold within "
             f"{wait or _LISTEN_TIMEOUT}s")
        return ""

    rms = _rms(data, _SAMPLE_WIDTH)
    peak = _peak(data, _SAMPLE_WIDTH)
    if _DEBUG:
        _dbg(f"captured {len(data)} bytes, rms={rms:.0f} peak={peak}")
        if peak >= 32767:
            _dbg("WARNING: peak at full scale (32768) -> input is CLIPPING; "
                 "lower the mic gain in the system mixer")

    audio = sr.AudioData(data, _SAMPLE_RATE, _SAMPLE_WIDTH)
    try:
        text = (rec.recognize_google(audio, language=STT_LANG) or "").strip()
    except sr.UnknownValueError:
        _dbg(f"UNKNOWN_VALUE: audio captured (rms={rms:.0f}) but no words recognised")
        return ""
    except sr.RequestError as exc:
        # Network / API error. Surface it, then transcribe locally so the turn is
        # not lost when the connection is down.
        print(f"[voice] REQUEST_ERROR from Google STT: {type(exc).__name__}: {exc}",
              flush=True)
        text = _local_transcribe(audio)
        if not text:
            return ""
    except Exception as exc:
        _dbg(f"recognition error: {type(exc).__name__}: {exc}")
        text = _local_transcribe(audio)
        if not text:
            return ""

    _dbg(f"recognised: {text!r}")
    if not text:
        return ""
    fixed = _fix_misheard_name(text)
    if fixed != text:
        _dbg(f"corrected name: {fixed!r}")
        text = fixed
    if barge and is_stop_phrase(text):
        # The barge-in monitor already started cutting the reply; this catches
        # the spoken "stop" itself so it is not dispatched as a command or
        # answered with another sentence.
        _dbg(f"barge-in stop phrase: {text!r}")
        stop_speaking()
        stop_listening()
        return ""
    print(f"👤 {text}", flush=True)
    return text.lower()


# Loudness-envelope extraction for the speaking animation. ffmpeg is already a
# dependency of the screen pipeline; if it is missing we fall back to synthetic
# motion, so this is always best-effort.
_ENVELOPE_RATE = 8000
_ENVELOPE_HOP = 0.02          # 20 ms per envelope sample


def _tts_envelope(path, hop=_ENVELOPE_HOP):
    """Return the loudness envelope (0..1 floats) of an audio file, via ffmpeg.

    Decoding is local and offline, so it never blocks on the network. Returns an
    empty list when ffmpeg is unavailable or fails; the caller then simply keeps
    the normal pulse instead of a speech-synced one.
    """
    proc = None
    try:
        proc = subprocess.Popen(
            ["ffmpeg", "-v", "quiet", "-i", str(path),
             "-ac", "1", "-ar", str(_ENVELOPE_RATE), "-f", "s16le", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        raw, _ = proc.communicate(timeout=20)
    except Exception:
        if proc is not None:
            with contextlib.suppress(Exception):
                proc.kill()
        return []
    if not raw:
        return []

    step = max(1, int(_ENVELOPE_RATE * hop) * _SAMPLE_WIDTH)
    levels = [_rms(raw[i:i + step], _SAMPLE_WIDTH)
              for i in range(0, len(raw) - _SAMPLE_WIDTH + 1, step)]
    peak = max(levels, default=0)
    if peak <= 0:
        return []
    return [min(1.0, level / peak) ** 0.5 for level in levels]


def _playback_seconds(started):
    """Seconds of music played (pygame if it can, else the wall clock)."""
    try:
        pos = pygame.mixer.music.get_pos()
        if pos is not None and pos >= 0:
            return pos / 1000.0
    except Exception:
        pass
    return time.monotonic() - started


def _pump_envelope(envelope, started):
    """Feed the envelope value for the current playback position to the sink."""
    if not envelope:
        return
    index = int(_playback_seconds(started) / _ENVELOPE_HOP)
    if 0 <= index < len(envelope):
        _emit_value(envelope[index])


# --- streaming TTS ---------------------------------------------------------- #
# edge-tts emits 24 kHz mono MP3. Waiting for the whole file before playing costs
# 2-3 seconds on a normal reply. Instead we pipe the MP3 chunks through ffmpeg as
# they arrive and feed the decoded PCM into one continuous sounddevice stream.
# The reader blocks when ten blocks are queued, so synthesis cannot race far
# ahead of playback. pygame is only a fallback for systems without sounddevice.
_TTS_RATE = 24000
_TTS_CHANNELS = 1
_TTS_SAMPLE_WIDTH = 2
_TTS_BLOCK_SECONDS = 0.20
_TTS_BLOCK_BYTES = int(_TTS_RATE * _TTS_BLOCK_SECONDS) * _TTS_SAMPLE_WIDTH
# Larger fallback chunks avoid pygame clipping the edges of tiny Sounds.
_TTS_FALLBACK_BLOCK_SECONDS = 0.50
_TTS_FALLBACK_BLOCK_BYTES = (
    int(_TTS_RATE * _TTS_FALLBACK_BLOCK_SECONDS) * _TTS_SAMPLE_WIDTH
)
_TTS_QUEUE_BLOCKS = 10          # bounded backpressure; the reader blocks when full
_TTS_FALLBACK_LEAD_SECONDS = 1.5  # max audio queued inside the pygame channel
_TTS_PYGAME_BUFFER = 2048       # ~85 ms at 24 kHz mono S16_LE
_TTS_STREAM_ENABLED = os.getenv("JARVIS_TTS_STREAM", "1").strip().lower() not in (
    "0", "false", "no", "off",
)


def _start_tts_decoder():
    """Start ffmpeg decoding MP3 from stdin to raw S16_LE PCM on stdout.

    Returns None when ffmpeg is unavailable, so the caller can use the old
    whole-file path. ffmpeg is already used by the envelope/screen features, so
    this adds no new mandatory dependency: it is an optimisation when present.
    """
    try:
        return subprocess.Popen(
            ["ffmpeg", "-v", "quiet", "-f", "mp3", "-i", "pipe:0",
             "-f", "s16le", "-acodec", "pcm_s16le",
             "-ac", str(_TTS_CHANNELS), "-ar", str(_TTS_RATE), "pipe:1"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0,
        )
    except (OSError, ValueError):
        return None


def _open_sounddevice_stream():
    """Open one continuous 24 kHz mono S16_LE output stream.

    Returns None when sounddevice/PortAudio is unavailable or the device cannot
    be opened, so the caller can fall back to the pygame queue player.
    """
    try:
        import sounddevice as sd
    except Exception as exc:
        _dbg(f"sounddevice unavailable: {type(exc).__name__}: {exc}")
        return None

    stream = None
    try:
        # Let PortAudio choose a generous device buffer: a larger cushion is
        # smoother than a low-latency stream when edge-tts arrives in bursts.
        stream = sd.RawOutputStream(
            samplerate=_TTS_RATE,
            channels=_TTS_CHANNELS,
            dtype="int16",
            blocksize=0,
        )
        stream.start()
        return stream
    except Exception as exc:
        _dbg(f"sounddevice output unavailable: {type(exc).__name__}: {exc}")
        if stream is not None:
            with contextlib.suppress(Exception):
                stream.close()
        return None


def _init_pygame_tts():
    """Initialise the fallback mixer with a larger buffer and 24 kHz mono output."""
    with contextlib.suppress(Exception):
        pygame.mixer.quit()
    pre_init = getattr(pygame.mixer, "pre_init", None)
    if pre_init is not None:
        with contextlib.suppress(Exception):
            pre_init(
                frequency=_TTS_RATE,
                size=-16,
                channels=_TTS_CHANNELS,
                buffer=_TTS_PYGAME_BUFFER,
            )
    pygame.mixer.init(
        frequency=_TTS_RATE,
        size=-16,
        channels=_TTS_CHANNELS,
        buffer=_TTS_PYGAME_BUFFER,
    )


def _synthesize_to_decoder(text, stdin, stop_event):
    """Stream edge-tts MP3 chunks into ffmpeg's stdin until done or interrupted."""
    async def pump():
        communicate = edge_tts.Communicate(text, voice=TTS_VOICE)
        try:
            async for event in communicate.stream():
                if stop_event.is_set():
                    break
                if event.get("type") != "audio":
                    continue
                data = event.get("data") or b""
                if not data:
                    continue
                try:
                    stdin.write(data)
                    stdin.flush()
                except (BrokenPipeError, OSError, ValueError):
                    break
        finally:
            with contextlib.suppress(Exception):
                stdin.close()

    try:
        asyncio.run(pump())
    except Exception as exc:
        _dbg(f"streaming TTS synthesis failed: {type(exc).__name__}: {exc}")


def _read_decoded_pcm(stdout, blocks, stop_event,
                      block_bytes=_TTS_BLOCK_BYTES):
    """Read decoded PCM and apply backpressure through a bounded queue.

    Reads from an unbuffered pipe can be short, so bytes are accumulated into
    whole blocks first. The reader blocks when ``blocks`` is full, so ffmpeg and
    synthesis cannot run ahead of playback. The short put timeout keeps Stop
    responsive even if the consumer has gone away.
    """
    buf = bytearray()

    def deliver(data):
        while not stop_event.is_set():
            try:
                blocks.put(data, timeout=0.1)
                return
            except queue.Full:
                continue

    try:
        while not stop_event.is_set():
            if len(buf) < block_bytes:
                data = stdout.read(block_bytes - len(buf))
                if not data:
                    break
                buf.extend(data)
                continue
            block = bytes(buf[:block_bytes])
            del buf[:block_bytes]
            # Both RawOutputStream and pygame.mixer.Sound need whole samples.
            if len(block) % _TTS_SAMPLE_WIDTH:
                block = block[:len(block) - (len(block) % _TTS_SAMPLE_WIDTH)]
            if block:
                deliver(block)
    except (OSError, ValueError):
        pass
    finally:
        if buf and not stop_event.is_set():
            block = bytes(buf)
            if len(block) % _TTS_SAMPLE_WIDTH:
                block = block[:len(block) - (len(block) % _TTS_SAMPLE_WIDTH)]
            if block:
                deliver(block)
        # Wake the player at EOF. On Stop the player is already exiting and the
        # queue may intentionally stay full, so do not wait forever here.
        if not stop_event.is_set():
            deliver(None)


def _pcm_level(data):
    """Normalise one decoded PCM block to the 0..1 level the GUI understands."""
    rms = _rms(data, _TTS_SAMPLE_WIDTH)
    return min(1.0, max(0.0, rms / _LEVEL_CEILING)) ** 0.5


def _flush_levels(pending, now):
    """Emit envelope values whose scheduled playback time has arrived."""
    while pending and pending[0][0] <= now:
        _emit_value(pending.popleft()[1])


def _play_tts_stream(text):
    """Play edge-tts audio progressively through ffmpeg + sounddevice.

    One continuous RawOutputStream is the primary path. If sounddevice or
    PortAudio is unavailable, a single pygame Channel is fed with larger 0.5 s
    Sounds as a fallback. Returns False only when no ffmpeg decoder could be
    started (the caller then uses the whole-file fallback). Once streaming has
    begun a failure is not replayed from the start, which would repeat words the
    user already heard.
    """
    proc = _start_tts_decoder()
    if proc is None:
        return False

    raw_stream = _open_sounddevice_stream()
    block_bytes = (
        _TTS_BLOCK_BYTES if raw_stream is not None
        else _TTS_FALLBACK_BLOCK_BYTES
    )

    # A small bounded queue is the backpressure mechanism: the reader blocks on
    # put() when the player has not consumed the decoded audio yet.
    blocks = queue.Queue(maxsize=_TTS_QUEUE_BLOCKS)
    writer = threading.Thread(
        target=_synthesize_to_decoder,
        args=(text, proc.stdin, _stop_speaking),
        daemon=True, name="jarvis-tts-writer",
    )
    reader = threading.Thread(
        target=_read_decoded_pcm,
        args=(proc.stdout, blocks, _stop_speaking, block_bytes),
        daemon=True, name="jarvis-tts-reader",
    )
    _set_active_tts_playback(raw_stream, blocks)
    writer.start()
    reader.start()

    channel = None
    clock = None
    pending = deque()
    enqueued = 0.0
    started = None
    mixer_started = False
    first_sound = True
    try:
        if raw_stream is not None:
            while not _stop_speaking.is_set():
                try:
                    data = blocks.get(timeout=0.05)
                except queue.Empty:
                    data = b""
                now = time.monotonic()
                _flush_levels(pending, now)
                if data is None:
                    break
                if not data:
                    continue

                if started is None:
                    started = time.monotonic()
                try:
                    # RawOutputStream.write() blocks until PortAudio has room,
                    # which also keeps our queue bounded end-to-end.
                    raw_stream.write(data)
                except Exception as exc:
                    if not _stop_speaking.is_set():
                        _dbg("sounddevice playback failed: "
                             f"{type(exc).__name__}: {exc}")
                    break
                duration = len(data) / float(_TTS_RATE * _TTS_SAMPLE_WIDTH)
                pending.append((started + enqueued, _pcm_level(data)))
                enqueued += duration
                _flush_levels(pending, time.monotonic())
        else:
            _init_pygame_tts()
            mixer_started = True
            channel = pygame.mixer.Channel(0)
            clock = pygame.time.Clock()

            while not _stop_speaking.is_set():
                try:
                    data = blocks.get(timeout=0.05)
                except queue.Empty:
                    data = b""
                now = time.monotonic()
                _flush_levels(pending, now)
                if data is None:
                    break
                if not data:
                    continue

                # After an underrun the channel has already gone silent: restart
                # the lead clock so the next block is not counted as backlog.
                if started is None or enqueued - (now - started) <= 0:
                    started = now
                    enqueued = 0.0
                sound = pygame.mixer.Sound(buffer=data)
                if first_sound or not channel.get_busy():
                    channel.play(sound)
                    first_sound = False
                else:
                    channel.queue(sound)
                duration = len(data) / float(_TTS_RATE * _TTS_SAMPLE_WIDTH)
                pending.append((started + enqueued, _pcm_level(data)))
                enqueued += duration
                _flush_levels(pending, time.monotonic())

                # channel.queue() accepts audio instantly, so wait once a short
                # lead is buffered. That lets the bounded reader queue fill and
                # block, instead of letting synthesis race ahead indefinitely.
                while not _stop_speaking.is_set():
                    now = time.monotonic()
                    _flush_levels(pending, now)
                    if enqueued - (now - started) <= _TTS_FALLBACK_LEAD_SECONDS:
                        break
                    clock.tick(30)

            # Let already-queued fallback audio finish unless interrupted.
            while not _stop_speaking.is_set() and channel.get_busy():
                _flush_levels(pending, time.monotonic())
                clock.tick(30)

        if not _stop_speaking.is_set():
            _flush_levels(pending, time.monotonic() + 1e9)
    finally:
        try:
            # Release a reader blocked on put() before waiting for it to exit.
            _drain_queue(blocks)
            if raw_stream is not None:
                with contextlib.suppress(Exception):
                    if _stop_speaking.is_set():
                        raw_stream.abort()
                    else:
                        raw_stream.stop()
                # The stream is drained/aborted now; do not let a concurrent
                # Stop call abort() while close() is running.
                _set_active_tts_playback(None, None)
                with contextlib.suppress(Exception):
                    raw_stream.close()
            if channel is not None:
                with contextlib.suppress(Exception):
                    channel.stop()
            with contextlib.suppress(Exception):
                if proc.stdin is not None:
                    proc.stdin.close()
            with contextlib.suppress(Exception):
                proc.terminate()
            with contextlib.suppress(Exception):
                proc.wait(timeout=1.0)
            with contextlib.suppress(Exception):
                if proc.stdout is not None:
                    proc.stdout.close()
            writer.join(timeout=0.5)
            reader.join(timeout=0.5)
            if mixer_started:
                with contextlib.suppress(Exception):
                    pygame.mixer.quit()
        finally:
            # Keep the stream registered through stop()/close() so a Stop press
            # during the final drain can still abort it immediately.
            _set_active_tts_playback(None, None)
            _emit_value(0.0)
    return True


def _play_tts_file(text):
    """Fallback: synthesise the whole reply to disk, then play it with pygame.

    Used only when ffmpeg is unavailable. It has the old 2-3 s startup latency
    but keeps TTS working on machines without the streaming decoder.
    """
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3").name
    try:
        _stop_speaking.clear()      # a new reply must ignore a stale Stop
        asyncio.run(edge_tts.Communicate(text, voice=TTS_VOICE).save(tmp))
        envelope = _tts_envelope(tmp)

        if _stop_speaking.is_set():
            # Stop was pressed while the reply was still being synthesised.
            _emit_value(0.0)
            return

        pygame.mixer.init()
        try:
            pygame.mixer.music.load(tmp)
            pygame.mixer.music.play()
            clock = pygame.time.Clock()
            started = time.monotonic()
            while pygame.mixer.music.get_busy():
                if _stop_speaking.is_set():
                    with contextlib.suppress(Exception):
                        pygame.mixer.music.stop()
                    break
                _pump_envelope(envelope, started)
                clock.tick(30)
        finally:
            with contextlib.suppress(Exception):
                pygame.mixer.music.unload()
            with contextlib.suppress(Exception):
                pygame.mixer.quit()
            _emit_value(0.0)
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)


def _play_tts(text):
    """Speak `text`, streaming the audio whenever possible.

    A fresh reply clears any stale Stop. If ffmpeg is missing we fall back to the
    whole-file player; if streaming started and then failed we do not replay the
    beginning of the reply (that would repeat words the user already heard).
    """
    _stop_speaking.clear()
    if _TTS_STREAM_ENABLED:
        try:
            if _play_tts_stream(text):
                return
        except Exception as exc:
            _dbg(f"streaming TTS failed: {type(exc).__name__}: {exc}")
            _emit_value(0.0)
            return
    _play_tts_file(text)


def speak(text):
    """Say `text` out loud. Never raises, and always prints the reply.

    Text-to-speech needs the network, so a failure there (offline, no audio
    device, edge-tts error) falls back to printing only -- it must never kill
    the session or hide the answer.
    """
    text = "" if text is None else str(text)
    if not _MUTE:
        try:
            _play_tts(text)
        except Exception as exc:
            print(f"[voice] TTS failed ({type(exc).__name__}: {exc}); "
                  "showing the reply only", flush=True)
    print(f"🤖 {text}", flush=True)
