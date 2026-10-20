"""Tests for sounddevice device selection and the Windows-safe reader.

The real PortAudio library and microphone are not available in CI, so these use
a small fake ``sounddevice`` module. They cover the Windows failure modes:
``select`` on a pipe, invalid ``device="default"``, and a missed or wrong input.
"""

import time

import pytest

import config
import main
import voice
from platform_adapters import _sd


DEVICES = [
    {
        "name": "Speakers",
        "max_input_channels": 0,
        "max_output_channels": 2,
        "hostapi": 0,
        "default_samplerate": 48000.0,
    },
    {
        "name": "Microphone (USB Audio)",
        "max_input_channels": 2,
        "max_output_channels": 0,
        "hostapi": 1,
        "default_samplerate": 48000.0,
    },
    {
        "name": "Headset Microphone",
        "max_input_channels": 1,
        "max_output_channels": 0,
        "hostapi": 1,
        "default_samplerate": 44100.0,
    },
]


class FakeStream:
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self, frames):
        time.sleep(0.002)
        return b"\x00\x00" * frames, False


class FakeSD:
    """Minimal stand-in for the parts of sounddevice that Jarvis uses."""

    def __init__(self, devices=None, default=(1, 3), fail_open=False):
        self.devices = list(DEVICES if devices is None else devices)
        self.default = type("Default", (), {"device": default})()
        self.fail_open = fail_open
        self.open_calls = []

    def query_devices(self, device=None, kind=None):
        if device is None and kind is None:
            return self.devices
        if kind == "input":
            index = self.default.device[0]
            return self.devices[index]
        return self.devices[device]

    def query_hostapis(self):
        return [{"name": "MME"}, {"name": "Windows WASAPI"}]

    def RawInputStream(self, **kwargs):
        self.open_calls.append(kwargs)
        if self.fail_open:
            raise RuntimeError("device busy")
        return FakeStream()


@pytest.fixture
def no_configured_device(monkeypatch):
    monkeypatch.setattr(_sd, "_configured_device", lambda: None)


def test_list_input_devices_filters_outputs_and_marks_default():
    devices = _sd.list_input_devices(sd=FakeSD())

    assert [item["index"] for item in devices] == [1, 2]
    assert [item["name"] for item in devices] == [
        "Microphone (USB Audio)", "Headset Microphone",
    ]
    assert [item["default"] for item in devices] == [True, False]
    assert devices[0]["hostapi_name"] == "Windows WASAPI"
    assert devices[0]["channels"] == 2


def test_resolve_numeric_index_and_name_substring(no_configured_device):
    sd = FakeSD()

    assert _sd.resolve_input_device("0", sd=sd) == 1  # 0 is output -> default
    assert _sd.resolve_input_device("2", sd=sd) == 2
    assert _sd.resolve_input_device("usb", sd=sd) == 1
    assert _sd.resolve_input_device("HEAD", sd=sd) == 2


def test_resolve_invalid_index_and_name_fall_back(no_configured_device, capsys):
    sd = FakeSD()

    assert _sd.resolve_input_device("99", sd=sd) == 1
    assert _sd.resolve_input_device("not-a-mic", sd=sd) == 1

    out = capsys.readouterr().out
    assert "not an input device" in out
    assert "No microphone name contains" in out


def test_resolve_prefers_environment_override(monkeypatch, no_configured_device):
    sd = FakeSD()
    monkeypatch.setenv("JARVIS_MIC_DEVICE", "2")
    monkeypatch.setattr(_sd, "_configured_device",
                        lambda: "2")

    assert _sd.resolve_input_device(sd=sd) == 2


def test_recorder_opens_the_resolved_device(monkeypatch):
    sd = FakeSD()
    monkeypatch.setattr(_sd, "require_sounddevice", lambda: sd)

    recorder = _sd.SoundDeviceRecorder("Headset")
    deadline = time.monotonic() + 2
    while not sd.open_calls and time.monotonic() < deadline:
        time.sleep(0.01)
    recorder.terminate()
    recorder.wait(timeout=2)

    assert sd.open_calls, "RawInputStream was never opened"
    assert sd.open_calls[0]["device"] == 2
    assert sd.open_calls[0]["samplerate"] == _sd.SAMPLE_RATE


def test_recorder_logs_a_clear_warning_when_open_fails(monkeypatch, capsys):
    sd = FakeSD(fail_open=True)
    monkeypatch.setattr(_sd, "require_sounddevice", lambda: sd)

    recorder = _sd.SoundDeviceRecorder()
    recorder.wait(timeout=2)
    out = capsys.readouterr().out

    assert "Failed to open microphone" in out
    assert "JARVIS_MIC_DEVICE" in out
    assert recorder.error is not None


def test_chunk_reader_times_out_without_data():
    reader = _sd._ChunkReader()
    started = time.monotonic()

    assert reader.read_chunk(time.monotonic() + 0.1, 3200) == b""
    assert time.monotonic() - started < 1.0


def test_voice_read_path_skips_select_for_sounddevice(monkeypatch):
    """Windows select() cannot wait on pipes; the recorder path must not call it."""
    class Reader:
        def read_chunk(self, deadline, size):
            return b"\x00" * size

    def fail(*args, **kwargs):
        pytest.fail("select() must not be used with the sounddevice reader")

    monkeypatch.setattr(voice.select, "select", fail)
    assert len(voice._read_chunk(Reader(), None, time.monotonic() + 1)) == \
        voice._CHUNK_BYTES


def test_list_mics_cli_prints_devices(monkeypatch, capsys):
    monkeypatch.setattr(_sd, "list_input_devices", lambda: [
        {
            "index": 1,
            "name": "Microphone (USB Audio)",
            "hostapi": 1,
            "hostapi_name": "Windows WASAPI",
            "channels": 2,
            "default_samplerate": 48000.0,
            "default": True,
        },
    ])
    monkeypatch.setattr(main.sys, "argv", ["main.py", "--list-mics"])

    assert main.main() == 0
    out = capsys.readouterr().out
    assert "[1] Microphone (USB Audio) (default)" in out
    assert "Windows WASAPI" in out
    assert "JARVIS_MIC_DEVICE" in out


def test_list_mics_cli_reports_missing_capture(monkeypatch, capsys):
    def boom():
        raise RuntimeError("sounddevice is not installed")

    monkeypatch.setattr(_sd, "list_input_devices", boom)
    monkeypatch.setattr(main.sys, "argv", ["main.py", "--list-mics"])

    assert main.main() == 1
    assert "Could not list microphones" in capsys.readouterr().err


def test_config_exposes_the_mic_override(monkeypatch):
    # config is imported once, so this checks the documented public constant is
    # present and usable by the adapters rather than re-importing the module.
    assert hasattr(config, "MIC_DEVICE")
    assert voice._mic_device()
