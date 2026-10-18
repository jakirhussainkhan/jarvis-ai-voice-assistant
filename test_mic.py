"""Standalone microphone diagnostic for Jarvis.

Run this on the machine with the microphone to isolate capture problems,
independently of the GUI / agent / TTS.

Usage:
    ./venv/bin/python test_mic.py            # list mics + record 3s + play + recognise
    ./venv/bin/python test_mic.py --list     # only list microphones
    ./venv/bin/python test_mic.py --device 5 # force a specific device index
    ./venv/bin/python test_mic.py --seconds 4
"""

import argparse
import ctypes
import os
import re
import sys
import wave
from pathlib import Path

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pyaudio
import speech_recognition as sr


# Mute ALSA/JACK chatter so the diagnostic output stays readable. Mirrors the
# suppression in voice.py, but keeps this script standalone. The handler
# objects MUST stay alive at module scope, otherwise C calls a freed pointer.
_ALSA_HANDLER = ctypes.CFUNCTYPE(
    None, ctypes.c_char_p, ctypes.c_int,
    ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
)
_alsa_handler = _ALSA_HANDLER(lambda *args: None)
try:
    ctypes.cdll.LoadLibrary("libasound.so").snd_lib_error_set_handler(_alsa_handler)
except OSError:
    pass

_JACK_HANDLER = ctypes.CFUNCTYPE(None, ctypes.c_char_p)
_jack_handler = _JACK_HANDLER(lambda msg: None)
try:
    ctypes.cdll.LoadLibrary("libjack.so.0").jack_set_error_function(_jack_handler)
except OSError:
    pass



try:
    from config import STT_LANG
except Exception:  # pragma: no cover - config should exist, but stay standalone
    STT_LANG = "en-US"

HERE = Path(__file__).resolve().parent
WAV_PATH = HERE / "test_mic.wav"
VOICE_PY = HERE / "voice.py"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def list_microphones():
    """Requirement 2: print every device the way speech_recognition sees them."""
    names = sr.Microphone.list_microphone_names()
    print(f"\n🎙️  speech_recognition sees {len(names)} device(s):")
    for i, mic in enumerate(names):
        print(f"  {i:>3}  {mic}")
    return names


def default_input_index():
    """pyaudio's default input device index, or None."""
    pa = pyaudio.PyAudio()
    try:
        return pa.get_default_input_device_info()["index"]
    except Exception:
        return None
    finally:
        pa.terminate()


def preferred_input_index():
    """Whatever voice._pick_input_device() would choose (system default/PipeWire)."""
    try:
        from voice import _pick_input_device
        return _pick_input_device()
    except Exception:
        return None


def device_name(index):
    if index is None:
        return "<system default>"
    try:
        names = sr.Microphone.list_microphone_names()
        return f"{index} ({names[index]})" if 0 <= index < len(names) else str(index)
    except Exception:
        return str(index)


def record_wav(index, seconds):
    """Requirement 3: capture `seconds` of audio to test_mic.wav."""
    rec = sr.Recognizer()
    rec.energy_threshold = 300
    rec.dynamic_energy_threshold = False

    print(f"\n🔴 Recording {seconds}s from device {device_name(index)} ...")
    with sr.Microphone(device_index=index) as src:
        # Deliberately NO adjust_for_ambient_noise() here: on this hardware it
        # pushed energy_threshold to ~30000 and blocked normal speech. We keep a
        # low fixed floor instead (matching voice.py). rec.record() ignores the
        # threshold anyway, but the settings stay in sync with voice.py.
        audio = rec.record(src, duration=seconds)

    with open(WAV_PATH, "wb") as fh:
        fh.write(audio.get_wav_data())
    print(f"   saved -> {WAV_PATH}  ({WAV_PATH.stat().st_size} bytes)")
    _report_level(audio)
    return rec, audio


def _report_level(audio):
    """Quick RMS so a silent capture is obvious without listening."""
    raw = audio.get_raw_data(convert_rate=16000, convert_width=2)
    samples = memoryview(raw).cast("h")
    if not samples:
        print("   ⚠️  captured 0 samples")
        return
    total = sum(s * s for s in samples)
    rms = (total / len(samples)) ** 0.5
    peak = max(abs(s) for s in samples)
    verdict = "SILENT (nothing captured!)" if peak < 500 else "audio present"
    print(f"   RMS {rms:.0f} · peak {peak} -> {verdict}")


def play_wav(path):
    """Requirement 4: play the recording back through pyaudio."""
    print(f"\n🔊 Playing back {path.name} ...")
    pa = pyaudio.PyAudio()
    try:
        with wave.open(str(path), "rb") as wf:
            stream = pa.open(
                format=pa.get_format_from_width(wf.getsampwidth()),
                channels=wf.getnchannels(),
                rate=wf.getframerate(),
                output=True,
            )
            try:
                data = wf.readframes(1024)
                while data:
                    stream.write(data)
                    data = wf.readframes(1024)
            finally:
                stream.stop_stream()
                stream.close()
    finally:
        pa.terminate()
    print("   playback done")


def recognise(rec, audio):
    """Requirement 5: show the recognised text, or why nothing was heard."""
    print("\n📝 Recognising ...")
    try:
        text = rec.recognize_google(audio, language=STT_LANG)
        print(f"   recognised ({STT_LANG}): {text!r}")
        return text
    except sr.UnknownValueError:
        print("   ❌ UnknownValueError: audio captured but Google heard no words")
        print("      (mic too quiet, wrong device, or silence)")
    except sr.RequestError as exc:
        print(f"   ❌ RequestError: could not reach Google STT: {exc}")
    except Exception as exc:
        print(f"   ❌ {type(exc).__name__}: {exc}")
    return None


# --------------------------------------------------------------------------- #
# requirement 6 - energy_threshold check
# --------------------------------------------------------------------------- #

def check_energy_threshold():
    """Read the threshold voice.py ships with and advise if it's too high."""
    print("\n🔧 Checking energy_threshold in voice.py ...")
    try:
        src = VOICE_PY.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"   could not read voice.py: {exc}")
        return

    match = (re.search(r"_ENERGY_THRESHOLD\s*=\s*([0-9.]+)", src)
             or re.search(r"energy_threshold\s*=\s*([0-9.]+)", src))
    dynamic = re.search(r"dynamic_energy_threshold\s*=\s*(True|False)", src)
    if not match:
        print("   no static energy_threshold found in voice.py")
    else:
        value = float(match.group(1))
        print(f"   voice.py sets energy_threshold = {value:.0f} "
              f"(dynamic = {dynamic.group(1) if dynamic else '?'})")
        if value > 4000:
            print("   ⚠️  VERY HIGH: normal speech may be ignored. Lower it.")
        elif value > 1000:
            print("   ⚠️  high-ish for a quiet room; watch the RMS reading above.")
        else:
            print("   ✅ reasonable starting value.")

    print(
        "\n   Recommended capture settings (NO ambient calibration):\n"
        "     rec = sr.Recognizer()\n"
        "     rec.energy_threshold = 300            # low fixed floor\n"
        "     rec.dynamic_energy_threshold = False  # fixed gate, no drift\n"
        "     with sr.Microphone(device_index=idx) as src:\n"
        "         <discard the stream-open warm-up transient>\n"
        "         audio = rec.listen(src, timeout=4, phrase_time_limit=8)\n"
        "\n   Notes:\n"
        "   - Do NOT call adjust_for_ambient_noise() on this machine: it pushed\n"
        "     the threshold to ~30000 and ignored normal speech.\n"
        "   - Use the system default input (\"default\" / PipeWire). Direct ALSA\n"
        "     access to the onboard codec (hw:1,0) returns a loud clipped signal\n"
        "     and makes every listen() time out.\n"
        "   - If the noise floor is high / audio clips, lower the capture gain\n"
        "     (e.g. `amixer -c 1 sset Capture 50%`); 100% (30 dB) clips here.\n"
        "   - Override the device with JARVIS_MIC_INDEX=<n> in the environment."
    )


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main():
    parser = argparse.ArgumentParser(description="Jarvis microphone diagnostic")
    parser.add_argument("--device", type=int, default=None,
                        help="force a device index (default: the device voice.py picks)")
    parser.add_argument("--seconds", type=float, default=3.0,
                        help="seconds to record (default: 3)")
    parser.add_argument("--list", action="store_true",
                        help="only list microphones and exit")
    parser.add_argument("--no-play", action="store_true",
                        help="skip pyaudio playback")
    args = parser.parse_args()

    print("=" * 60)
    print(" Jarvis microphone test")
    print("=" * 60)

    names = list_microphones()
    if not names:
        print("❌ No microphones reported by PortAudio. Check PulseAudio/PipeWire "
              "and that the user is in the 'audio' group.")
        return 1

    default_idx = default_input_index()
    preferred_idx = preferred_input_index()
    # Test the SAME device voice.py would pick (falling back to the system
    # default if voice.py couldn't choose one).
    chosen = args.device if args.device is not None else (
        preferred_idx if preferred_idx is not None else default_idx)

    print("\nDevice selection:")
    print(f"  system default input : {device_name(default_idx)}")
    print(f"  voice.py would pick   : {device_name(preferred_idx)}")
    print(f"  using for this test   : {device_name(chosen)}")
    print(f"  chosen device index   : {chosen}")

    if args.list:
        check_energy_threshold()
        return 0

    try:
        rec, audio = record_wav(chosen, args.seconds)
    except Exception as exc:
        print(f"\n❌ Recording failed on device {device_name(chosen)}: "
              f"{type(exc).__name__}: {exc}")
        print("   Try another index with --device N (list printed above).")
        check_energy_threshold()
        return 1

    if not args.no_play:
        try:
            play_wav(WAV_PATH)
        except Exception as exc:
            print(f"   ⚠️  playback failed: {type(exc).__name__}: {exc}")

    recognise(rec, audio)
    check_energy_threshold()
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
