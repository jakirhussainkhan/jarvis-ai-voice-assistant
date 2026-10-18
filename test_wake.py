"""Tests for the offline "Hey Jarvis" wake word (wake.py).

openWakeWord is an optional, heavy dependency (ONNX runtime + a few MB of
models), so the tests must not require it. Everything *around* the model is
covered here instead:

* the framing/threshold logic, against a fake scoring backend;
* the listening thread, against a fake microphone (voice's arecord helpers are
  stubbed), including the rule that matters most -- the microphone is handed over
  *before* ``on_wake`` runs, so the wake listener and ``voice.listen`` can never
  hold the device at the same time.

The only thing left to the real machine is whether the model itself recognises
your voice.
"""

import threading
import time
import wave
from io import BytesIO

import pytest

import voice
import wake


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

class FakeBackend:
    """Scores frames from a fixed script; records the frame sizes it saw."""

    def __init__(self, scores=()):
        self.scores = list(scores)
        self.frames = []
        self.resets = 0

    def score(self, frame):
        self.frames.append(len(frame))
        return self.scores.pop(0) if self.scores else 0.0

    def reset(self):
        self.resets += 1


def _frames(count):
    """``count`` frames of silence, as raw S16_LE mono 16 kHz bytes."""
    return b"\x00\x00" * (wake.FRAME_SAMPLES * count)


def _detector(scores, threshold=0.5):
    return wake.Detector(backend=FakeBackend(scores), threshold=threshold)


def _wait_for(predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def mic(monkeypatch):
    """A fake microphone: scripted chunks, and open/close/mute bookkeeping."""
    state = {"opens": 0, "closes": 0, "muted": False, "chunks": [], "idx": 0}

    class Stdout:
        def fileno(self):
            return 123

    class Proc:
        stdout = Stdout()

    proc = Proc()

    def spawn(device=None):
        state["opens"] += 1
        return proc

    def read(stream, fd, deadline):
        if state["idx"] < len(state["chunks"]):
            chunk = state["chunks"][state["idx"]]
            state["idx"] += 1
            return chunk
        time.sleep(0.01)
        return b""

    def stop(p):
        assert p is proc, "the listener must only stop the process it opened"
        state["closes"] += 1

    monkeypatch.setattr(voice, "_spawn_arecord", spawn)
    monkeypatch.setattr(voice, "_read_chunk", read)
    monkeypatch.setattr(voice, "_stop_arecord", stop)
    monkeypatch.setattr(voice, "is_mic_muted", lambda: state["muted"])
    monkeypatch.setattr(voice, "_stop_listening", threading.Event())
    return state


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #

def test_install_hint_names_the_engine():
    hint = wake.install_hint()
    assert "openwakeword" in hint
    assert "pip install" in hint


def test_model_name_is_normalised(monkeypatch):
    monkeypatch.delenv("JARVIS_WAKE_MODEL", raising=False)
    assert wake.model_name() == "hey jarvis"
    # openWakeWord uses spaces, but people (and env files) write underscores.
    assert wake.model_name("hey_jarvis") == "hey jarvis"
    assert wake.model_name("Hey Jarvis") == "hey jarvis"

    monkeypatch.setenv("JARVIS_WAKE_MODEL", "alexa")
    assert wake.model_name() == "alexa"


def test_enabled_needs_the_engine_installed(monkeypatch):
    monkeypatch.delenv("JARVIS_WAKE", raising=False)
    monkeypatch.setattr(wake, "available", lambda: False)
    assert wake.enabled() is False, "no engine, no listening"


def test_enabled_is_on_by_default_once_installed(monkeypatch):
    monkeypatch.delenv("JARVIS_WAKE", raising=False)
    monkeypatch.setattr(wake, "available", lambda: True)
    assert wake.enabled() is True


def test_enabled_honours_the_env_switch(monkeypatch):
    monkeypatch.setattr(wake, "available", lambda: True)
    for raw, expected in (("0", False), ("1", True), ("no", False), ("yes", True)):
        monkeypatch.setenv("JARVIS_WAKE", raw)
        assert wake.enabled() is expected


def test_a_model_name_of_off_disables_it(monkeypatch):
    monkeypatch.delenv("JARVIS_WAKE", raising=False)
    monkeypatch.setattr(wake, "available", lambda: True)
    assert wake.enabled("off") is False


def test_env_float_survives_junk(monkeypatch):
    """A typo in .env must never stop Jarvis from starting."""
    monkeypatch.setenv("JARVIS_WAKE_TEST", "loud")
    assert wake._env_float("JARVIS_WAKE_TEST", 0.5) == 0.5

    monkeypatch.setenv("JARVIS_WAKE_TEST", "")
    assert wake._env_float("JARVIS_WAKE_TEST", 0.5) == 0.5

    monkeypatch.setenv("JARVIS_WAKE_TEST", "0.9")
    assert wake._env_float("JARVIS_WAKE_TEST", 0.5) == 0.9

    monkeypatch.setenv("JARVIS_WAKE_TEST", "-3")
    assert wake._env_float("JARVIS_WAKE_TEST", 0.5, minimum=0.01) == 0.5


def test_import_time_settings_are_sane():
    assert 0.0 < wake._THRESHOLD <= 1.0
    assert wake._VAD >= 0.0
    assert wake.FRAME_BYTES == wake.FRAME_SAMPLES * 2
    assert wake.FRAME_SAMPLES == 1280, "the model wants multiples of 80 ms"


# --------------------------------------------------------------------------- #
# framing + thresholding (no model needed)
# --------------------------------------------------------------------------- #

def test_detector_fires_above_the_threshold():
    backend = FakeBackend([0.1, 0.9])
    detector = wake.Detector(backend=backend, threshold=0.5)

    assert detector.feed(_frames(2)) is True
    assert backend.frames == [wake.FRAME_BYTES, wake.FRAME_BYTES]


def test_detector_stays_quiet_below_the_threshold():
    assert _detector([0.2, 0.49]).feed(_frames(2)) is False


def test_detector_threshold_is_inclusive():
    assert _detector([0.5]).feed(_frames(1)) is True


def test_detector_uses_the_configured_threshold():
    assert _detector([0.6], threshold=0.7).feed(_frames(1)) is False
    assert _detector([0.6], threshold=0.6).feed(_frames(1)) is True


def test_detector_re_frames_partial_audio():
    backend = FakeBackend([0.9])
    detector = wake.Detector(backend=backend, threshold=0.5)
    half = wake.FRAME_BYTES // 2

    assert detector.feed(_frames(1)[:half]) is False
    assert backend.frames == [], "a partial frame must not be scored"

    assert detector.feed(_frames(1)[half:]) is True
    assert backend.frames == [wake.FRAME_BYTES]


def test_detector_handles_several_frames_in_one_feed():
    backend = FakeBackend([0.0, 0.0, 0.0])
    detector = wake.Detector(backend=backend, threshold=0.5)

    assert detector.feed(_frames(3)) is False
    assert backend.frames == [wake.FRAME_BYTES] * 3


def test_detector_stops_at_the_first_hit():
    """One phrase must mean one wake, so the rest of the buffer is dropped."""
    backend = FakeBackend([0.9, 0.9])
    detector = wake.Detector(backend=backend, threshold=0.5)

    assert detector.feed(_frames(2)) is True
    assert backend.frames == [wake.FRAME_BYTES], "must not score the tail too"


def test_detector_starts_clean_after_a_hit():
    backend = FakeBackend([0.9, 0.9])
    detector = wake.Detector(backend=backend, threshold=0.5)

    detector.feed(_frames(2))
    detector.feed(_frames(1))

    assert backend.frames == [wake.FRAME_BYTES, wake.FRAME_BYTES]


def test_detector_ignores_empty_audio():
    backend = FakeBackend([0.9])
    detector = wake.Detector(backend=backend, threshold=0.5)

    assert detector.feed(b"") is False
    assert backend.frames == []


def test_detector_survives_a_broken_backend():
    class Boom(FakeBackend):
        def score(self, frame):
            raise RuntimeError("model exploded")

    assert wake.Detector(backend=Boom(), threshold=0.5).feed(_frames(2)) is False


def test_detector_reset_clears_buffer_and_backend():
    backend = FakeBackend()
    detector = wake.Detector(backend=backend, threshold=0.5)
    detector.feed(_frames(1)[:10])          # half a frame sits in the buffer

    detector.reset()

    assert detector._buffer == bytearray()
    assert backend.resets == 1


# --------------------------------------------------------------------------- #
# the listening thread
# --------------------------------------------------------------------------- #

def test_listener_hands_the_mic_over_before_waking(mic):
    """The capture must be closed *before* on_wake, not after."""
    heard = threading.Event()
    seen = {}

    def on_wake():
        seen["closes"] = mic["closes"]
        heard.set()

    mic["chunks"] = [b"\x00" * voice._CHUNK_BYTES] * 2
    listener = wake.Listener(on_wake, detector=_detector([0.0, 0.9]), chime=False)
    listener.start()
    try:
        assert heard.wait(3.0), "the wake word was never reported"
        assert mic["closes"] >= 1
        assert seen["closes"] >= 1, "the mic was still open when on_wake ran"
        assert listener.paused
    finally:
        listener.stop()
    listener.join(3.0)
    assert not listener.is_alive()


def test_listener_pauses_and_resumes(mic):
    listener = wake.Listener(lambda: None, detector=_detector([]), chime=False)
    listener.start()
    try:
        assert _wait_for(lambda: mic["opens"] >= 1)

        listener.pause()
        assert listener.paused
        assert mic["closes"] >= 1
        opens = mic["opens"]
        time.sleep(0.2)
        assert mic["opens"] == opens, "must not reopen while paused"

        listener.resume()
        assert _wait_for(lambda: mic["opens"] > opens)
    finally:
        listener.stop()
    listener.join(3.0)
    assert listener.status == "stopped"


def test_listener_never_opens_a_muted_mic(mic):
    mic["muted"] = True
    listener = wake.Listener(lambda: None, detector=_detector([]), chime=False)
    listener.start()
    try:
        assert _wait_for(lambda: listener.status == "paused")
        time.sleep(0.2)
        assert mic["opens"] == 0
    finally:
        listener.stop()
    listener.join(3.0)


def test_muting_closes_an_open_mic(mic):
    listener = wake.Listener(lambda: None, detector=_detector([]), chime=False)
    listener.start()
    try:
        assert _wait_for(lambda: mic["opens"] >= 1)
        mic["muted"] = True
        assert _wait_for(lambda: mic["closes"] >= 1)
    finally:
        listener.stop()
    listener.join(3.0)


def test_unmuting_starts_listening_again(mic):
    mic["muted"] = True
    listener = wake.Listener(lambda: None, detector=_detector([]), chime=False)
    listener.start()
    try:
        assert _wait_for(lambda: listener.status == "paused")
        mic["muted"] = False
        assert _wait_for(lambda: mic["opens"] >= 1)
    finally:
        listener.stop()
    listener.join(3.0)


def test_listener_survives_a_detector_error(mic):
    class Boom:
        model = "boom"
        threshold = 0.5

        def feed(self, pcm):
            raise RuntimeError("bang")

        def reset(self):
            pass

    mic["chunks"] = [b"\x00" * voice._CHUNK_BYTES] * 3
    listener = wake.Listener(lambda: None, detector=Boom(), chime=False)
    listener.start()
    try:
        assert _wait_for(lambda: mic["idx"] >= 3)
        assert listener.is_alive()
    finally:
        listener.stop()
    listener.join(3.0)


def test_listener_reports_a_broken_microphone(monkeypatch):
    def boom(device=None):
        raise OSError("no such device")

    monkeypatch.setattr(voice, "_spawn_arecord", boom)
    monkeypatch.setattr(voice, "is_mic_muted", lambda: False)

    listener = wake.Listener(lambda: None, detector=_detector([]), chime=False)
    listener.start()
    try:
        assert _wait_for(lambda: listener.status == "no-microphone")
    finally:
        listener.stop()
    listener.join(3.0)


def test_listener_reports_a_missing_model(mic, monkeypatch, capsys):
    def broken(*_args, **_kwargs):
        raise RuntimeError("openWakeWord is not installed")

    monkeypatch.setattr(wake, "Detector", broken)
    listener = wake.Listener(lambda: None, chime=False)
    listener.start()
    listener.join(3.0)

    assert not listener.is_alive()
    assert listener.status == "unavailable"
    out = capsys.readouterr().out
    assert "openwakeword" in out and "pip install" in out


def test_listener_counts_a_failed_wake_callback_as_survivable(mic):
    """on_wake raising must not kill the thread."""
    mic["chunks"] = [b"\x00" * voice._CHUNK_BYTES] * 2

    def boom():
        raise RuntimeError("the GUI went away")

    listener = wake.Listener(boom, detector=_detector([0.0, 0.9]), chime=False)
    listener.start()
    try:
        assert _wait_for(lambda: listener.status == "heard")
        assert listener.is_alive()
    finally:
        listener.stop()
    listener.join(3.0)


# --------------------------------------------------------------------------- #
# the "I heard you" chime
# --------------------------------------------------------------------------- #

def test_chime_is_a_short_valid_wav():
    with wave.open(BytesIO(wake._chime_wav()), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == voice._SAMPLE_RATE
        assert 0 < wav.getnframes() < voice._SAMPLE_RATE
