# Jarvis — Voice Assistant for Linux

A voice-controlled desktop assistant for Linux. You speak a command; Jarvis either
handles it **locally** (offline regex — no API call) or forwards it to a
**Groq-backed agent** that can call tools, then answers out loud in English.
Speech recognition and replies are **English-only by design**. A PyQt5
"Arc Reactor" widget provides a click-to-talk GUI.

All system actions go through one whitelisted module (`desktop.py`): Jarvis never
uses `sudo` and never runs arbitrary shell commands from speech. Power actions are
confirmation-gated, and a protected list keeps Jarvis, its terminal, the display
server, the session manager, systemd and the desktop shell safe from being closed.

## Features

- **Voice I/O** — speech-to-text via SpeechRecognition (Google STT, `en-US`) and
  text-to-speech via `edge-tts` (`en-US-AriaNeural`) played through `pygame`.
- **Local fast-path router** — offline handling for power, media, volume,
  brightness, screenshot, lock, battery, system status, time, date, YouTube and
  opening/closing apps. The API is only used on a miss.
- **Desktop control** — open any installed app (fuzzy match over `.desktop`
  files), graceful close (window close → SIGTERM → SIGKILL), list running windows,
  YouTube play/search, media (`playerctl`), volume (`pactl`), brightness
  (`brightnessctl`), screenshots and screen lock.
- **Safety** — protected-process list, "Did you mean <app>?" on weak matches, and
  an "Are you sure?" confirmation (15 s, "yes"-only) before shutdown/restart/
  logout/suspend. `close/quit/kill X` can never become a YouTube search.
- **Groq agent with tool calling** — OpenAI-compatible Chat Completions client
  running a bounded tool-calling loop, with an in-memory response cache and
  token accounting.
- **Tools** — open app, close app, list running, YouTube, media, volume,
  brightness, screenshot, lock, system status, web search, save note, read file.
  Tool schemas are generated from the functions' signatures and docstrings, so
  there is no hand-written schema to keep in sync.
- **Conversation memory** — a rolling window of recent turns fed back to the model.
- **Two front ends** — a terminal loop (`main.py`) and a floating, transparent,
  always-on-top reactor widget (`gui.py`) with live state colours
  (Idle / Listening / Thinking / Speaking). Both share `main.handle()`, so the
  confirmation flow works identically in each.
- **Usable without a microphone** — `JARVIS_TEXT=1 ./venv/bin/python main.py`
  (or `main.py --text`) takes *typed* commands through the exact same
  router/agent path, and `./venv/bin/python doctor.py` (or `main.py --check`)
  prints a readiness report: which tools are installed, what each missing one
  costs, and how many apps were indexed.
- **Robust audio capture** — capture runs through `arecord -D default -f S16_LE
  -r 16000 -c 1` (PyAudio/PortAudio never saw this machine's mic), read in 0.1 s
  chunks and RMS-gated. The stream-open spike is discarded, the stop floor is
  auto-calibrated from the room noise (`1.5 × median`, clamped to 150–300), a
  ~0.3 s pre-roll keeps quiet onsets, and hysteresis ends the phrase after ~1.2 s
  of silence. Phrases are capped at 6 s. Playing media is paused while Jarvis
  listens and resumed afterwards (best effort), and `JARVIS_PTT=1` switches to
  push-to-talk (press Enter, speak, press Enter again) for noisy rooms. All
  bounded so a misbehaving mic can never stall the loop.
- **Tests** — a `pytest` suite (110 tests) covering routing, app matching, the
  protected list, the confirmation flow, volume/brightness limits and phrasing,
  the YouTube search/play path (mocked subprocess calls), push-to-talk, the
  text mode, the readiness doctor, exit detection, memory, the response cache,
  the capture gate, and the real router→agent dispatch path.

## Requirements

- Linux with a working microphone (PipeWire or PulseAudio).
- `arecord` / `aplay` from `alsa-utils` for capture and playback
  (`sudo apt install alsa-utils`).
- Python 3.10+ (developed on 3.12).

Optional desktop tools — Jarvis degrades gracefully when these are missing, but
these commands need them:

```bash
sudo apt install playerctl brightnessctl xdotool scrot imagemagick
```

| Tool | Enables |
| --- | --- |
| `pactl` (pulseaudio-utils/pipewire-pulse) | volume up/down/set/mute |
| `wmctrl` | closing/focusing windows on X11 |
| `gtk-launch` / `gio` | launching apps from `.desktop` files |
| `yt-dlp` | playing the top YouTube result instead of opening search |
| `playerctl` | pause / resume / next / previous / stop |
| `brightnessctl` | screen brightness |
| `xdotool` | closing a focused YouTube tab with Ctrl+W (X11) |
| `gnome-screenshot` / `scrot` / ImageMagick `import` | screenshots |
| `cinnamon-screensaver-command` / `loginctl` | lock screen |

> On **Wayland**, `wmctrl` and `xdotool` do not work. App control falls back to
> process-based closing and media to `playerctl`; `close youtube` then answers
> "Which window?" instead of sending Ctrl+W.

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
| `JARVIS_DEBUG` | *(unset)* | Set to `1` to print per-chunk RMS / capture diagnostics (including the calibrated background median and floor) |
| `JARVIS_PTT` | *(unset)* | Set to `1` for push-to-talk: press Enter to record, press Enter again (or stop talking) to end the phrase |
| `JARVIS_TEXT` | *(unset)* | Set to `1` (or run `main.py --text`) to type commands instead of speaking |
| `JARVIS_MUTE` | *(unset)* | Set to `1` to skip text-to-speech entirely (replies are still printed) |

STT language (`en-US`), TTS voice (`en-US-AriaNeural`), history window, output
token cap and tool-round limit are constants in `config.py`.

## Usage

```bash
./venv/bin/python main.py       # terminal voice loop
./venv/bin/python main.py --text    # type commands instead of speaking
./venv/bin/python main.py --check   # readiness report (same as doctor.py)
./venv/bin/python doctor.py     # readiness report: tools, apps, config
./venv/bin/python gui.py        # floating Arc Reactor widget
./venv/bin/python test_mic.py   # microphone diagnostic (--list to list devices)
./venv/bin/python -m pytest     # run the test suite
```

In the CLI loop, prints `[STAT]` lines with token counts and tracks
local vs. agent vs. cache hits. Say **"exit"**, **"quit"**, **"bye"** or
**"stop"** (on their own) to end a session. In the GUI, left-click to talk, drag
to move, and right-click / `Esc` to quit.

### Voice commands

| You say | Handled by |
| --- | --- |
| "open telegram" / "open files" / "open terminal" / "open settings" / "open calculator" | local router (fuzzy app match) |
| "close telegram" / "quit files" / "kill calculator" | local router (graceful close) |
| "open youtube" | local router |
| "play lo-fi on youtube" | local router (`yt-dlp` top result; only the song name is used, filler is stripped) |
| "search cats on youtube" | Groq agent → YouTube search |
| "pause" / "resume" / "next song" / "previous track" / "stop the music" | local router (`playerctl`) |
| "volume up" / "volume down" / "set volume to 40" / "mute" / "unmute" | local router (`pactl`) |
| "brightness up" / "brightness down" / "set brightness to 40" | local router (`brightnessctl`) |
| "take a screenshot" | local router (saves to `~/Pictures`) |
| "lock the screen" | local router |
| "what time is it" / "what's the date" / "battery" / "system status" | local router |
| "shut down" / "restart" / "log out" / "suspend" | local router → **"Are you sure?"** |
| "search for lo-fi music" / "look up the weather" / "find my notes" | Groq agent (search verb → tool) |
| "save a note: buy milk" | Groq agent → `fn_save_note` |

`close youtube` closes the *focused tab* (Ctrl+W) when possible, and never
closes the whole browser unless you say "close the browser" / "close brave".

## Safety

- **Protected, never touched:** Jarvis itself and its entire ancestor chain (so
  the terminal running it), the display server (`Xorg`/`Xwayland`), the session
  manager, `systemd`/`dbus`, and the desktop shell (`cinnamon`, `gnome-shell`,
  `plasmashell`, …).
- **Confirmation-gated:** shutdown, restart, logout and suspend register a
  pending action and ask **"Are you sure?"**. Only a clear "yes" runs it;
  anything else cancels, and an unanswered request expires after ~15 s. (There is
  currently no file-delete action.)
- **Weak matches:** if an app name is only a weak match, Jarvis asks
  "Did you mean <app>?" before closing it.
- **Whitelist only:** the agent's tools and the router both call fixed functions
  in `desktop.py`; no `sudo`, no arbitrary shell.

## Architecture

```
listen() ──► confirm.resolve() ──► try_local() ──hit──► speak()
   (voice.py)     (confirm.py)      (router.py)           (voice.py)
                                        │
                                       miss
                                        ▼
                                 run_agent() ──► Groq (agent.py) ──► tools.py ──► desktop.py
                                        │
                                        ▼
                                 memory.add() (memory.py)
```

`main.handle()` is the single dispatch path: it resolves any pending
confirmation first, then tries the router and only sends a miss to the agent.

### Module map

| File | Role |
| --- | --- |
| `config.py` | Loads `.env`; holds `API_KEY`, `BASE_URL`, `MODEL_NAME`, `STT_LANG`, `TTS_VOICE` and the history/output/tool limits. |
| `voice.py` | `listen()` (SpeechRecognition + Google STT, `en-US`) and `speak()` (edge-tts + pygame). `listen()` captures via `arecord` in 0.1 s chunks, discards the stream-open spike, auto-calibrates the stop floor, and wraps the bytes in `sr.AudioData`; it needs no PyAudio. |
| `router.py` | `try_local()` — offline handling for power, media, volume, brightness, screenshot, lock, battery, system status, time, date, YouTube and app open/close. Search verbs return `None` so they reach the agent. |
| `desktop.py` | Whitelisted desktop actions: `.desktop` app index + fuzzy match, launch/close/list, YouTube, media, volume, brightness, screenshot, lock, power, and the protected-process list. |
| `confirm.py` | Pending "Are you sure?" state: yes runs, anything else cancels, expires after 15 s. |
| `agent.py` | Groq chat orchestration: md5 response cache, token accounting, tool schemas built from function signatures, English-only system instruction, bounded tool loop. |
| `tools.py` | Tool functions: open/close/list apps, YouTube, media, volume, brightness, screenshot, lock, system status, web search, save note, read file. |
| `memory.py` | Rolling `deque` window of recent turns, rendered as a transcript. |
| `main.py` | Terminal entry point and `handle()` dispatch (`--text`, `--check`). |
| `gui.py` | PyQt5 Arc Reactor widget; the listen/think/speak cycle runs on a `QThread` and shares `main.handle()`. |
| `doctor.py` | Readiness self-check: session type, installed tools and what each missing one costs, apps indexed, and API-key status. |
| `test_mic.py` | Standalone mic diagnostic (arecord-based): lists capture devices via `arecord -l`/`-L`, records a WAV with the same command as `listen()`, reports RMS/peak, plays it back with `aplay`, and recognises it. |

## Troubleshooting

- **Microphone problem / want to try commands instantly.** Type them instead of
  speaking (no mic needed):

  ```bash
  JARVIS_TEXT=1 ./venv/bin/python main.py     # add JARVIS_MUTE=1 to skip TTS
  ```

  and print a readiness report first with `./venv/bin/python doctor.py`.
- **`Listening…` repeats and nothing is recognised.** Run with diagnostics on:

  ```bash
  JARVIS_DEBUG=1 ./venv/bin/python main.py
  ```

  This prints the device, the RMS gate, the per-0.1 s chunk RMS (with the
  warm-up/waiting/recording state and the calibrated `floor=`), the captured
  length/peak, and whether the outcome was a timeout, an unrecognised capture,
  or a network (`RequestError`) failure.
- **Input is clipping** (debug prints `peak at full scale (32768)`): lower the
  microphone gain in your system mixer (`alsamixer`, or your desktop's sound
  settings).
- **Phrases never end / music keeps the gate open:** if speakers or music leak
  into the mic, the background median can stay above the silence floor and the
  phrase runs to the 6 s cap. In debug mode Jarvis prints
  `WARNING: background median … > 200 - speakers or music are probably leaking
  into the mic`. Move the mic away from the speakers, or set `JARVIS_PTT=1` and
  end phrases by hand with Enter.
- **"playerctl/brightnessctl is not installed"**: install them with
  `sudo apt install playerctl brightnessctl`.
- **No audio / no mic found:** run `./venv/bin/python test_mic.py` to list devices
  and check capture.
- **Agent replies are cut off:** raise `MAX_OUTPUT_TOKENS` in `config.py`
  (reasoning models need more headroom).
- **`RequestError` from STT:** the speech-to-text endpoint needs network access;
  the local router fast-path still works offline.

## Security notes

- The router and agent only call the fixed functions in `desktop.py`; there is no
  `sudo` and no arbitrary shell execution from speech.
- `fn_read_file` reads any user-supplied path and `fn_save_note` writes to
  `~/jarvis_notes.md` — there is no sandboxing. Review before exposing Jarvis to
  untrusted input.
- Never commit `.env` or API keys. `.gitignore` covers `.env`, `venv/`, caches,
  audio artifacts and token files.

## License

MIT © 2026 Jakir Hussain Khan. See [LICENSE](LICENSE).
