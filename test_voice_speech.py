"""Tests for the speech-side helpers: level sink, TTS envelope and interruption.

The microphone and the real audio device cannot be driven from a test, so the
pygame mixer and the ffmpeg decode are stubbed. What is covered here is the
logic that used to be easy to get wrong: the level sink never raising, the
envelope degrading to [] when ffmpeg is missing, and stop_speaking() actually
cutting playback short.
"""

import shutil
import types
import wave

import pytest

import voice


@pytest.fixture(autouse=True)
def _reset_voice_globals():
    """Keep the module-level mute/sink/stop state from leaking between tests."""
    voice.set_level_sink(None)
    voice.set_mic_muted(False)
    voice._stop_speaking.clear()
    voice._stop_listening.clear()
    yield
    voice.set_level_sink(None)
    voice.set_mic_muted(False)
    voice._stop_speaking.clear()
    voice._stop_listening.clear()


# --------------------------------------------------------------------------- #
# mic mute
# --------------------------------------------------------------------------- #

def test_mic_mute_toggles_and_resets():
    assert voice.is_mic_muted() is False
    assert voice.toggle_mic_mute() is True
    assert voice.is_mic_muted() is True
    assert voice.toggle_mic_mute() is False
    assert voice.set_mic_muted(1) is True          # coerced to bool
    assert voice.is_mic_muted() is True


def test_emit_value_clamps_and_swallows_errors():
    seen = []
    voice.set_level_sink(seen.append)
    voice._emit_value(2.0)
    voice._emit_value(-1.0)
    voice._emit_value("not a number")              # float() raises -> swallowed
    assert seen == [1.0, 0.0]

    def boom(_value):
        raise RuntimeError("sink is broken")

    voice.set_level_sink(boom)
    voice._emit_value(0.5)                          # must not raise


def test_emit_level_normalises_rms():
    seen = []
    voice.set_level_sink(seen.append)
    voice._emit_level(0)
    voice._emit_level(voice._LEVEL_CEILING)
    voice._emit_level(voice._LEVEL_CEILING * 999)
    assert seen[0] == 0.0
    assert seen[1] == pytest.approx(1.0)
    assert seen[2] == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# TTS loudness envelope
# --------------------------------------------------------------------------- #

class _FakeMusic:
    def __init__(self):
        self.played = False
        self.stopped = False
        self.positions = [0]
        self.busy_calls = 0
        self.busy = True

    def load(self, _path):
        pass

    def play(self):
        self.played = True

    def get_busy(self):
        self.busy_calls += 1
        return self.busy

    def unload(self):
        pass

    def stop(self):
        self.stopped = True
        self.busy = False

    def get_pos(self):
        return self.positions[0]

    def quit(self):
        pass


def _fake_pygame(music, on_tick=None):
    def tick(_fps):
        if on_tick is not None:
            on_tick()
        return 0

    clock = types.SimpleNamespace(tick=tick)
    mixer = types.SimpleNamespace(
        init=lambda *a, **k: None,
        quit=lambda *a, **k: None,
        music=music,
    )
    return types.SimpleNamespace(
        mixer=mixer, time=types.SimpleNamespace(Clock=lambda: clock)
    )


def _patch_tts(monkeypatch, music, envelope, on_tick=None):
    monkeypatch.setattr(voice, "pygame", _fake_pygame(music, on_tick=on_tick))

    def run(coro):
        coro.close()                          # avoid "never awaited" churn
        return None

    monkeypatch.setattr(voice.asyncio, "run", run)
    monkeypatch.setattr(voice, "_tts_envelope", lambda path, hop=0.02: envelope)


def test_tts_envelope_reads_a_real_tone(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    path = tmp_path / "tone.wav"
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 4000)          # 0.25 s silence
        import array
        wf.writeframes(array.array("h", [4000, -4000] * 4000).tobytes())

    envelope = voice._tts_envelope(str(path))

    assert envelope, "a real tone should produce an envelope"
    assert all(0.0 <= level <= 1.0 for level in envelope)
    assert max(envelope) == pytest.approx(1.0)


def test_tts_envelope_is_empty_when_ffmpeg_missing(monkeypatch):
    def boom(*_a, **_k):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(voice.subprocess, "Popen", boom)
    assert voice._tts_envelope("/does/not/exist.mp3") == []


def test_playback_seconds_prefers_get_pos(monkeypatch):
    music = _FakeMusic()
    music.positions[0] = 1500                       # 1.5 s
    monkeypatch.setattr(voice, "pygame", _fake_pygame(music))
    assert voice._playback_seconds(0.0) == pytest.approx(1.5)


def test_play_tts_completes_normally(monkeypatch):
    music = _FakeMusic()
    calls = {"n": 0}

    def busy():
        calls["n"] += 1
        return calls["n"] <= 1                      # one pass, then done

    music.get_busy = busy
    _patch_tts(monkeypatch, music, envelope=[0.2, 0.9])

    levels = []
    voice.set_level_sink(levels.append)
    voice._play_tts("hello there")

    assert music.played is True
    assert music.stopped is False
    assert levels and levels[-1] == 0.0             # ends quiet


def test_play_tts_is_cut_short_by_stop_speaking(monkeypatch):
    music = _FakeMusic()
    # The clock tick sets the stop flag once, so the next loop pass breaks.
    _patch_tts(monkeypatch, music, envelope=[0.5, 0.8],
               on_tick=voice.stop_speaking)

    levels = []
    voice.set_level_sink(levels.append)
    voice._play_tts("a long reply")

    assert music.played is True
    assert music.stopped is True, "stop_speaking() must cut playback"
    assert levels and levels[-1] == 0.0


def test_stop_speaking_clears_on_the_next_reply(monkeypatch):
    """A stale Stop must not silence the *next* reply."""
    voice.stop_speaking()                            # user pressed Stop
    music = _FakeMusic()
    calls = {"n": 0}

    def busy():
        calls["n"] += 1
        return calls["n"] <= 1

    music.get_busy = busy
    _patch_tts(monkeypatch, music, envelope=[0.3])

    voice._play_tts("next reply")

    assert music.stopped is False


def test_stop_during_synthesis_cancels_playback(monkeypatch):
    """Pressing Stop while the reply is still being synthesised must skip it."""
    music = _FakeMusic()

    def run(coro):
        coro.close()
        voice.stop_speaking()          # Stop arrives during synthesis
        return None

    monkeypatch.setattr(voice, "pygame", _fake_pygame(music))
    monkeypatch.setattr(voice.asyncio, "run", run)
    monkeypatch.setattr(voice, "_tts_envelope", lambda path, hop=0.02: [0.5])

    levels = []
    voice.set_level_sink(levels.append)
    voice._play_tts("a reply nobody will hear")
    voice.set_level_sink(None)

    assert music.played is False, "a Stop during synthesis must cancel playback"
    assert levels and levels[-1] == 0.0
