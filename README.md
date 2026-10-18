# Jarvis — Voice Assistant for Linux

A voice-controlled desktop assistant for Linux. You speak a command; Jarvis either
handles it **locally** (offline regex — no API call) or forwards it to a
**Groq-backed agent** that can call tools, then answers out loud in English.
Speech recognition and replies are **English-only by design**. A PyQt5
"Arc Reactor" widget provides a click-to-talk GUI.

## Features

- **Voice I/O** — speech-to-text via SpeechRecognition (Google STT, `en-US`) and
  text-to-speech via `edge-tts` (`en-US-AriaNeural`) played through `pygame`.
- **Local fast-path router** — offline regex handling for volume, system status,
  time, date, YouTube and app launching. The API is only used on a miss.
- **Groq agent with tool calling** — OpenAI-compatible Chat Completions client
  running a bounded tool-calling loop, with an in-memory response cache and
  token accounting.
- **Tools** — open app, system status, YouTube search, web search, save note,
  read file. Tool schemas are generated from the functions' signatures and
  docstrings, so there is no hand-written schema to keep in sync.
- **Conversation memory** — a rolling window of recent turns fed back to the model.
- **Two front ends** — a terminal loop (`main.py`) and a floating, transparent,
  always-on-top reactor widget (`gui.py`) with live state colours
  (Idle / Listening / Thinking / Speaking).
- **Robust audio capture** — capture runs through `arecord -D default -f S16_LE
  -r 16000 -c 1` (PyAudio/PortAudio never saw this machine's mic), read in 0.1 s
  chunks and RMS-gated: it starts above RMS 300, keeps a ~0.3 s pre-roll, uses
  hysteresis (stays recording down to RMS 150) and ends after ~1.2 s below that,
  with bounded timeouts so a misbehaving mic can never stall the loop.
- **Tests** — a `pytest` suite covering routing, exit detection, memory, the
  response cache, the capture gate, and the real router→agent dispatch path.

## Requirements

- Linux with a working microphone (PipeWire or PulseAudio).
- `pactl` (from `pulseaudio-utils`/`pipewire-pulse`) for volume control.
- `arecord` / `aplay` from `alsa-utils` for capture and playback
  (`sudo apt install alsa-utils`).
- Python 3.10+ (developed on 3.12).

## Setup

```bash
python -m venv venv
./venv/bin/pip install -r requirements.txt

# Create .env with your Groq API key (starts with "gsk_")
echo "GROQ_API_KEY=your-key-here" > .env
```

Get a key at <https://console.groq.com/keys>. `.env`, `venv/` and caches are
gitignored and must never be committed.

### Configuration

All configuration lives in `config.py` and can be overridden from `.env`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `GROQ_API_KEY` | *(required)* | Groq API key, `gsk_…` |
| `GROQ_BASE_URL` | `https://api.groq.com/openai/v1` | OpenAI-compatible endpoint |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Model ID (must support tools) |
| `JARVIS_MIC_DEVICE` | `default` | ALSA PCM name for `arecord -D` (any name from `arecord -L`) |
| `JARVIS_DEBUG` | *(unset)* | Set to `1` to print per-chunk RMS / capture diagnostics |

STT language (`en-US`), TTS voice (`en-US-AriaNeural`), history window, output
token cap and tool-round limit are constants in `config.py`.

## Usage

```bash
./venv/bin/python main.py       # terminal voice loop
./venv/bin/python gui.py        # floating Arc Reactor widget
./venv/bin/python test_mic.py   # microphone diagnostic (--list to list devices)
./venv/bin/python -m pytest     # run the test suite
```

In the CLI loop, prints `[STAT]` lines with token counts and tracks
local vs. agent vs. cache hits. Say **"exit"**, **"quit"**, **"bye"** or
**"stop"** to end a session. In the GUI, left-click to talk, drag to move, and
right-click / `Esc` to quit.

### Example commands

| You say | Handled by |
| --- | --- |
| "open YouTube" | local router |
| "what time is it" / "system status" | local router |
| "volume up" / "mute" | local router |
| "open chrome" | local router |
| "search for lo-fi music" | Groq agent (search verb → tool) |
| "save a note: buy milk" | Groq agent → `fn_save_note` |

## Architecture

```
listen() ──► try_local() ──hit──► speak()
   (voice.py)   (router.py)          (voice.py)
                    │
                   miss
                    ▼
             run_agent() ──► Groq (agent.py) ──► tools.py
                    │
                    ▼
             memory.add() (memory.py)
```

`main.handle()` is the single dispatch path: it tries the router first and only
sends a miss to the agent.

### Module map

| File | Role |
| --- | --- |
| `config.py` | Loads `.env`; holds `API_KEY`, `BASE_URL`, `MODEL_NAME`, `STT_LANG`, `TTS_VOICE` and the history/output/tool limits. |
| `voice.py` | `listen()` (SpeechRecognition + Google STT, `en-US`) and `speak()` (edge-tts + pygame). `listen()` captures via `arecord` in 0.1 s chunks, RMS-gates at `_ENERGY_THRESHOLD`, and wraps the bytes in `sr.AudioData`; it needs no PyAudio. |
| `router.py` | `try_local()` — offline handling for volume, system status, time, date, YouTube and app launching. Commands containing a search verb (`search`, `look up`, `find`) deliberately return `None` so they reach the agent. |
| `agent.py` | Groq chat orchestration: md5 response cache, token accounting, tool schemas built from function signatures, English-only system instruction, bounded tool loop. |
| `tools.py` | Tool functions: open app, system status, YouTube, web search, save note, read file. |
| `memory.py` | Rolling `deque` window of recent turns, rendered as a transcript. |
| `main.py` | Terminal entry point and `handle()` dispatch. |
| `gui.py` | PyQt5 Arc Reactor widget; the listen/think/speak cycle runs on a `QThread`. |
| `test_mic.py` | Standalone mic diagnostic (arecord-based): lists capture devices via `arecord -l`/`-L`, records a WAV with the same command as `listen()`, reports RMS/peak, plays it back with `aplay`, and recognises it. |

## Troubleshooting

- **`Listening…` repeats and nothing is recognised.** Run with diagnostics on:

  ```bash
  JARVIS_DEBUG=1 ./venv/bin/python main.py
  ```

  This prints the device, the RMS gate, the per-0.1 s chunk RMS, the captured
  length/peak, and whether the outcome was a timeout, an unrecognised capture,
  or a network (`RequestError`) failure.
- **Input is clipping** (debug prints `peak at full scale (32768)`): lower the
  microphone gain in your system mixer (`alsamixer`, or your desktop's sound
  settings).
- **No audio / no mic found:** run `./venv/bin/python test_mic.py` to list devices
  and check capture.
- **Agent replies are cut off:** raise `MAX_OUTPUT_TOKENS` in `config.py`
  (reasoning models need more headroom).
- **`RequestError` from STT:** the speech-to-text endpoint needs network access;
  the local router fast-path still works offline.

## Security notes

- `fn_read_file` reads any user-supplied path and `fn_save_note` writes to
  `~/jarvis_notes.md` — there is no sandboxing. Review before exposing Jarvis to
  untrusted input.
- Never commit `.env` or API keys. `.gitignore` covers `.env`, `venv/`, caches,
  audio artifacts and token files.

## License

MIT © 2026 Jakir Hussain Khan. See [LICENSE](LICENSE).
