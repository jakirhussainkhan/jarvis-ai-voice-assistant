"""Tests for the speech-side helpers: level sink, TTS envelope and interruption.

The microphone and the real audio device cannot be driven from a test, so the
sounddevice/pygame outputs and the ffmpeg decode are stubbed. What is covered
here is the logic that used to be easy to get wrong: the level sink never
raising, the envelope degrading to [] when ffmpeg is missing, bounded queue
backpressure, and stop_speaking() actually cutting playback short.
"""

import array
import queue
import shutil
import sys
import threading
import time
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
    voice._set_active_tts_playback(None, None)
    yield
    voice.set_level_sink(None)
    voice.set_mic_muted(False)
    voice._stop_speaking.clear()
    voice._stop_listening.clear()
    voice._set_active_tts_playback(None, None)


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


def test_mic_mute_only_stops_the_internal_capture(monkeypatch):
    """Regression: muting Jarvis must never mute the system microphone."""
    # There must be no OS-level mic-mute path at all (it lit the keyboard LED).
    assert not hasattr(voice, "_apply_source_mute")
    assert not hasattr(voice.Audio, "set_source_mute")

    calls = []
    monkeypatch.setattr(voice, "stop_listening",
                        lambda: calls.append("capture-stopped"))
    monkeypatch.setattr(voice, "pause_barge_in", lambda: calls.append("paused"))
    monkeypatch.setattr(voice, "resume_barge_in", lambda: calls.append("resumed"))

    assert voice.set_mic_muted(True) is True
    assert voice.set_mic_muted(False) is False
    # Only Jarvis's own capture is stopped; the hardware is never addressed.
    assert calls == ["capture-stopped", "paused", "resumed"]


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
    voice._play_tts_file("hello there")

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
    voice._play_tts_file("a long reply")

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

    voice._play_tts_file("next reply")

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
    voice._play_tts_file("a reply nobody will hear")
    voice.set_level_sink(None)

    assert music.played is False, "a Stop during synthesis must cancel playback"
    assert levels and levels[-1] == 0.0


# --------------------------------------------------------------------------- #
# streaming TTS (edge-tts -> ffmpeg -> queued pygame Sounds)
# --------------------------------------------------------------------------- #

class _ChunkSink:
    """A write sink that keeps its bytes even after close()."""

    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, chunk):
        self.data += chunk

    def flush(self):
        pass

    def close(self):
        self.closed = True


def _audio_event(data):
    return {"type": "audio", "data": data}


def test_streaming_synthesis_forwards_only_audio_chunks(monkeypatch):
    class FakeCommunicate:
        def __init__(self, text, voice=None, **kwargs):
            self.text = text

        async def stream(self):
            yield _audio_event(b"aa")
            yield {"type": "SentenceBoundary", "text": "ignored"}
            yield _audio_event(b"bb")

    monkeypatch.setattr(voice.edge_tts, "Communicate", FakeCommunicate)
    sink = _ChunkSink()
    voice._synthesize_to_decoder("hello", sink, threading.Event())

    assert sink.data == b"aabb"
    assert sink.closed is True


def test_streaming_synthesis_stops_after_interrupt(monkeypatch):
    stop = threading.Event()

    class FakeCommunicate:
        def __init__(self, text, voice=None, **kwargs):
            self.text = text

        async def stream(self):
            yield _audio_event(b"first")
            stop.set()                    # user pressed Stop / barge-in
            yield _audio_event(b"second")

    monkeypatch.setattr(voice.edge_tts, "Communicate", FakeCommunicate)
    sink = _ChunkSink()
    voice._synthesize_to_decoder("hello", sink, stop)

    assert sink.data == b"first"


class _FakeSound:
    def __init__(self, buffer=None):
        self.buffer = buffer


class _FakeChannel:
    def __init__(self, on_queue=None):
        self.queued = []
        self.stopped = False
        self.busy = False
        self.plays = 0
        self.queues = 0
        self._busy_checks = 0
        self._on_queue = on_queue

    def _record(self, sound):
        self.queued.append(sound)
        if self._on_queue is not None:
            self._on_queue(len(self.queued))

    def play(self, sound):
        self.plays += 1
        self.busy = True
        self._busy_checks = 0
        self._record(sound)

    def queue(self, sound):
        self.queues += 1
        self._record(sound)

    def get_busy(self):
        # One successful "still playing" check is enough to exercise the queue
        # path, then report completion so the post-loop drain can finish.
        if not self.busy:
            return False
        if self._busy_checks:
            self.busy = False
            return False
        self._busy_checks += 1
        return True

    def stop(self):
        self.stopped = True
        self.busy = False


class _FakeRawStream:
    """Minimal stand-in for sounddevice.RawOutputStream."""

    def __init__(self, on_write=None):
        self.written = []
        self.started = False
        self.stopped = False
        self.aborted = False
        self.closed = False
        self._on_write = on_write

    def start(self):
        self.started = True

    def write(self, data):
        self.written.append(data)
        if self._on_write is not None:
            self._on_write(len(self.written))

    def stop(self):
        self.stopped = True

    def abort(self):
        self.aborted = True

    def close(self):
        self.closed = True


class _FakeStdin:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    def write(self, _data):
        pass

    def flush(self):
        pass


class _FakeStdout:
    def __init__(self, blocks):
        self.blocks = list(blocks)

    def read(self, _size):
        return self.blocks.pop(0) if self.blocks else b""

    def close(self):
        pass


class _FakeProc:
    def __init__(self, blocks):
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(blocks)
        self.terminated = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def _fake_stream_pygame(channel):
    calls = {"pre_init": [], "init": []}
    mixer = types.SimpleNamespace(
        get_init=lambda: (voice._TTS_RATE, -16, voice._TTS_CHANNELS),
        pre_init=lambda **kwargs: calls["pre_init"].append(kwargs),
        init=lambda *args, **kwargs: calls["init"].append(kwargs),
        quit=lambda *a, **k: None,
        Channel=lambda _index: channel,
        Sound=lambda buffer=None: _FakeSound(buffer),
    )
    clock = types.SimpleNamespace(tick=lambda _fps: 0)
    return types.SimpleNamespace(
        mixer=mixer, time=types.SimpleNamespace(Clock=lambda: clock),
        calls=calls,
    )


def _pcm_block(amplitude=2000, size=None):
    size = voice._TTS_BLOCK_BYTES if size is None else size
    samples = array.array("h", [amplitude] * (size // 2))
    return samples.tobytes()


def test_open_sounddevice_stream_uses_raw_output(monkeypatch):
    calls = {}

    class FakeRawStream:
        def __init__(self, **kwargs):
            calls["kwargs"] = kwargs
            self.started = False

        def start(self):
            self.started = True

        def close(self):
            pass

    fake_sd = types.SimpleNamespace(RawOutputStream=FakeRawStream)
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    stream = voice._open_sounddevice_stream()

    assert isinstance(stream, FakeRawStream)
    assert stream.started is True
    assert calls["kwargs"] == {
        "samplerate": voice._TTS_RATE,
        "channels": voice._TTS_CHANNELS,
        "dtype": "int16",
        "blocksize": 0,
    }


def test_open_sounddevice_stream_returns_none_on_failure(monkeypatch):
    class BrokenRawStream:
        def __init__(self, **kwargs):
            raise RuntimeError("no audio device")

    fake_sd = types.SimpleNamespace(RawOutputStream=BrokenRawStream)
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    assert voice._open_sounddevice_stream() is None


def test_streaming_tts_queues_decoded_chunks(monkeypatch):
    proc = _FakeProc([
        _pcm_block(2000, voice._TTS_FALLBACK_BLOCK_BYTES),
        _pcm_block(2000, voice._TTS_FALLBACK_BLOCK_BYTES),
    ])
    channel = _FakeChannel()
    fake_pygame = _fake_stream_pygame(channel)
    monkeypatch.setattr(voice, "_start_tts_decoder", lambda: proc)
    monkeypatch.setattr(voice, "_open_sounddevice_stream", lambda: None)
    monkeypatch.setattr(
        voice, "_synthesize_to_decoder",
        lambda text, stdin, stop: stdin.close(),
    )
    monkeypatch.setattr(voice, "pygame", fake_pygame)

    levels = []
    voice.set_level_sink(levels.append)
    assert voice._play_tts_stream("hello") is True

    assert len(channel.queued) == 2
    assert channel.plays == 1          # first Sound starts the channel
    assert channel.queues == 1         # later Sounds are queued, not played
    assert all(sound.buffer for sound in channel.queued)
    assert any(level > 0 for level in levels)
    assert levels[-1] == 0.0
    assert proc.terminated is True
    assert channel.stopped is True
    # The fallback asks pygame for a larger buffer before initialising.
    assert fake_pygame.calls["pre_init"] == [{
        "frequency": voice._TTS_RATE,
        "size": -16,
        "channels": voice._TTS_CHANNELS,
        "buffer": voice._TTS_PYGAME_BUFFER,
    }]
    assert fake_pygame.calls["init"][-1]["buffer"] == voice._TTS_PYGAME_BUFFER


def test_streaming_tts_stops_after_the_first_chunk(monkeypatch):
    proc = _FakeProc([
        _pcm_block(size=voice._TTS_FALLBACK_BLOCK_BYTES),
        _pcm_block(size=voice._TTS_FALLBACK_BLOCK_BYTES),
        _pcm_block(size=voice._TTS_FALLBACK_BLOCK_BYTES),
    ])
    channel = _FakeChannel(
        on_queue=lambda count: voice.stop_speaking() if count == 1 else None
    )
    monkeypatch.setattr(voice, "_start_tts_decoder", lambda: proc)
    monkeypatch.setattr(voice, "_open_sounddevice_stream", lambda: None)
    monkeypatch.setattr(
        voice, "_synthesize_to_decoder",
        lambda text, stdin, stop: stdin.close(),
    )
    monkeypatch.setattr(voice, "pygame", _fake_stream_pygame(channel))

    assert voice._play_tts_stream("hello") is True

    assert len(channel.queued) == 1
    assert channel.stopped is True


def test_streaming_tts_uses_one_continuous_sounddevice_stream(monkeypatch):
    proc = _FakeProc([_pcm_block(2000), _pcm_block(2000)])
    raw_stream = _FakeRawStream()
    monkeypatch.setattr(voice, "_start_tts_decoder", lambda: proc)

    def open_stream():
        raw_stream.start()
        return raw_stream

    monkeypatch.setattr(voice, "_open_sounddevice_stream", open_stream)
    monkeypatch.setattr(
        voice, "_synthesize_to_decoder",
        lambda text, stdin, stop: stdin.close(),
    )

    levels = []
    voice.set_level_sink(levels.append)
    assert voice._play_tts_stream("hello") is True

    assert raw_stream.started is True
    assert len(raw_stream.written) == 2
    assert all(len(block) == voice._TTS_BLOCK_BYTES
               for block in raw_stream.written)
    assert raw_stream.stopped is True      # normal completion drains
    assert raw_stream.aborted is False
    assert raw_stream.closed is True
    assert any(level > 0 for level in levels)
    assert levels[-1] == 0.0
    assert proc.terminated is True


def test_streaming_tts_stop_aborts_sounddevice_stream(monkeypatch):
    proc = _FakeProc([_pcm_block(), _pcm_block(), _pcm_block()])
    raw_stream = _FakeRawStream(
        on_write=lambda count: voice.stop_speaking() if count == 1 else None
    )
    monkeypatch.setattr(voice, "_start_tts_decoder", lambda: proc)
    monkeypatch.setattr(voice, "_open_sounddevice_stream", lambda: raw_stream)
    monkeypatch.setattr(
        voice, "_synthesize_to_decoder",
        lambda text, stdin, stop: stdin.close(),
    )

    assert voice._play_tts_stream("hello") is True

    assert len(raw_stream.written) == 1
    assert raw_stream.aborted is True      # Stop cuts the device immediately
    assert raw_stream.stopped is False
    assert raw_stream.closed is True
    assert proc.terminated is True


def test_stop_speaking_aborts_active_stream_and_clears_queue():
    raw_stream = _FakeRawStream()
    blocks = queue.Queue()
    blocks.put(b"first")
    blocks.put(b"second")
    voice._set_active_tts_playback(raw_stream, blocks)

    voice.stop_speaking()

    assert raw_stream.aborted is True
    assert blocks.empty() is True


def test_decoded_pcm_reader_blocks_when_queue_is_full():
    blocks_in = [_pcm_block(), _pcm_block(), _pcm_block()]
    blocks = queue.Queue(maxsize=1)
    stop = threading.Event()
    reader = threading.Thread(
        target=voice._read_decoded_pcm,
        args=(_FakeStdout(blocks_in), blocks, stop, voice._TTS_BLOCK_BYTES),
        daemon=True,
    )
    reader.start()

    assert blocks.get(timeout=1) == blocks_in[0]
    deadline = time.monotonic() + 1.0      # let the reader hit the full queue
    while blocks.qsize() == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert reader.is_alive() is True
    assert blocks.qsize() == 1             # the second block is waiting

    assert blocks.get(timeout=1) == blocks_in[1]
    assert blocks.get(timeout=1) == blocks_in[2]
    assert blocks.get(timeout=1) is None
    reader.join(timeout=1)
    assert reader.is_alive() is False


def test_play_tts_uses_streaming_when_available(monkeypatch):
    calls = []
    monkeypatch.setattr(voice, "_TTS_STREAM_ENABLED", True)
    monkeypatch.setattr(
        voice, "_play_tts_stream",
        lambda text: calls.append(("stream", text)) or True,
    )
    monkeypatch.setattr(
        voice, "_play_tts_file",
        lambda text: calls.append(("file", text)),
    )

    voice._play_tts("hello")

    assert calls == [("stream", "hello")]


def test_play_tts_falls_back_when_ffmpeg_is_missing(monkeypatch):
    calls = []
    monkeypatch.setattr(voice, "_TTS_STREAM_ENABLED", True)
    monkeypatch.setattr(voice, "_play_tts_stream", lambda text: False)
    monkeypatch.setattr(voice, "_play_tts_file", lambda text: calls.append(text))

    voice._play_tts("hello")

    assert calls == ["hello"]


def test_play_tts_does_not_replay_after_streaming_error(monkeypatch):
    calls = []
    monkeypatch.setattr(voice, "_TTS_STREAM_ENABLED", True)

    def boom(_text):
        raise RuntimeError("audio device died")

    monkeypatch.setattr(voice, "_play_tts_stream", boom)
    monkeypatch.setattr(voice, "_play_tts_file", lambda text: calls.append(text))

    voice._play_tts("hello")

    assert calls == []                  # never repeat words already heard
