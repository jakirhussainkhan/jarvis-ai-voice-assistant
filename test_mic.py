"""Standalone microphone diagnostic for Jarvis.

Run this on the machine with the microphone to isolate capture problems,
independently of the GUI / agent / TTS. Capture now goes through ``arecord``
(the same path as ``voice.listen()``), so this script no longer needs PyAudio.

Usage:
    ./venv/bin/python test_mic.py            # list devices + record 3s + play + recognise
    ./venv/bin/python test_mic.py --list     # only list capture devices
    ./venv/bin/python test_mic.py --device plughw:1,0
    ./venv/bin/python test_mic.py --seconds 4
"""

import argparse
import os
import re
import subprocess
import sys
import wave
from pathlib import Path

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import speech_recognition as sr

import voice

try:
    from config import STT_LANG
except Exception:  # pragma: no cover - config should exist, but stay standalone
    STT_LANG = "en-US"

HERE = Path(__file__).resolve().parent
WAV_PATH = HERE / "test_mic.wav"
VOICE_PY = HERE / "voice.py"
SAMPLE_RATE = voice._SAMPLE_RATE
SAMPLE_WIDTH = voice._SAMPLE_WIDTH


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _run(cmd, **kwargs):
    """Run a command and return the CompletedProcess (text mode)."""
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _indent(text):
    return "\n".join("  " + line for line in text.splitlines())


def list_devices():
    """Print every capture device / PCM name arecord can see.

    Replaces the old ``sr.Microphone.list_microphone_names()`` (PyAudio) with
    native ALSA enumeration, matching the arecord capture path.
    """
    print("\n🎙️  arecord -l (hardware capture devices):")
    out = _run(["arecord", "-l"])
    body = (out.stdout or out.stderr or "<none>").strip()
    print(_indent(body))

    print("\n🎙️  arecord -L (PCM names usable with --device):")
    out = _run(["arecord", "-L"])
    pcm_names = [line for line in out.stdout.splitlines()
                 if line and not line[0].isspace()]
    for name in pcm_names:
        print(f"  {name}")
    if not pcm_names:
        print("  <none>")
    return pcm_names


def preferred_device():
    """Whatever voice.listen() would capture from (default: ``default``)."""
    return voice._mic_device()


def record_wav(device, seconds):
    """Capture `seconds` of audio to test_mic.wav using arecord.

    Uses the same command builder as voice.listen() so the diagnostic and the
    real capture path cannot drift apart.
    """
    cmd = voice.arecord_command(device, seconds=seconds, output=WAV_PATH, fmt="wav")
    print(f"\n🔴 Recording {seconds}s from {device} ...")
    print(f"   $ {' '.join(cmd)}")
    proc = _run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(
            (proc.stderr or proc.stdout or f"arecord exited {proc.returncode}").strip()
        )
    if not WAV_PATH.exists() or WAV_PATH.stat().st_size == 0:
        raise RuntimeError("arecord reported success but produced no output")

    with wave.open(str(WAV_PATH), "rb") as wf:
        rate, width = wf.getframerate(), wf.getsampwidth()
        if rate != SAMPLE_RATE or width != SAMPLE_WIDTH:
            raise RuntimeError(
                f"unexpected WAV format: rate={rate} width={width}; "
                f"expected rate={SAMPLE_RATE} width={SAMPLE_WIDTH}"
            )
        data = wf.readframes(wf.getnframes())

    print(f"   saved -> {WAV_PATH}  ({WAV_PATH.stat().st_size} bytes)")
    _report_level(data)
    return data


def _report_level(raw):
    """Quick RMS so a silent capture is obvious without listening."""
    audio = sr.AudioData(raw, SAMPLE_RATE, SAMPLE_WIDTH)
    samples = memoryview(audio.get_raw_data()).cast("h")
    if not samples:
        print("   ⚠️  captured 0 samples")
        return
    total = sum(s * s for s in samples)
    rms = (total / len(samples)) ** 0.5
    peak = max(abs(s) for s in samples)
    verdict = "SILENT (nothing captured!)" if peak < 500 else "audio present"
    print(f"   RMS {rms:.0f} · peak {peak} -> {verdict}")


def play_wav(path):
    """Play the recording back through ALSA (aplay) instead of PyAudio."""
    print(f"\n🔊 Playing back {path.name} ...")
    proc = _run(["aplay", "-q", str(path)])
    if proc.returncode != 0:
        raise RuntimeError(
            (proc.stderr or proc.stdout or f"aplay exited {proc.returncode}").strip()
        )
    print("   playback done")


def recognise(data):
    """Show the recognised text, or why nothing was heard."""
    print("\n📝 Recognising ...")
    audio = sr.AudioData(data, SAMPLE_RATE, SAMPLE_WIDTH)
    rec = sr.Recognizer()
    rec.operation_timeout = 8
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
# requirement 6 - capture settings check
# --------------------------------------------------------------------------- #

def check_energy_threshold():
    """Read the RMS gate voice.py ships with and advise if it's too high."""
    print("\n🔧 Checking capture settings in voice.py ...")
    try:
        src = VOICE_PY.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"   could not read voice.py: {exc}")
        return

    match = (re.search(r"_ENERGY_THRESHOLD\s*=\s*([0-9.]+)", src)
             or re.search(r"energy_threshold\s*=\s*([0-9.]+)", src))
    if not match:
        print("   no RMS gate found in voice.py")
    else:
        value = float(match.group(1))
        print(f"   voice.py gates speech at RMS > {value:.0f}")
        if value > 4000:
            print("   ⚠️  VERY HIGH: normal speech may be ignored. Lower it.")
        elif value > 1000:
            print("   ⚠️  high-ish for a quiet room; watch the RMS reading above.")
        else:
            print("   ✅ reasonable starting value.")

    print(
        "\n   Capture path (same as voice.listen()):\n"
        "     arecord -D <device> -f S16_LE -r 16000 -c 1\n"
        "\n   Notes:\n"
        "   - listen() reads 0.1 s chunks, RMS-gates at _ENERGY_THRESHOLD,\n"
        "     starts on speech, stops after ~1 s of silence (max phrase 8 s).\n"
        "   - It waits up to 4 s for speech to start; no ambient calibration.\n"
        "   - PyAudio/PortAudio is NOT used for capture: it never saw this\n"
        "     machine's mic (it always timed out), while arecord works.\n"
        "   - The device defaults to \"default\" (system/PipeWire input).\n"
        "     Override with JARVIS_MIC_DEVICE=<name from `arecord -L`>.\n"
        "   - If the noise floor sits near/above the gate, lower the capture\n"
        "     gain (e.g. `amixer -c 1 sset Capture 40%`); 100% clips here."
    )


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main():
    parser = argparse.ArgumentParser(description="Jarvis microphone diagnostic")
    parser.add_argument("--device", default=None,
                        help="ALSA PCM name to record from "
                             "(default: the device voice.py picks)")
    parser.add_argument("--seconds", type=float, default=3.0,
                        help="seconds to record (default: 3)")
    parser.add_argument("--list", action="store_true",
                        help="only list capture devices and exit")
    parser.add_argument("--no-play", action="store_true",
                        help="skip aplay playback")
    args = parser.parse_args()

    print("=" * 60)
    print(" Jarvis microphone test (arecord)")
    print("=" * 60)

    list_devices()

    preferred = preferred_device()
    chosen = args.device if args.device is not None else preferred
    print("\nDevice selection:")
    print(f"  voice.py would pick : {preferred}")
    print(f"  using for this test : {chosen}")

    if args.list:
        check_energy_threshold()
        return 0

    try:
        data = record_wav(chosen, args.seconds)
    except Exception as exc:
        print(f"\n❌ Recording failed on {chosen}: {type(exc).__name__}: {exc}")
        print("   Try another device with --device NAME (see list above).")
        check_energy_threshold()
        return 1

    if not args.no_play:
        try:
            play_wav(WAV_PATH)
        except Exception as exc:
            print(f"   ⚠️  playback failed: {type(exc).__name__}: {exc}")

    recognise(data)
    check_energy_threshold()
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
