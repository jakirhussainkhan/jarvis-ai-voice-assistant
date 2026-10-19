"""Speech recognition and text-to-speech I/O for Jarvis."""

import os

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

if os.path.exists(os.environ.get("XDG_RUNTIME_DIR", "") + "/pulse/native"):
    os.environ.setdefault("SDL_AUDIODRIVER", "pulseaudio")

import array
import asyncio
import contextlib
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
    """Mute/unmute the microphone and return the new state."""
    global _mic_muted
    _mic_muted = bool(flag)
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


def stop_speaking():
    """Interrupt the reply currently being spoken, if any."""
    _stop_speaking.set()


# Set by stop_listening() to abandon a listen that is still waiting for speech
# (the GUI Stop button), instead of waiting out the full window.
_stop_listening = threading.Event()


def stop_listening():
    """Give up on a listen that has not started recording yet."""
    _stop_listening.set()


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


def listen(wait=None, *, label=""):
    """Capture one utterance from the microphone via the platform adapter.

    Returns the recognised text in lower case, or "" when nothing usable was
    heard (timeout, silence, or no microphone). Bounded by the *_TIMEOUT
    constants above so it always returns instead of blocking forever.

    ``wait`` overrides how long it waits for you to *start* speaking (seconds).
    Continuous-chat mode passes a longer one: Jarvis then keeps the mic open
    after each reply instead of going to sleep. Pass ``label`` to tag debug
    output (``listen`` vs ``chat``).

    With ``JARVIS_PTT=1`` this becomes push-to-talk: it waits for Enter, records
    until Enter is pressed again or silence is detected, then transcribes. Media
    that is playing is paused first (and resumed after capture) so it does not
    leak into the mic or hold the silence gate open.
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
            data = _capture_from_stream(proc.stdout, ptt=ptt, stop_event=stop_event,
                                        wait_timeout=wait)
        except Exception as exc:
            _dbg(f"capture error: {type(exc).__name__}: {exc}")
            return ""
    finally:
        _stop_arecord(proc)
        desktop.resume_media(pause_token)

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


def _play_tts(text):
    """Synthesise `text` with edge-tts and play it through pygame (may raise).

    While it plays, the decoded loudness envelope drives the level sink so the
    GUI orb moves in time with the words, and stop_speaking() cuts it short.
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
