"""Tests for the speech-side helpers: level sink, TTS envelope and interruption.

The microphone and the real audio device cannot be driven from a test, so the
sounddevice output and the ffmpeg decode are stubbed. What is covered here is
the logic that used to be easy to get wrong: the level sink never raising, the
envelope degrading to [] when ffmpeg is missing, bounded queue backpressure, and
stop_speaking() actually cutting playback short.
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


# --------------------------------------------------------------------------- #
# streaming TTS (edge-tts -> ffmpeg -> continuous sounddevice stream)
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
        self.killed = False
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


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
    }


def test_open_sounddevice_stream_returns_none_on_failure(monkeypatch):
    class BrokenRawStream:
        def __init__(self, **kwargs):
            raise RuntimeError("no audio device")

    fake_sd = types.SimpleNamespace(RawOutputStream=BrokenRawStream)
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    assert voice._open_sounddevice_stream() is None


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
    proc = _FakeProc([])
    blocks = queue.Queue()
    blocks.put(b"first")
    blocks.put(b"second")
    voice._set_active_tts_playback(raw_stream, blocks, proc)

    voice.stop_speaking()

    assert raw_stream.aborted is True
    assert proc.killed is True, "Stop must kill the ffmpeg decoder too"
    assert blocks.empty() is True


def test_streaming_tts_kills_decoder_when_sounddevice_is_unavailable(monkeypatch):
    proc = _FakeProc([_pcm_block()])
    monkeypatch.setattr(voice, "_start_tts_decoder", lambda: proc)
    monkeypatch.setattr(voice, "_open_sounddevice_stream", lambda: None)

    assert voice._play_tts_stream("hello") is False

    assert proc.terminated is True
    assert proc.stdin.closed is True
    assert proc.stdout is not None


def test_ffmpeg_binary_prefers_the_bundled_copy(monkeypatch, tmp_path):
    bundled = tmp_path / "ffmpeg.exe"
    bundled.write_bytes(b"")
    monkeypatch.setattr(voice.sys, "frozen", True, raising=False)
    monkeypatch.setattr(voice.sys, "_MEIPASS", str(tmp_path), raising=False)

    assert voice._ffmpeg_binary() == str(bundled)


def test_ffmpeg_binary_falls_back_to_path_in_a_source_checkout(monkeypatch):
    monkeypatch.delattr(voice.sys, "frozen", raising=False)

    assert voice._ffmpeg_binary() == "ffmpeg"


def test_start_tts_decoder_uses_the_resolved_ffmpeg(monkeypatch):
    seen = {}
    proc = object()

    def fake_popen(args, **_kwargs):
        seen["args"] = args
        return proc

    monkeypatch.setattr(voice, "_ffmpeg_binary",
                        lambda: r"C:\bundle\ffmpeg.exe")
    monkeypatch.setattr(voice.subprocess, "Popen", fake_popen)

    assert voice._start_tts_decoder() is proc
    assert seen["args"][0] == r"C:\bundle\ffmpeg.exe"


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

    assert voice._play_tts("hello") is True

    assert calls == [("stream", "hello")]


def test_play_tts_returns_false_when_streaming_is_unavailable(monkeypatch):
    """No ffmpeg or no sounddevice: speak() prints the reply instead."""
    monkeypatch.setattr(voice, "_TTS_STREAM_ENABLED", True)
    monkeypatch.setattr(voice, "_play_tts_stream", lambda text: False)

    assert voice._play_tts("hello") is False


def test_play_tts_does_not_replay_after_streaming_error(monkeypatch):
    monkeypatch.setattr(voice, "_TTS_STREAM_ENABLED", True)

    def boom(_text):
        raise RuntimeError("audio device died")

    monkeypatch.setattr(voice, "_play_tts_stream", boom)

    assert voice._play_tts("hello") is False


def test_play_tts_is_disabled_by_the_environment_flag(monkeypatch):
    monkeypatch.setattr(voice, "_TTS_STREAM_ENABLED", False)
    monkeypatch.setattr(
        voice, "_play_tts_stream",
        lambda text: pytest.fail("streaming must stay off"),
    )

    assert voice._play_tts("hello") is False
