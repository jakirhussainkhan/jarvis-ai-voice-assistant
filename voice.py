"""Speech recognition and text-to-speech I/O for Jarvis."""

import os

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

if os.path.exists(os.environ.get("XDG_RUNTIME_DIR", "") + "/pulse/native"):
    os.environ.setdefault("SDL_AUDIODRIVER", "pulseaudio")

import array
import asyncio
import contextlib
import math
import select
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque

import edge_tts
import pygame
import speech_recognition as sr

import desktop
from config import STT_LANG, TTS_VOICE

# Hard bounds so a silent, missing, busy or blocked microphone can never stall
# the caller (and therefore the worker loop) indefinitely.
_LISTEN_TIMEOUT = 4        # max seconds to wait for speech to *start*
_PHRASE_TIME_LIMIT = 6     # max seconds of a single utterance
_RECOGNIZE_TIMEOUT = 8     # max seconds for the speech-to-text HTTP round-trip

# Fixed RMS gate: no ambient auto-calibration and no dynamic threshold.
# Measured on this machine's onboard mic (ALC245): idle ambient RMS is ~40-80,
# while speech lands in the thousands, so 300 separates the two cleanly.
_ENERGY_THRESHOLD = 300

# Capture format. arecord streams headerless S16_LE mono PCM and we wrap it in
# sr.AudioData(data, _SAMPLE_RATE, _SAMPLE_WIDTH) before Google STT.
_SAMPLE_RATE = 16000
_SAMPLE_WIDTH = 2                       # bytes per sample (S16_LE)
_CHANNELS = 1

# We read (and RMS-gate) fixed 0.1 s chunks ourselves instead of letting PyAudio
# / PortAudio own the stream: PortAudio never saw this machine's mic, while
# arecord through the system default does (see progress.md).
_CHUNK_SECONDS = 0.1
_CHUNK_FRAMES = int(_SAMPLE_RATE * _CHUNK_SECONDS)   # 1600
_CHUNK_BYTES = _CHUNK_FRAMES * _SAMPLE_WIDTH         # 3200

# Speech starts on a chunk above _ENERGY_THRESHOLD (300). The level that counts
# as "still speaking" is auto-calibrated on every listen: we measure the room's
# background noise first and set the stop floor to 1.5x its median, clamped to
# [_SILENCE_THRESHOLD, _MAX_SILENCE_THRESHOLD] (150..300). Hysteresis then keeps
# recording through brief dips between words; the phrase only ends after
# ~_SILENCE_SECONDS (1.2 s) below that floor.
_SILENCE_THRESHOLD = 150        # minimum floor (also the fallback)
_MAX_SILENCE_THRESHOLD = 300    # maximum floor
_SILENCE_FACTOR = 1.5
_SILENCE_SECONDS = 1.2
_SILENCE_CHUNKS_TO_STOP = max(1, round(_SILENCE_SECONDS / _CHUNK_SECONDS))  # 12

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
    """Build an ``arecord`` command for the shared capture format.

    ``fmt="raw"`` streams headerless PCM (used by :func:`listen`); ``fmt="wav"``
    writes a self-describing WAV (used by ``test_mic.py``). ``seconds`` limits
    the recording and ``output`` names a file (default: stdout).
    """
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


def _spawn_arecord(device=None):
    """Start ``arecord`` streaming raw PCM to a pipe."""
    return subprocess.Popen(
        arecord_command(device),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )


def _stop_arecord(proc):
    """Terminate an arecord subprocess and reap it (never raises)."""
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


def _capture_from_stream(stream, fd=None, ptt=False, stop_event=None):
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
    wait_deadline = time.monotonic() + _LISTEN_TIMEOUT
    phrase_deadline = None
    reason = "ptt" if ptt else "timeout"
    reported = False
    # Cap by chunk count as well as wall-clock, so a stream that delivers faster
    # than realtime (e.g. a file) still honours the phrase limit.
    max_phrase_chunks = max(1, round(_PHRASE_TIME_LIMIT / _CHUNK_SECONDS))

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
        if stop_event is not None and stop_event.is_set():
            reason = "ptt"
            break
        deadline = phrase_deadline if started else wait_deadline
        chunk = _read_chunk(stream, fd, deadline)
        if not chunk:
            reason = "stream_end" if started else "timeout"
            break

        rms = _rms(chunk, _SAMPLE_WIDTH)

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
                 f"silence={silent_run}/{_SILENCE_CHUNKS_TO_STOP}")

        if silent_run >= _SILENCE_CHUNKS_TO_STOP:
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


def listen():
    """Capture one utterance from the microphone via ``arecord``.

    Returns the recognised text in lower case, or "" when nothing usable was
    heard (timeout, silence, or no microphone). Bounded by the *_TIMEOUT
    constants above so it always returns instead of blocking forever.

    With ``JARVIS_PTT=1`` this becomes push-to-talk: it waits for Enter, records
    until Enter is pressed again or silence is detected, then transcribes. Media
    that is playing is paused first (and resumed after capture) so it does not
    leak into the mic or hold the silence gate open.
    """
    rec = sr.Recognizer()
    rec.operation_timeout = _RECOGNIZE_TIMEOUT

    device = _mic_device()
    ptt = _ptt_active()
    _dbg(f"device={device} gate={_ENERGY_THRESHOLD} chunk={_CHUNK_SECONDS}s "
         f"wait={_LISTEN_TIMEOUT}s phrase_max={_PHRASE_TIME_LIMIT}s ptt={ptt}")

    if ptt and not _ptt_wait_for_enter():
        return ""

    # Best effort: pause any playing media so it neither leaks into the mic nor
    # keeps the silence gate open. Resumed after the phrase is captured.
    pause_token = None
    try:
        pause_token = desktop.pause_media_if_playing()
    except Exception as exc:                                   # never fatal
        _dbg(f"media pause skipped: {type(exc).__name__}: {exc}")

    try:
        print("🎤 Listening...", flush=True)
        proc = _spawn_arecord(device)
    except Exception as exc:
        _dbg(f"capture error: {type(exc).__name__}: {exc}")
        desktop.resume_media(pause_token)
        return ""

    stop_event = threading.Event() if ptt else None
    if ptt:
        _ptt_stop_reader(stop_event)

    try:
        try:
            data = _capture_from_stream(proc.stdout, ptt=ptt, stop_event=stop_event)
        except Exception as exc:
            _dbg(f"capture error: {type(exc).__name__}: {exc}")
            return ""
    finally:
        _stop_arecord(proc)
        desktop.resume_media(pause_token)

    if not data:
        _dbg(f"TIMEOUT: no speech above threshold within {_LISTEN_TIMEOUT}s")
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


def _play_tts(text):
    """Synthesise `text` with edge-tts and play it through pygame (may raise)."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3").name
    try:
        asyncio.run(edge_tts.Communicate(text, voice=TTS_VOICE).save(tmp))

        pygame.mixer.init()
        try:
            pygame.mixer.music.load(tmp)
            pygame.mixer.music.play()
            clock = pygame.time.Clock()
            while pygame.mixer.music.get_busy():
                clock.tick(10)
        finally:
            with contextlib.suppress(Exception):
                pygame.mixer.music.unload()
            with contextlib.suppress(Exception):
                pygame.mixer.quit()
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)


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
