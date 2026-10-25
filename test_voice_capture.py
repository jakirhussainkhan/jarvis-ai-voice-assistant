"""Tests for the arecord-based capture gate in voice.listen().

These exercise the RMS gating without a microphone: synthetic PCM is pushed
through a real OS pipe (so the select/read path is covered) and the arecord
subprocess is stubbed. The live microphone cannot be driven from a test.
"""

import os
import socket
import threading
import time
import wave

import pytest
import speech_recognition as sr

import voice


@pytest.fixture(autouse=True)
def _isolate_capture_globals(monkeypatch):
    """Keep the module-level capture state from leaking between tests."""
    monkeypatch.setattr(voice, "_level_sink", None)
    monkeypatch.setattr(voice, "_mic_muted", False)
    monkeypatch.setattr(voice, "_stop_listening", threading.Event())


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LOG_DIR", str(tmp_path / "logs"))
    return tmp_path / "logs"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _silence(seconds):
    return b"\x00\x00" * int(voice._SAMPLE_RATE * seconds)


def _tone(seconds, amplitude):
    import array

    n = int(voice._SAMPLE_RATE * seconds)
    samples = array.array("h", [amplitude, -amplitude] * (n // 2))
    return samples.tobytes()


class _Pipe:
    """A real OS pipe exposed as a binary file object, with a writer thread.

    On Windows ``select()`` only accepts sockets, so the pair is a
    ``socketpair`` there (exactly like ``platform_adapters._sd``). A CRT pipe
    would make every read look empty, so these tests would fail on Windows for a
    reason that has nothing to do with the gating logic under test.
    """

    def __init__(self):
        self._reader_sock = None
        self._writer_sock = None
        self.w = None
        if os.name == "nt":
            self._reader_sock, self._writer_sock = socket.socketpair()
            self.reader = self._reader_sock.makefile("rb", buffering=0)
        else:
            r, self.w = os.pipe()
            self.reader = os.fdopen(r, "rb", buffering=0)

    def _write(self, data):
        if self._writer_sock is not None:
            self._writer_sock.sendall(data)
        else:
            os.write(self.w, data)

    def _close_writer(self):
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
        if self.w is not None:
            fd, self.w = self.w, None
            try:
                os.close(fd)
            except OSError:
                pass

    def feed(self, data):
        def run():
            try:
                self._write(data)
            except OSError:
                pass
            finally:
                # Signal a real EOF once the writer is done, so the reader finishes
                # promptly instead of waiting out a timeout.
                self._close_writer()

        threading.Thread(target=run, daemon=True).start()

    def close(self):
        try:
            self.reader.close()
        except OSError:
            pass
        self._close_writer()
        if self._reader_sock is not None:
            try:
                self._reader_sock.close()
            except OSError:
                pass
            self._reader_sock = None


def _capture(pipe, monkeypatch, *, wait=2.0, phrase=5.0, silence=0.5):
    # Override the (generous) production windows so synthetic runs end promptly;
    # the real defaults are asserted by test_phrase_windows_are_generous_by_default.
    monkeypatch.setattr(voice, "_LISTEN_TIMEOUT", wait)
    monkeypatch.setattr(voice, "_PHRASE_TIME_LIMIT", phrase)
    monkeypatch.setattr(voice, "_SILENCE_SECONDS", silence)
    return voice._capture_from_stream(pipe.reader)


# --------------------------------------------------------------------------- #
# gating
# --------------------------------------------------------------------------- #

def test_starts_on_speech_and_stops_on_silence(monkeypatch):
    pipe = _Pipe()
    try:
        leading = _silence(0.3)
        speech = _tone(0.4, 3000)
        trailing = _silence(1.5)
        pipe.feed(leading + speech + trailing)

        data = _capture(pipe, monkeypatch)

        assert data, "speech should have been captured"
        assert voice._rms(data) > voice._ENERGY_THRESHOLD
        # Leading silence is dropped before speech starts; capture is bounded by
        # the whole stream (trailing silence stops it early).
        assert not data.startswith(leading)
        assert len(data) <= len(leading + speech + trailing)
    finally:
        pipe.close()


def test_times_out_without_speech(monkeypatch):
    pipe = _Pipe()
    try:
        pipe.feed(_silence(2.0))
        started = time.monotonic()
        data = _capture(pipe, monkeypatch, wait=0.3)
        elapsed = time.monotonic() - started

        assert data == b""
        assert elapsed < 1.5, "must return near the wait bound, not hang"
    finally:
        pipe.close()


def test_phrase_length_is_capped(monkeypatch):
    """A continuous loud tone must stop at ~_PHRASE_TIME_LIMIT, not run forever."""
    pipe = _Pipe()
    try:
        pipe.feed(_tone(4.0, 3000))
        data = _capture(pipe, monkeypatch, phrase=0.5)

        # 0.5 s of phrase + at most a little slack while the cap is noticed.
        assert 0 < len(data) <= int(1.0 * voice._SAMPLE_RATE) * voice._SAMPLE_WIDTH
    finally:
        pipe.close()


def test_pre_roll_keeps_soft_onset(monkeypatch):
    """A word that starts below the 300 gate keeps its first syllable."""
    pipe = _Pipe()
    try:
        soft = _tone(0.3, 250)          # below the start gate, above the floor
        loud = _tone(0.5, 2000)
        pipe.feed(soft + loud + _silence(1.5))

        data = _capture(pipe, monkeypatch)

        assert data
        # The capture begins with the soft onset (kept by the pre-roll), so its
        # RMS sits between the silence floor and the start gate.
        onset = data[: int(0.2 * voice._SAMPLE_RATE) * voice._SAMPLE_WIDTH]
        assert voice._SILENCE_THRESHOLD < voice._rms(onset) < voice._ENERGY_THRESHOLD
    finally:
        pipe.close()


def test_short_dip_in_middle_is_not_cut(monkeypatch):
    """Regression: a dip between words must not truncate the phrase.

    The dip sits between the floor (150) and the start gate (300). Under the old
    single-threshold / 1 s logic the capture stopped ~1 s into the dip and lost
    the second word; with hysteresis the whole phrase survives.
    """
    pipe = _Pipe()
    try:
        first = _tone(0.4, 2000)
        dip = _tone(1.5, 200)           # 150 < 200 < 300 -> counted as speech
        second = _tone(0.4, 2000)
        pipe.feed(first + dip + second)

        data = _capture(pipe, monkeypatch, phrase=8.0)

        assert len(data) >= int(2.0 * voice._SAMPLE_RATE) * voice._SAMPLE_WIDTH
        tail = data[-int(0.3 * voice._SAMPLE_RATE) * voice._SAMPLE_WIDTH:]
        assert voice._rms(tail) > voice._ENERGY_THRESHOLD, "second word was lost"
    finally:
        pipe.close()


# --------------------------------------------------------------------------- #
# listen() wiring
# --------------------------------------------------------------------------- #

def test_listen_wraps_bytes_in_audiodata_and_recognises(monkeypatch):
    pipe = _Pipe()
    try:
        pipe.feed(_silence(0.2) + _tone(0.5, 4000) + _silence(1.2))

        class FakeProc:
            def __init__(self, stream):
                self.stdout = stream

            def poll(self):
                return None

            def terminate(self):
                pass

            def wait(self, timeout=None):
                return 0

            def kill(self):
                pass

        monkeypatch.setattr(voice, "_spawn_arecord", lambda device=None: FakeProc(pipe.reader))
        seen = {}

        def fake_recognize_google(self, audio, language=None):
            seen["data"] = audio.get_raw_data()
            seen["rate"] = audio.sample_rate
            seen["width"] = audio.sample_width
            seen["language"] = language
            return "hello jarvis"

        monkeypatch.setattr(sr.Recognizer, "recognize_google", fake_recognize_google)

        assert voice.listen() == "hello jarvis"
        assert seen["rate"] == 16000
        assert seen["width"] == 2
        assert seen["language"] == "en-US"
        assert voice._rms(seen["data"]) > voice._ENERGY_THRESHOLD
    finally:
        pipe.close()


def test_listen_returns_empty_when_arecord_missing(monkeypatch):
    def boom(device=None):
        raise FileNotFoundError("arecord")

    monkeypatch.setattr(voice, "_spawn_arecord", boom)
    assert voice.listen() == ""


def _recognising_listen(monkeypatch, recognise, **listen_kwargs):
    """Drive one listen() pass over synthetic speech with a fake recogniser."""
    pipe = _Pipe()
    pipe.feed(_silence(0.2) + _tone(0.5, 4000) + _silence(1.2))

    class FakeProc:
        def __init__(self, stream):
            self.stdout = stream

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    monkeypatch.setattr(voice, "_spawn_arecord", lambda device=None: FakeProc(pipe.reader))
    monkeypatch.setattr(sr.Recognizer, "recognize_google", recognise)
    try:
        return voice.listen(**listen_kwargs)
    finally:
        pipe.close()


def test_listening_does_not_pause_media(monkeypatch):
    """Jarvis listens while media plays; it must never pause it automatically."""
    import desktop

    monkeypatch.setattr(desktop, "pause_media_if_playing",
                        lambda: pytest.fail("listen() must not pause media"))
    monkeypatch.setattr(desktop, "resume_media",
                        lambda token: pytest.fail("listen() must not resume media"))

    def recognise(self, audio, language=None):
        return "pause music"

    assert _recognising_listen(monkeypatch, recognise) == "pause music"


def test_google_failure_falls_back_to_local_stt(monkeypatch):
    """A network error from Google STT must not lose the phrase: transcribe it
    locally instead."""
    def request_error(self, audio, language=None):
        raise sr.RequestError("network is down")

    monkeypatch.setattr(voice, "_local_transcribe", lambda audio: "local words")

    assert _recognising_listen(monkeypatch, request_error) == "local words"


def test_google_failure_without_local_model_returns_empty(monkeypatch):
    def request_error(self, audio, language=None):
        raise sr.RequestError("network is down")

    monkeypatch.setattr(voice, "_local_transcribe", lambda audio: "")

    assert _recognising_listen(monkeypatch, request_error) == ""


def test_barge_in_stop_phrase_cuts_speech_without_dispatching(monkeypatch):
    stops = []
    monkeypatch.setattr(voice, "stop_speaking", lambda: stops.append("speaking"))
    monkeypatch.setattr(voice, "stop_listening", lambda: stops.append("listening"))

    def recognise(self, audio, language=None):
        return "stop"

    assert _recognising_listen(monkeypatch, recognise, barge=True) == ""
    assert stops == ["speaking", "listening"]


def test_barge_in_does_not_swallow_the_mic_command(monkeypatch):
    """'stop listening' is a real mic command, not a bare interruption."""
    def recognise(self, audio, language=None):
        return "stop listening"

    assert _recognising_listen(monkeypatch, recognise, barge=True) == "stop listening"


def test_stop_phrase_helper_accepts_normal_interruptions():
    for phrase in ("stop", "please stop", "stop it now", "shut up",
                   "quiet", "be quiet", "silence"):
        assert voice.is_stop_phrase(phrase), phrase
    for phrase in ("stop the music", "stop listening", "don't stop", ""):
        assert not voice.is_stop_phrase(phrase), phrase


def test_stop_phrase_helper_accepts_chatgpt_style_interruptions():
    """'wait', 'listen' and 'hold on' cut the reply without touching a command."""
    for phrase in ("wait", "wait a second", "wait a moment", "hold on",
                   "hold on a second", "hang on", "listen", "listen up",
                   "hey jarvis, hold on"):
        assert voice.is_stop_phrase(phrase), phrase
    # Commands that merely start with an interruption word must still dispatch.
    for phrase in ("wait for the download", "listen to music",
                   "hold on to this file", "stop the music"):
        assert not voice.is_stop_phrase(phrase), phrase


def test_local_transcribe_returns_empty_without_faster_whisper(monkeypatch):
    import builtins

    monkeypatch.setattr(voice, "_whisper_model", None)
    monkeypatch.setattr(voice, "_whisper_unavailable", False)

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "faster_whisper":
            raise ImportError("no faster_whisper")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    audio = sr.AudioData(b"\x00\x00" * 100, voice._SAMPLE_RATE, voice._SAMPLE_WIDTH)
    assert voice._local_transcribe(audio) == ""
    assert voice._whisper_unavailable is True


# --------------------------------------------------------------------------- #
# STT name biasing (initial_prompt) and misheard-name correction
# --------------------------------------------------------------------------- #

class _FakeWhisperModel:
    """Stand-in for faster_whisper.WhisperModel that records the call kwargs."""

    def __init__(self, text="my name is Jakir"):
        self.text = text
        self.calls = []

    def transcribe(self, path, **kwargs):
        self.calls.append({"path": path, **kwargs})
        segment = type("Segment", (), {"text": self.text})
        return [segment], {"language": "en"}


def test_local_transcribe_passes_the_name_biased_initial_prompt(monkeypatch):
    model = _FakeWhisperModel()
    monkeypatch.setattr(voice, "_whisper_model", model)
    monkeypatch.setattr(voice, "_whisper_unavailable", False)

    audio = sr.AudioData(b"\x00\x00" * 100, voice._SAMPLE_RATE, voice._SAMPLE_WIDTH)
    assert voice._local_transcribe(audio) == "my name is Jakir"

    assert len(model.calls) == 1
    assert model.calls[0]["initial_prompt"] == voice.STT_INITIAL_PROMPT
    assert model.calls[0]["initial_prompt"] == (
        "My name is Jakir. Jarvis is an AI assistant. Dhaka, Bangladesh."
    )
    # The language hint must still be sent alongside the prompt.
    assert model.calls[0]["language"] == voice.STT_LANG.split("-")[0]


def test_google_output_is_corrected_when_the_name_is_misheard(monkeypatch):
    def recognise(self, audio, language=None):
        return "my name is Jahan"

    assert _recognising_listen(monkeypatch, recognise) == "my name is jakir"


def test_zaaakir_spelling_is_corrected(monkeypatch):
    def recognise(self, audio, language=None):
        return "hello zaaakir"

    assert _recognising_listen(monkeypatch, recognise) == "hello jakir"


def test_name_correction_leaves_unrelated_sentences_alone():
    assert voice._fix_misheard_name("the jahan dynasty was famous") == (
        "the jahan dynasty was famous"
    )
    assert voice._fix_misheard_name("my name is Jakir") == "my name is Jakir"
    assert voice._fix_misheard_name("") == ""


def test_listen_returns_empty_without_capturing_when_muted(monkeypatch):
    """A muted mic must not even open arecord (nor call STT)."""
    monkeypatch.setattr(voice, "_mic_muted", True)
    spawned = []
    monkeypatch.setattr(voice, "_spawn_arecord",
                        lambda device=None: spawned.append(device))

    assert voice.listen() == ""
    assert spawned == []


def test_muting_mid_capture_discards_the_phrase(monkeypatch):
    """Pressing Mute while listening must end the capture and drop the phrase."""
    pipe = _Pipe()
    try:
        pipe.feed(_tone(3.0, 3000))
        voice.set_level_sink(lambda _v: voice.set_mic_muted(True))
        data = _capture(pipe, monkeypatch, phrase=5.0)
    finally:
        voice.set_level_sink(None)
        pipe.close()

    assert data == b"", "a phrase captured after Mute must be discarded"


def test_listen_timeout_is_generous_by_default():
    """Jarvis should wait a long time for you to start speaking (not 4 s)."""
    if os.getenv("JARVIS_LISTEN_TIMEOUT"):
        pytest.skip("JARVIS_LISTEN_TIMEOUT is set in this environment")
    assert voice._LISTEN_TIMEOUT >= 25


def test_phrase_windows_are_generous_by_default():
    """A whole sentence must fit, and a thinking pause must not cut it."""
    if os.getenv("JARVIS_PHRASE_LIMIT") or os.getenv("JARVIS_SILENCE_END"):
        pytest.skip("the tuning env vars are set in this environment")
    assert voice._PHRASE_TIME_LIMIT >= 12, "a full sentence must not be truncated"
    assert voice._SILENCE_SECONDS >= 1.5, "a pause mid-sentence must not end it"


def test_a_pause_between_words_is_not_a_full_stop(monkeypatch):
    """Regression: "play ... kesariya" was cut off during the thinking pause.

    With the old 1.2 s end-of-phrase window an 0.8 s pause survived, but any pause
    past it truncated the sentence; the window is now 2 s by default.
    """
    eager, patient = _Pipe(), _Pipe()
    try:
        utterance = (_tone(0.4, 3000) + _silence(0.8) + _tone(0.4, 3000)
                     + _silence(3.0))
        eager.feed(utterance)
        patient.feed(utterance)

        cut = _capture(eager, monkeypatch, silence=0.5)      # a too-eager window
        kept = _capture(patient, monkeypatch, silence=2.0)   # the new default

        assert len(cut) < len(kept), "a 0.8 s pause must not truncate the phrase"
        assert len(kept) >= int(1.4 * voice._SAMPLE_RATE) * voice._SAMPLE_WIDTH
    finally:
        eager.close()
        patient.close()


def test_stop_listening_abandons_the_wait(monkeypatch):
    """Stop (from the GUI) must end a listen that is still waiting for speech."""
    pipe = _Pipe()
    try:
        pipe.feed(_silence(4.0))
        # The level sink fires on every chunk; the first one presses Stop.
        voice.set_level_sink(lambda _v: voice.stop_listening())

        started = time.monotonic()
        data = _capture(pipe, monkeypatch, wait=15.0)
        elapsed = time.monotonic() - started
    finally:
        voice.set_level_sink(None)
        pipe.close()

    assert data == b""
    assert elapsed < 1.5, "Stop should end the wait almost immediately"


def test_capture_feeds_the_live_level_sink(monkeypatch):
    """The GUI animation is fed the chunk level, and a final 0 when it ends."""
    seen = []
    voice.set_level_sink(seen.append)
    pipe = _Pipe()
    try:
        pipe.feed(_silence(0.3) + _tone(0.4, 3000) + _silence(1.5))
        data = _capture(pipe, monkeypatch)
    finally:
        voice.set_level_sink(None)
        pipe.close()

    assert data, "speech should still have been captured"
    assert seen, "the level sink should have received live values"
    assert all(0.0 <= value <= 1.0 for value in seen)
    assert seen[-1] == 0.0, "the sink must go quiet when capture ends"


# --------------------------------------------------------------------------- #
# calibration / warm-up (step 7)
# --------------------------------------------------------------------------- #

def test_calibrate_floor_is_1_5x_median_clamped():
    assert voice._calibrate([]) == voice._SILENCE_THRESHOLD
    assert voice._calibrate([10, 20, 30]) == voice._SILENCE_THRESHOLD   # 15 -> 150
    assert voice._calibrate([120]) == 180                               # 1.5x120
    assert voice._calibrate([250]) == voice._MAX_SILENCE_THRESHOLD      # 375 -> 300
    # The median ignores an outlier (e.g. the open spike).
    assert voice._calibrate([100, 120, 400]) == 180


def test_stream_open_spike_is_discarded(monkeypatch):
    """The ~6000 stream-open spike must not itself trigger a capture."""
    pipe = _Pipe()
    try:
        spike = _tone(0.1, 6000)          # one chunk, exactly
        pipe.feed(spike + _silence(0.6) + _tone(0.4, 3000) + _silence(1.5))

        data = _capture(pipe, monkeypatch)

        assert data, "the real speech after the spike should be captured"
        onset = data[: voice._CHUNK_BYTES]
        assert voice._rms(onset) < voice._ENERGY_THRESHOLD, "spike leaked into capture"
    finally:
        pipe.close()


def test_speech_that_starts_immediately_is_not_cut(monkeypatch):
    """A phrase starting on the very first chunk must keep its onset."""
    pipe = _Pipe()
    try:
        pipe.feed(_tone(1.0, 3000) + _silence(1.5))

        data = _capture(pipe, monkeypatch, phrase=8.0)

        assert data
        onset = data[: voice._CHUNK_BYTES]
        assert voice._rms(onset) > voice._ENERGY_THRESHOLD, "the first word was cut"
    finally:
        pipe.close()


def test_median_ignores_outliers():
    assert voice._median([]) == 0
    assert voice._median([10, 20, 30]) == 20
    assert voice._median([100, 120, 400]) == 120


# --------------------------------------------------------------------------- #
# push-to-talk (JARVIS_PTT=1)
# --------------------------------------------------------------------------- #

def test_ptt_records_without_waiting_for_the_start_gate(monkeypatch):
    """Push-to-talk records as soon as Enter is pressed, even below the gate.

    A 250 tone sits above the silence floor but below the 300 start gate, so the
    automatic mode ignores it; PTT must still capture it.
    """
    pipe = _Pipe()
    try:
        pipe.feed(_tone(0.4, 250) + _silence(1.5))

        data = voice._capture_from_stream(pipe.reader, ptt=True)

        assert data, "PTT should record even below the start gate"
        assert voice._peak(data) == 250
    finally:
        pipe.close()


def test_same_soft_tone_is_ignored_without_ptt(monkeypatch):
    pipe = _Pipe()
    try:
        pipe.feed(_tone(0.4, 250) + _silence(0.5))

        data = _capture(pipe, monkeypatch, wait=0.3)

        assert data == b""                 # below the 300 gate -> never starts
    finally:
        pipe.close()


def test_ptt_stop_event_ends_the_phrase():
    """A set stop_event (second Enter) ends the phrase immediately."""
    pipe = _Pipe()
    try:
        pipe.feed(_tone(3.0, 3000))
        stop = threading.Event()
        stop.set()

        data = voice._capture_from_stream(pipe.reader, ptt=True, stop_event=stop)

        assert data == b""
    finally:
        pipe.close()


# --------------------------------------------------------------------------- #
# speak(): must never crash the session and always show the reply
# --------------------------------------------------------------------------- #

def test_speak_prints_reply_and_survives_tts_failure(monkeypatch, capsys):
    monkeypatch.setattr(voice, "_MUTE", False)

    def boom(text):
        raise RuntimeError("offline")

    monkeypatch.setattr(voice, "_play_tts", boom)

    voice.speak("hello there")                 # must not raise
    out = capsys.readouterr().out
    assert "🤖 hello there" in out
    assert "TTS failed" in out


def test_speak_muted_skips_tts(monkeypatch, capsys):
    monkeypatch.setattr(voice, "_MUTE", True)
    called = []
    monkeypatch.setattr(voice, "_play_tts", lambda text: called.append(text))

    voice.speak("hi")
    assert called == []
    assert "🤖 hi" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# recorded speech WAV (opt-in)
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(
    not os.getenv("JARVIS_TEST_WAV"),
    reason="set JARVIS_TEST_WAV to a 16 kHz mono S16_LE WAV of speech",
)
def test_gate_on_recorded_speech_wav():
    path = os.environ["JARVIS_TEST_WAV"]
    if not os.path.exists(path):
        pytest.skip(f"{path} not found")
    with wave.open(path, "rb") as wf:
        assert wf.getframerate() == voice._SAMPLE_RATE
        assert wf.getsampwidth() == voice._SAMPLE_WIDTH
        raw = wf.readframes(wf.getnframes())

    pipe = _Pipe()
    try:
        pipe.feed(raw)
        data = voice._capture_from_stream(pipe.reader)
        assert data, "the recorded speech should trip the gate"
        assert voice._rms(data) > voice._ENERGY_THRESHOLD
    finally:
        pipe.close()


# --------------------------------------------------------------------------- #
# Silero VAD endpointing
# --------------------------------------------------------------------------- #

def _vad_frame():
    """One exact Silero frame: 512 samples of S16_LE silence."""
    import array

    return array.array("h", [0] * voice._VAD_FRAME_SAMPLES).tobytes()


def test_silero_stream_requires_consecutive_speech_to_start():
    probabilities = iter([0.9, 0.9, 0.1])
    stream = voice._SileroVADStream(
        lambda _pcm: next(probabilities),
        threshold=0.5,
        start_ms=64,
        end_ms=64,
    )

    assert stream.feed(_vad_frame()) is False   # one 32 ms frame
    assert stream.feed(_vad_frame()) is True    # 64 ms -> confirmed speech


def test_silero_stream_ends_after_the_silence_window():
    probabilities = iter([0.1, 0.1])
    stream = voice._SileroVADStream(
        lambda _pcm: next(probabilities),
        threshold=0.5,
        start_ms=64,
        end_ms=64,
    )
    stream.mark_started()

    stream.feed(_vad_frame())
    assert stream.silence_ms == 32
    assert stream.should_end() is False
    stream.feed(_vad_frame())
    assert stream.silence_ms == 64
    assert stream.should_end() is True


def test_silero_stream_buffers_partial_frames():
    calls = []

    def probability(frame):
        calls.append(len(frame))
        return 0.9

    stream = voice._SileroVADStream(probability, start_ms=32)
    data = _vad_frame()

    stream.feed(data[:300])
    assert calls == []                 # not a complete 512-sample frame
    stream.feed(data[300:])
    assert calls == [voice._VAD_FRAME_BYTES]


class _FakeVADStream:
    """Deterministic VAD double: loud chunks are speech, quiet chunks silence."""

    def __init__(self):
        self.started = False
        self.silence_ms = 0

    def feed(self, chunk):
        if not self.started:
            if voice._rms(chunk) > 1000:
                self.started = True
                return True
            return False
        self.silence_ms = 0 if voice._rms(chunk) > 300 else self.silence_ms + 100
        return False

    def mark_started(self):
        self.started = True
        self.silence_ms = 0

    def should_end(self):
        return self.started and self.silence_ms >= 300


def test_capture_uses_silero_to_end_the_phrase(monkeypatch):
    fake = _FakeVADStream()
    monkeypatch.setattr(voice, "_silero_stream", lambda: fake)
    pipe = _Pipe()
    try:
        pipe.feed(_silence(0.3) + _tone(0.4, 3000) + _silence(1.5))
        # RMS's fixed window is deliberately huge: only VAD can end this early.
        data = _capture(pipe, monkeypatch, silence=5.0)
    finally:
        pipe.close()

    assert fake.started is True
    assert data, "VAD-triggered speech should be captured"
    # 0.4 s speech + ~0.3 s VAD tail + 0.3 s pre-roll, not the full 1.5 s tail.
    assert len(data) < int(1.4 * voice._SAMPLE_RATE) * voice._SAMPLE_WIDTH


def test_silero_initialization_failure_falls_back_to_rms(monkeypatch, log_dir):
    def boom():
        raise RuntimeError("silero exploded")

    monkeypatch.setattr(voice, "_silero_stream", boom)
    pipe = _Pipe()
    try:
        pipe.feed(_silence(0.3) + _tone(0.4, 3000) + _silence(0.8))
        data = _capture(pipe, monkeypatch, silence=0.5)
    finally:
        pipe.close()

    assert data, "the RMS fallback must still capture the phrase"
    log = (log_dir / "command_errors.log").read_text(encoding="utf-8")
    assert "Silero VAD could not start" in log
    assert "RuntimeError: silero exploded" in log


def test_silero_midstream_failure_falls_back_to_rms(monkeypatch, log_dir):
    class BrokenVAD:
        def feed(self, _chunk):
            raise RuntimeError("inference failed")

        def mark_started(self):
            pass

        def should_end(self):
            return False

    monkeypatch.setattr(voice, "_silero_stream", lambda: BrokenVAD())
    pipe = _Pipe()
    try:
        pipe.feed(_silence(0.3) + _tone(0.4, 3000) + _silence(0.8))
        data = _capture(pipe, monkeypatch, silence=0.5)
    finally:
        pipe.close()

    assert data, "the RMS fallback must finish the phrase"
    log = (log_dir / "command_errors.log").read_text(encoding="utf-8")
    assert "Silero VAD failed during capture" in log
    assert "inference failed" in log


def test_silero_can_be_disabled(monkeypatch):
    monkeypatch.setenv("JARVIS_SILERO_VAD", "0")

    assert voice._silero_stream() is None
