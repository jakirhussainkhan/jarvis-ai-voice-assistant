"""Tests for the arecord-based capture gate in voice.listen().

These exercise the RMS gating without a microphone: synthetic PCM is pushed
through a real OS pipe (so the select/read path is covered) and the arecord
subprocess is stubbed. The live microphone cannot be driven from a test.
"""

import os
import threading
import time
import wave

import pytest
import speech_recognition as sr

import voice


@pytest.fixture(autouse=True)
def _no_media_pause(monkeypatch):
    """Never touch the real audio sink during tests: pause/resume are stubbed."""
    monkeypatch.setattr(voice.desktop, "pause_media_if_playing", lambda: None)
    monkeypatch.setattr(voice.desktop, "resume_media", lambda token: None)
    monkeypatch.setattr(voice, "_level_sink", None)
    monkeypatch.setattr(voice, "_mic_muted", False)
    monkeypatch.setattr(voice, "_stop_listening", threading.Event())


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
    """A real OS pipe exposed as a binary file object, with a writer thread."""

    def __init__(self):
        r, self.w = os.pipe()
        self.reader = os.fdopen(r, "rb", buffering=0)

    def feed(self, data):
        def run():
            try:
                os.write(self.w, data)
            except OSError:
                pass
            finally:
                # Signal a real EOF once the writer is done, so the reader finishes
                # promptly instead of waiting out a timeout.
                try:
                    os.close(self.w)
                except OSError:
                    pass

        threading.Thread(target=run, daemon=True).start()

    def close(self):
        for closer in (self.reader.close, lambda: os.close(self.w)):
            try:
                closer()
            except OSError:
                pass


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
