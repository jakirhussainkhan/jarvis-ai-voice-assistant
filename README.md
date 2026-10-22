![CI](https://github.com/jakirhussainkhan/jarvis-ai-voice-assistant/actions/workflows/ci.yml/badge.svg)

# Jarvis — Cross-Platform Voice Assistant

**Version 2.2.3**

## Download and run (no Python needed)

Prebuilt apps are attached to the
[latest GitHub release](https://github.com/jakirhussainkhan/jarvis-ai-voice-assistant/releases/latest):

| Platform | Download | First launch |
| --- | --- | --- |
| Windows 10/11 | `JARVIS.exe` | Double-click it. If SmartScreen appears, choose **More info → Run anyway**. |
| Linux (Debian/Ubuntu) | `jarvis_2.2.3_amd64.deb` | Double-click it, or run `sudo apt install ./jarvis_2.2.3_amd64.deb`. |
| macOS 12+ (Intel or Apple Silicon) | `JARVIS.dmg` | Open it, drag **JARVIS** to **Applications**, then right-click the app and choose **Open** the first time. |

The macOS build is Intel; Apple Silicon runs it through Rosetta 2, which macOS
offers to install automatically the first time.

The app opens a small window and asks for a free
[Groq API key](https://console.groq.com/keys) on first run. The key is stored
only in your own home folder (`~/.jarvis/.env`). Microphone permission is
requested automatically; see the platform guides for screen/accessibility
permissions.

> The macOS build is ad-hoc signed (without a paid Apple Developer ID), so
> Gatekeeper asks for a one-time confirmation. The Windows EXE is unsigned, so
> SmartScreen may do the same. These prompts are expected and safe to accept.

A voice-controlled desktop assistant for **Linux, Windows and macOS** that can
*see* your screen, use the internet, and operate the terminal. You speak a
command; Jarvis either handles it **locally and offline** (regex router — no API
call) or hands it to a **Groq-backed agent** that can call 44 tools, then answers
out loud in English. Speech recognition and replies are **English-only by
design**. A PyQt5 ChatGPT-Voice-style card lives in the system tray and talks
when you click it — or when you just say **"Hey Jarvis"**, once the optional
offline wake word is installed.

Every OS-specific action lives behind a **platform adapter**
(`platform_adapters/`): Linux uses `arecord`/`pactl`/`playerctl`/`wmctrl`/`xdotool`/
`brightnessctl`, Windows uses `sounddevice`/`pycaw`/`pywinctl`, and macOS uses
`sounddevice`/`osascript`/`pywinctl`. The core (router, agent, memory, TTS, GUI,
vision) is identical on all three, and a missing library degrades to a clear
message instead of crashing.

Jarvis **never runs `sudo`** and never runs arbitrary shell from speech — except
through a guarded terminal tool that refuses anything that could power the device
off and asks before anything risky. Power actions and other sensitive steps are
confirmation-gated everywhere. When a tool is missing it will *print* a
copy-paste install command for you to run yourself, but it never executes one.

---

## What's new in the UI overhaul

- **A ChatGPT-Voice-style window.** The old floating reactor is now a frameless,
  rounded dark card (`#1E1E1E`), always on top, with minimise/close controls in
  the top-right and one modern status line ("Listening...", "Thinking...",
  "Jarvis is speaking...").
- **A living orb and a live waveform.** The orb is a `QPainter` gradient that
  breathes with a `QPropertyAnimation` when idle, reacts to your real mic level
  while listening and to the TTS envelope while speaking. Thirteen bars below it
  scroll in real time from the same `voice.set_level_sink()` data.
- **Barge-in.** Start talking while Jarvis is speaking and it stops immediately
  and returns to listening — no Stop button needed. Echo is rejected with a high,
  sustained RMS threshold; `JARVIS_BARGE_THRESHOLD`, `JARVIS_BARGE_HOLD` and
  `JARVIS_BARGE_IN=0` tune it, and push-to-talk (`JARVIS_PTT=1`) disables it.
- **A new app icon.** A glowing azure orb with a stylised "J" on a charcoal tile,
  drawn by `icon_art.py` and written to `assets/jarvis-{32,48,64,128,256}.png` by
  `gen_icon.py`; the window, tray and launcher all share that one artwork.

## What's new in 1.0.11

- **The clickable orb is an Iron Man arc reactor too.** The Reactor widget now
  paints the *same* artwork as the launcher icon — dark gunmetal housing, ten
  glowing coil slots and a white-hot core — through one shared painter
  (`gui._draw_reactor_face`), so the icon and the orb can never drift apart.
  All behaviour survives: the "breathing" shimmer, the live mic/TTS energy
  pulse, and the per-state colour (the light tints green while listening,
  amber while thinking, purple while speaking, grey when muted, red slash
  badge included).

## What's new in 1.0.10

- **Jarvis uses the YouTube tab you already had open.** "Play X" (or a search)
  with YouTube on screen now navigates *that tab* in place — no pile of extra
  tabs — and falls back to a normal tab when it can't take input safely.
  "Open YouTube" brings the existing tab up instead of opening another.
- **"Close YouTube" closes the YouTube tab from anywhere.** The visible
  YouTube window is closed directly; when the title has no "YouTube" suffix it
  only closes the focused tab if a browser media session is actually playing
  there, and it never presses ctrl+w blind: without verified input focus it
  says "There is no YouTube tab open." A browser focused on other work is
  always left alone.
- Keyboard/window actions (play nudge, next/previous, close tab) now verify
  real X input focus before sending keys, so nothing is ever typed into the
  wrong application under Cinnamon's focus-stealing prevention.

## What's new in 1.0.9

- **An Iron Man face for Jarvis.** The icon is now a proper arc reactor, built
  the way Tony Stark's reads on screen: a dark gunmetal housing, **ten glowing
  coil slots** around a **white-hot core** with a light halo. Rendered with the
  same code for the widget, the tray, the app-menu and every committed
  `assets/jarvis-*.png`, so they can never drift apart. Legible down to 32 px.

## What's new in 1.0.8

- **Continuous conversation.** Say one thing and Jarvis replies, then **opens the
  microphone again** so you can keep talking — hands-free, like ChatGPT's voice
  mode. It stops only when you: go quiet (**60 s**, `JARVIS_CHAT_TIMEOUT`), say an
  **exit word** ("bye", "stop", "exit"), press **Stop**, or mute the mic.
  `JARVIS_CHAT=0` brings back the old one-shot behaviour, and the checkable
  **Continuous chat** menu item switches it at runtime.
- **End a session by voice.** A spoken "bye"/"stop"/"exit" now closes the whole
  exchange politely instead of expecting the terminal. And risky commands (power
  off, risky terminal commands) keep their spoken **"Are you sure?"** — a "yes"
  runs them, anything else cancels, so you never need to touch the keyboard.

## What's new in 1.0.7

- **A stronger, Iron-Man voice.** Jarvis now speaks with **`en-GB-RyanNeural`** —
  a British male neural voice, the closest match to Iron Man's Jarvis — instead of
  the female `en-US-AriaNeural`. Change it without touching code:
  `JARVIS_TTS_VOICE=…` in `.env` (any edge-tts voice name).

## What's new in 1.0.6

- **Launch it like a real app.** `./run.sh` starts the GUI with the right
  virtualenv, no typing and no `cd`. Prefer a click? `install_desktop.py` adds
  Jarvis to your **application menu** (with the arc-reactor icon) and can
  optionally start it at login:

  ```bash
  ./venv/bin/python install_desktop.py              # add to the app menu
  ./venv/bin/python install_desktop.py --autostart  # ...and start at login
  ./venv/bin/python install_desktop.py --uninstall   # undo, exactly
  ```

  It only writes inside your own home directory (`~/.local/share/applications`
  and `~/.config/autostart`) — no root, no `sudo`, nothing else touched.
- **A crisper orb icon.** The icon margin is now proportional, so the reactor is
  no longer clipped when rendered large (it is shipped at 32-256 px in `assets/`).

## What's new in 1.0.5

- **"Hey Jarvis" — hands-free, fully offline.** The GUI no longer needs a click:
  say the wake phrase and it starts listening, with a short chime to confirm it
  heard you. It uses `openWakeWord`'s pretrained model, which runs entirely on
  this machine (no network, no account, ~1-2% of one CPU core). Optional, so
  nothing changes until you install it:
  `./venv/bin/pip install openwakeword onnxruntime`. It is on by default once
  installed — turn it off with `JARVIS_WAKE=0` or the **Hey Jarvis** menu item.
- **The microphone is never fought over.** Only one thing records at a time: the
  wake listener releases the mic *before* a listen cycle starts and takes it back
  the moment Jarvis is idle again. Muting the mic stops the wake word too, so a
  muted microphone really is silent.
- **It explains itself.** `doctor.py` reports whether the wake word is available,
  listening, or switched off, and prints the exact `pip install` line when it is
  missing. Tune the sensitivity with `JARVIS_WAKE_THRESHOLD` if it fires too
  eagerly (or misses you), and `JARVIS_WAKE_DING=0` silences the chime.

## What's new in 1.0.4

- **It no longer cuts you off mid-sentence.** A pause while you think (or between
  "play …" and the song name) used to end the phrase after just **1.2 s**, and the
  whole utterance was **capped at 6 s**. It now tolerates a **2 s** pause and fits
  up to **15 s** of speech. Tune with `JARVIS_SILENCE_END` / `JARVIS_PHRASE_LIMIT`.
- **Silent screen reading — no more flash.** `gnome-screenshot` plays a bright
  shutter animation (and `vision.act` captures once per step), which is painful to
  look at. Screenshots now use **`scrot`**, then ImageMagick **`import`**, and
  `gnome-screenshot` only as a last resort.

## What's new in 1.0.3

- **Play a song by name** — "play kesariya" (or "play the song kesariya") searches
  and plays it directly, and pause/resume/next work on it as usual.
- **Point at the screen** — "play the second song", "play this song", "play that
  one" and "play it" are read off your screen and acted on, so you can pick a
  video out of the feed you are looking at.
- **A 30-second listening window** — after you trigger Jarvis it now waits up to
  **30 s** for you to start speaking (was 4 s), so you have time to think or say a
  long name. Tune it with `JARVIS_LISTEN_TIMEOUT`.
- **Stop cancels a wait too** — pressing Stop (or `Space`) now abandons a listen
  that is still waiting for you, as well as cutting off a reply mid-sentence.
- **Playback that actually starts, and honest feedback** — a freshly opened or
  reused video usually sits paused (browsers block autoplay without a real
  click), and a paused tab publishes no MPRIS session at all. After navigating,
  Jarvis checks `playerctl status`; if it is not already **Playing** it focuses
  the YouTube window and presses **Space** (`xdotool`), then verifies before
  saying "Playing" — so it no longer claims success while the player is paused.
  **`next`/`previous`
  work on a YouTube tab** by driving the page's own shortcuts — Brave's MPRIS
  reports `CanGoNext = false`, so `playerctl` alone can never skip tracks there.
- **Copy-paste install hints** — a missing tool and the readiness report now print
  the exact command (e.g. `sudo apt install playerctl`) for *you* to run. Jarvis
  still never runs `sudo` or installs anything itself.

## What's new in 1.0.2

- **A live, voice-chat-style widget** — the reactor now *reacts*. While it listens
  it grows and glows with your real microphone level, and while it speaks it moves
  in time with its own words (the TTS loudness envelope).
- **Mic mute / unmute** — a button (plus a tray item, an `M` key, and a checkable
  tray action) mutes the microphone instantly, even mid-listen; the orb dims and
  Jarvis stops listening until you turn it back on.
- **Stop** — cut a reply off mid-sentence with the Stop button (or `Space`). A Stop
  pressed while the reply is still being synthesised cancels it too.

## What's new in 1.0.1

- **Screen vision** — ask "what's on my screen?" and Jarvis screenshots the
  display and answers with a multimodal model. Ask it to *operate* the UI
  ("click the login button") and it runs a bounded perceive → act → verify loop.
- **Live internet intelligence** — keyless web search, full-page reading, Wikipedia
  summaries and weather, so answers use *fresh* information, not just training data.
- **A guarded terminal** — Jarvis can run shell commands like an expert, behind a
  three-layer guard (see [Terminal safety](#terminal-safety)).
- **Offline-first** — everything that can work without a connection does: apps,
  folders, windows, media, volume, brightness, clipboard, screenshots, notes,
  timers and dictated commands. `main.py --offline` forces this mode.
- **A real desktop app** — the reactor floats on top, clicks to talk, and hides to
  the system tray instead of quitting.

---

## Features

- **Voice I/O** — speech-to-text via SpeechRecognition (Google STT, `en-US`) and
  text-to-speech via `edge-tts` (`en-GB-RyanNeural` — a British male voice) played
  through `pygame`.
- **Local fast-path router** — offline handling for power, media (incl. seeking by
  seconds and "what's playing"), volume, brightness, screenshot, lock, battery,
  system status, time, date, windows, clipboard, folders, YouTube (including
  **playing a song by name**, and a plain "play <name>"), dictated terminal
  commands, and opening/closing apps. The API is only used on a miss.
- **Real-time screen vision** — reads the screen ("what does this error say?",
  "summarise this page") and operates it ("click Save", "type my name in the box",
  or pick something you are pointing at: "play the second song", "play that one")
  through whitelisted mouse/keyboard actions only. Screenshots are taken
  **silently** (`scrot`, then `import`) — never with `gnome-screenshot`'s flash.
- **Internet intelligence** — `fn_web_lookup` (live search), `fn_fetch_page`,
  `fn_wikipedia`, `fn_weather`. No API keys required for any of them.
- **Guarded terminal** — `fn_run_command` (hidden, output returned) and
  `fn_run_in_terminal` (visible). Denies privilege escalation and every
  power/session command; confirms anything that mutates state.
- **Desktop control** — open any installed app (fuzzy match over `.desktop`
  files), graceful close, list/focus/minimise/maximise windows, click/type/scroll,
  YouTube play/search, media (`playerctl`, with a browser-tab fallback so
  play/pause/next work on YouTube itself), volume (`pactl`), brightness
  (`brightnessctl`), screenshots, clipboard, notifications, file search, notes,
  timers and reminders.
- **Safety everywhere** — protected-process list, "Did you mean <app>?" on weak
  matches, and an "Are you sure?" confirmation (15 s, "yes"-only) before
  shutdown/restart/logout/suspend, before any risky terminal command, and before
  destructive screen-automation goals (delete/buy/send/…). `close/quit/kill X`
  can never become a YouTube search.
- **Two front ends** — a terminal loop (`main.py`) and a floating, rounded,
  always-on-top voice card with a system tray (`gui.py`). Both share
  `main.handle()`, so confirmation and routing behave identically in each. The
  card is a mini voice-chat console: a breathing orb, a live 13-bar waveform, a
  mic mute button and a Stop button. One click starts a **continuous
  conversation**: Jarvis replies, then keeps listening so you can just talk,
  hands-free — and you can interrupt a reply by simply speaking.
- **Optional offline wake word** — say **"Hey Jarvis"** instead of clicking. The
  GUI listens through `openWakeWord`'s pretrained ONNX model: no network, no
  account, no API key, ~1-2% of one CPU core, and English-only like everything
  else. It is *optional*: install it with
  `./venv/bin/pip install openwakeword onnxruntime` and it switches on by itself
  (off with `JARVIS_WAKE=0`, or the checkable **Hey Jarvis** menu item). The wake
  listener and `voice.listen` **never hold the microphone at the same time** — the
  listener hands it over before recording starts — and muting the mic stops the
  wake word too. A short chime confirms it heard you (`JARVIS_WAKE_DING=0` mutes
  the chime), and `JARVIS_WAKE_THRESHOLD` tunes how easily it triggers.
- **Usable without a microphone** — `JARVIS_TEXT=1 ./venv/bin/python main.py`
  (or `main.py --text`) takes *typed* commands through the exact same
  router/agent path.
- **Long-term memory** — durable personal facts (name, preferences, birthdays)
  are stored in the same local SQLite database under `user_facts`. The agent
  saves them with `fn_remember_fact`, recalls them with `fn_recall_facts`, and
  deletes or replaces them with `fn_forget_fact` (say "forget my name", or give
  a new name and the old one is removed first). The agent receives the 15 most
  recent facts as a `[SYSTEM MEMORY: ...]` message before each prompt. The static
  system instruction stays unchanged for prefix caching.
- **Readiness report** — `./venv/bin/python doctor.py` (or `main.py --check`)
  prints the session type, which tools are installed and what each missing one
  costs, the model and vision model, the app count, how to try every command, and
  a **copy-paste `sudo apt install …` line** for whatever is missing (for you to
  run — Jarvis never runs it).
- **Robust audio capture** — capture runs through `arecord -D default -f S16_LE
  -r 16000 -c 1` (PyAudio/PortAudio never saw this machine's mic), read in 0.1 s
  chunks and RMS-gated. The stream-open spike is discarded, the stop floor is
  auto-calibrated from the room noise (`1.5 × median`, clamped to 150–300), a
  ~0.3 s pre-roll keeps quiet onsets, and hysteresis ends the phrase after ~2 s
  of silence. Phrases are capped at 15 s (`JARVIS_PHRASE_LIMIT`), and Jarvis waits
  up to **30 s** (`JARVIS_LISTEN_TIMEOUT`) for you to start speaking — Stop
  abandons a pending wait. Background media keeps playing while Jarvis listens,
  so "pause music" / "next song" work; the RMS gate and barge-in threshold keep
  the audio from registering as speech, and `JARVIS_PTT=1` switches to
  push-to-talk for noisy rooms.
- **Tests** — a `pytest` suite of **557 tests** (1 skipped) covering routing, app
  matching, the protected list, the confirmation flow, the terminal guardrails,
  screen routing, clipboard fallbacks, offline behaviour, the internet tools,
  vision parsing, timers, the YouTube path (play-by-name and on-screen picks),
  text mode, the doctor, the GUI (offscreen), the wake word (framing, threshold
  and the mic hand-over), the desktop launcher, the live mic-level/TTS-envelope
  hooks and the real router→agent dispatch path.

---

## Requirements

- **Linux, Windows or macOS**, with a working microphone.
- Python 3.10+ (developed on 3.12).
- A Groq API key for the agent, internet tools and screen vision.

Audio capture and playback come from the platform adapter: **Linux** uses
`arecord`/`aplay` (`alsa-utils`), while **Windows and macOS** use `sounddevice`
(installed from `requirements.txt`). Volume is `pactl` on Linux, `pycaw` on
Windows and `osascript` on macOS.

Optional desktop tools — Jarvis degrades gracefully when these are missing, but
these features need them. On Linux install them with your system package manager
(for example, `apt`); `./venv/bin/python doctor.py` prints the exact
`sudo apt install …` line for whatever is missing (Jarvis never runs it):

```text
playerctl  brightnessctl  wmctrl  xdotool  scrot  imagemagick  ffmpeg
notify-send  xdg-utils  yt-dlp  xclip (or xsel, optional — Qt is the fallback)
```

| Tool | Enables |
| --- | --- |
| `pactl` (pulseaudio-utils/pipewire-pulse) | volume up/down/set/mute |
| `wmctrl` | focus/minimise/maximise/close windows (X11) |
| `xdotool` | screen automation: click, type, scroll, keys (X11) |
| `gtk-launch` / `gio` | launching apps from `.desktop` files |
| `yt-dlp` | playing the top YouTube result instead of opening search |
| `playerctl` | pause / resume / next / previous / stop / seek |
| `brightnessctl` | screen brightness |
| `gnome-screenshot` / `scrot` / ImageMagick `import` | screenshots (`scrot`/`import` preferred — `gnome-screenshot` flashes the screen) |
| `convert` / `ffmpeg` | shrinking screenshots for the vision model |
| `notify-send` | desktop notifications |
| `xdg-open` | opening files, folders and URLs |
| `xclip` / `xsel` | reading the clipboard (falls back to Qt/PyQt5) |
| `cinnamon-screensaver-command` / `loginctl` | lock screen |

> On **Wayland**, `wmctrl` and `xdotool` do not work. Screen automation and
> window control report that plainly; app control falls back to process-based
> closing and media to `playerctl`.

**Optional: the "Hey Jarvis" wake word.** A second, separate install — it is not
in `requirements.txt` because it is a few hundred MB of ONNX runtime and only the
GUI uses it. Nothing else changes if you skip it:

```bash
./venv/bin/pip install openwakeword onnxruntime   # ~1-2% CPU, fully offline
```

On Windows the same install is:

```powershell
.\venv\Scripts\python.exe -m pip install openwakeword onnxruntime
```

The first run downloads the pretrained model (a one-time network fetch); after
that it never touches the network. `doctor.py` prints this exact line for you when
the wake word is missing, and Jarvis itself never installs anything.

---

## Installation

Pick your platform and follow its guide. Each installer handles the Python venv, platform-specific dependencies, and the starter configuration — about 3 minutes on broadband.

| Platform | Installation guide | One-liner |
| --- | --- | --- |
| **Linux** | [Linux Instructions](docs/install-linux.md) | `curl -fsSL https://raw.githubusercontent.com/jakirhussainkhan/jarvis-ai-voice-assistant/main/install.sh \| bash` |
| **Native Windows** | [Windows Instructions](docs/install-windows.md) | `irm https://raw.githubusercontent.com/jakirhussainkhan/jarvis-ai-voice-assistant/main/install.ps1 \| iex` |
| **macOS** | [macOS Instructions](docs/install-macos.md) | `curl -fsSL https://raw.githubusercontent.com/jakirhussainkhan/jarvis-ai-voice-assistant/main/install.sh \| bash` |
| **Desktop GUI** | Download from the [latest release](https://github.com/jakirhussainkhan/jarvis-ai-voice-assistant/releases) | — |

Run the one-liner from inside the cloned repository folder. Then launch JARVIS with `./run.sh` (Linux/macOS) or by double-clicking **`start.bat`** (Windows; `start.bat --console` keeps a log window open). Check readiness with `./venv/bin/python doctor.py` or `.\venv\Scripts\python.exe main.py --check`.

On Windows, **`stop.bat`** stops Jarvis cleanly, even when the widget is hidden in the tray. To start it at login, run `.\venv\Scripts\python.exe main.py --autostart-enable` (undo with `--autostart-disable`).

Platform-specific notes (WSL2 setup, native-Windows scheduled-task service, desktop prerequisites, manual install) are covered in the three guides above.

### Configuration

All configuration lives in `config.py` and can be overridden from `.env`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `GROQ_API_KEY` | *(required)* | Groq API key, `gsk_…` |
| `GROQ_BASE_URL` | `https://api.groq.com/openai/v1` | OpenAI-compatible endpoint |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Main model ID (must support tools) |
| `GROQ_VISION_MODEL` | `qwen/qwen3.8-27b` | Multimodal model for screen vision (verified live) |
| `USE_LOCAL_LLM` | `False` | `True` sends agent requests to local Ollama instead of Groq |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible Ollama endpoint |
| `OLLAMA_MODEL` | `llama3` | Model pulled in Ollama (for example `llama3`) |
| `JARVIS_SCREEN_MAX_WIDTH` | `1280` | Downscale width for vision screenshots |
| `JARVIS_SCREEN_STEPS` | `6` | Max perceive→act steps per screen goal |
| `JARVIS_SHELL_TIMEOUT` | `20` | Seconds before a terminal command is killed |
| `JARVIS_SHELL_MAX_OUTPUT` | `4000` | Output characters returned to the model |
| `JARVIS_OFFLINE` | *(unset)* | `1` disables the agent (router only) |
| `JARVIS_MIC_DEVICE` | `default` | ALSA PCM name for `arecord -D` |
| `JARVIS_LISTEN_TIMEOUT` | `30` | Seconds to wait for you to start speaking |
| `JARVIS_SILENCE_END` | `2.0` | Seconds of a pause that ends the phrase |
| `JARVIS_PHRASE_LIMIT` | `15` | Max seconds of a single utterance |
| `JARVIS_DEBUG` | *(unset)* | `1` prints per-chunk RMS / capture diagnostics |
| `JARVIS_PTT` | *(unset)* | `1` for push-to-talk (Enter to start/stop) |
| `JARVIS_TEXT` | *(unset)* | `1` to type commands instead of speaking |
| `JARVIS_MUTE` | *(unset)* | `1` to skip text-to-speech (replies still print) |
| `JARVIS_TTS_VOICE` | `en-GB-RyanNeural` | Spoken voice (any edge-tts name; restart Jarvis to apply) |
| `JARVIS_CHAT` | *(on)* | `0` turns continuous voice conversation off |
| `JARVIS_CHAT_TIMEOUT` | `60` | Seconds of silence that end a continuous session |
| `JARVIS_WAKE` | *(on if installed)* | `0` disables the "Hey Jarvis" wake word |
| `JARVIS_WAKE_MODEL` | `hey jarvis` | Wake phrase/model name (`alexa`, `hey_mycroft`, … or `off`) |
| `JARVIS_WAKE_THRESHOLD` | `0.5` | Score (0-1) a frame needs to count — raise if it triggers too easily |
| `JARVIS_WAKE_DING` | *(on)* | `0` silences the "I heard you" chime |
| `JARVIS_WAKE_VAD` | *(off)* | Voice-activity threshold to ignore non-speech noise |
| `JARVIS_WAKE_SPEEX` | *(off)* | `1` enables Speex noise suppression (needs the extra speexdsp wheel) |

**Optional local LLM:** install and start
[Ollama](https://ollama.com), pull a model (`ollama pull llama3`), then set
`USE_LOCAL_LLM=True` in `.env` or pass `--local` for one session. Jarvis uses
Ollama's OpenAI-compatible `/v1` endpoint, so no extra Python package is needed.
With the flag unset, Ollama is never contacted during normal Groq operation.

STT language (`en-US`), TTS voice (`en-GB-RyanNeural`), history window, output
token cap and tool-round limit are constants in `config.py`.

---

## Usage

```bash
./run.sh                                     # the GUI (floating widget + tray)
./venv/bin/python main.py                    # terminal voice loop
./venv/bin/python main.py --text             # type commands instead of speaking
./venv/bin/python main.py --offline          # router only (no network)
./venv/bin/python main.py --local            # this session uses local Ollama
./venv/bin/python main.py --check            # readiness report (same as doctor.py)
./venv/bin/python main.py --version          # print the version
./venv/bin/python main.py --help             # every option, with a short description
./venv/bin/python main.py --screen "what is on my screen?"   # one screen answer
./venv/bin/python main.py --gui              # floating widget + system tray
./venv/bin/python main.py --wake             # wake the running Jarvis (or start it)
./venv/bin/python main.py --quit             # stop the running Jarvis (e.g. hidden in the tray)
./venv/bin/python install_desktop.py         # add Jarvis to the app menu
./venv/bin/python doctor.py                  # readiness report
./venv/bin/python test_mic.py                # microphone diagnostic
./venv/bin/python -m pytest                  # run the test suite
```

In the CLI loop, `[STAT]` lines show token counts and track local vs. agent vs.
cache hits. Say **"exit"**, **"quit"**, **"bye"** or **"stop"** (on their own) to
end a session.

### Windows

```powershell
.\start.bat                                 # the GUI, no console window
.\start.bat --console                       # the GUI with a visible log window
.\stop.bat                                  # stop it, even from the tray
.\venv\Scripts\python.exe main.py           # terminal voice loop
.\venv\Scripts\python.exe main.py --text    # type commands instead of speaking
.\venv\Scripts\python.exe main.py --check   # readiness report
.\venv\Scripts\python.exe main.py --quit    # same as stop.bat
```

`start.bat` uses `pythonw.exe`, so there is no console window; starting it twice
is harmless (the second launch wakes the running instance). `stop.bat` asks the
app to quit, then force-stops only this project's process if it is stuck.

**GUI:** left-click the orb (or the tray icon) to talk — or just say **"Hey
Jarvis"** once the wake word is installed, or press **Ctrl+Shift+J** (or
**Ctrl+Alt+J**) from any application to wake Jarvis — it brings the card
forward and unmutes the mic if it was muted — drag to move the card, drag an
edge/corner or the corner grip to **resize** it, right-click
for the menu, `Esc`, the **✕** button or the window close all hide it to the
tray (they never quit), and only **Quit**
in the tray menu really exits. Launching Jarvis twice is safe: the second launch
wakes the running one and exits, so there is only ever one tray icon (a lock
file at `~/.jarvis/jarvis.lock`). The orb **breathes** when idle, **reacts to your
voice** while it listens and **moves while it speaks**, and the 13-bar waveform
under it shows the live level. Start talking while Jarvis speaks to **interrupt
it** (barge-in): the reply stops and Jarvis goes straight back to listening. The
**Mic** button mutes/unmutes the microphone instantly (also `M` or the tray's
*Mute mic* — and it silences the wake word too), and **Stop** (or `Space`) cuts a
reply off mid-sentence — or abandons a listen that is still waiting for you — and
in continuous chat it also ends the whole exchange.
With **Continuous chat** on (the default, `JARVIS_CHAT=0` for the old one-shot
behaviour), Jarvis **keeps the microphone open after each reply**: you answer, it
replies, and you just keep talking until you go quiet (60 s,
`JARVIS_CHAT_TIMEOUT`), say **"bye"/"stop"/"exit"**, press Stop, or mute. The
**Hey Jarvis** and **Continuous chat** menu items (in both the widget and the
tray menu) switch the wake word and the chat mode off and on while Jarvis runs.

**Desktop launcher.** `./run.sh` starts the GUI with the right virtualenv on
Linux/macOS, and `./venv/bin/python install_desktop.py` adds Jarvis to your
application menu with the glowing-orb icon (`--autostart` also starts it at
login; `--uninstall` removes exactly what it wrote). On Windows use `start.bat`
and `stop.bat`, or add Jarvis to the login items with
`main.py --autostart-enable` (which launches it through `pythonw.exe`, so no
console window appears). To keep it running after you close the terminal:

```bash
nohup ./run.sh >/tmp/jarvis.log 2>&1 &     # start in the background (Linux/macOS)
pkill -f "main.py --gui"                   # stop it again
```

### Voice commands

| You say | Handled by |
| --- | --- |
| "open telegram" / "open files" / "open terminal" / "open settings" | local router (fuzzy app match) |
| "open downloads" / "open my home folder" / "open ~/notes.txt" | local router (folder / path) |
| "close telegram" / "quit files" / "kill calculator" | local router (graceful close) |
| "what windows are open" / "list windows" | local router (`wmctrl`) |
| "open youtube" | local router |
| "play lo-fi on youtube" | local router (`yt-dlp`; only the song name is used) |
| "play kesariya" / "play the song kesariya" | local router (`yt-dlp`; plays it) |
| "play the second song" / "play this video" / "play that one" / "play it" | local router → **vision** (finds it on your screen) |
| "search cats on youtube" | Groq agent → YouTube search |
| "pause" / "resume" / "next song" / "stop the music" | local router (`playerctl`; `next`/`previous` fall back to the YouTube page) |
| "go back 10 seconds" / "skip forward 30 seconds" / "what's playing" | local router (`playerctl`) |
| "volume up" / "set volume to 40" / "mute" / "unmute" | local router (`pactl`) |
| "brightness up" / "set brightness to 40" | local router (`brightnessctl`) |
| "take a screenshot" / "lock the screen" | local router |
| "what time is it" / "what's the date" / "battery" / "system status" | local router |
| "copy" / "cut" / "paste" / "read the clipboard" | local router (Clipboard) |
| "help" / "what can you do offline" | local router (offline capability list) |
| "terminal ls -la" / "run the command df -h" | local router → **guarded terminal** |
| "shut down" / "restart" / "log out" / "suspend" | local router → **"Are you sure?"** |
| "what's on my screen?" / "read this" / "describe the screen" | local router → **vision** |
| "search for lo-fi music" / "look up the weather" / "latest python version" | Groq agent (live internet) |
| "click the login button" / "open the first result" | Groq agent → screen automation |
| "run ls in the terminal and show me the output" | Groq agent → terminal tool |
| "set a 5 minute timer" / "remind me to call mom in 10 minutes" | Groq agent → timers |

`close youtube` closes the *focused tab* (Ctrl+W) when possible, and never
closes the whole browser unless you say "close the browser" / "close brave".

---

## Terminal safety

Speech is unreliable, so a command must pass **three layers** in `shell.py` before
it runs:

1. **Refused outright.** Privilege escalation (`sudo`, `su`, `pkexec`, `doas`) and
   anything that can take the machine down: `shutdown`/`reboot`/`poweroff`/`halt`/
   `telinit`, `power off` in any spelling, `suspend`/`hibernate`, `systemctl`
   power verbs, `loginctl` session termination, session-logout binaries
   (`gnome-session-quit --power-off`, `xfce4-session-logout`, …), kernel `sysrq`/
   `kexec`, writes to `/sys/power/` or `/proc/sysrq-trigger`, `kill`/`pkill` of
   init/systemd, recursive force-deletes (`rm -rf` in every spelling), disk
   partitioning/formatting, `dd of=/dev/…`, fork bombs, piping *or chaining* a
   download into a shell, and account/credential changes. Quotes and backslashes
   are stripped before matching, so `s\hutdown` and `sh'utdown'` are caught too.
2. **Confirmed first.** Anything that mutates state — `rm`, `mv`, `chmod`, `kill`,
   package managers, `systemctl`, `ssh`/`scp`/`rsync`, `docker`, `git push`, and
   writing to a file with `>` — is read back to you and only runs after you say
   **"yes"** (a 15 s pending confirmation).
3. **Runs.** Everything else (`ls`, `cat`, `grep`, `df`, `git status`, …) runs with
   a timeout (`JARVIS_SHELL_TIMEOUT`) and a capped output size.

**The terminal can never shut down, restart or power off the device.** These rules
are enforced in code, not in the prompt.

---

## Safety

- **Protected, never touched:** Jarvis itself and its entire ancestor chain (so
  the terminal running it), the display server (`Xorg`/`Xwayland`), the session
  manager, `systemd`/`dbus`, and the desktop shell (`cinnamon`, `gnome-shell`,
  `plasmashell`, …).
- **Confirmation-gated:** shutdown, restart, logout and suspend; risky terminal
  commands; and destructive/financial screen-automation goals (delete, uninstall,
  format, buy, pay, send, …). Only a clear "yes" runs them; anything else cancels,
  and an unanswered request expires after ~15 s.
- **Weak matches:** if an app name is only a weak match, Jarvis asks
  "Did you mean <app>?" before acting.
- **Whitelist only:** the agent's tools and the router both call fixed functions in
  `desktop.py`, which delegates to fixed adapter methods in `platform_adapters/`
  (the `xdotool`/`wmctrl`/`pactl`/… wrappers). Screen actions are limited to
  move/click/type/key/scroll/wait. The only way to reach a shell is the guarded
  terminal above.

---

## Architecture

```
listen() ─► confirm.resolve() ─► try_local() ─hit─► speak()
 (voice.py)    (confirm.py)       (router.py)         (voice.py)
                                      │
                                     miss
                                      ▼
                               run_agent() ─► Groq (agent.py) ─► tools.py ─┬─► desktop.py  (apps, windows, media, screen)
                                      │                                     ├─► vision.py   (see / operate the screen)
                                      ▼                                     ├─► net.py      (search / wiki / weather)
                               memory.add() (memory.py)                     ├─► shell.py    (guarded terminal)
                                                                            └─► timers.py   (timers / reminders)
```

`main.handle()` is the single dispatch path: it resolves any pending
confirmation first, then tries the router, and only sends a miss to the agent.

### Module map

| File | Role |
| --- | --- |
| `config.py` | Loads `.env`; holds `__version__`, models, and the screen/shell/history limits. |
| `voice.py` | `listen()` (arecord + SpeechRecognition, `en-US`) and `speak()` (edge-tts + pygame). Also the runtime mic mute (`set_mic_muted`), the live level sink the GUI animates from, the TTS loudness envelope, and `stop_speaking()` / `stop_listening()` (the 30 s wait for speech is `JARVIS_LISTEN_TIMEOUT`). |
| `router.py` | `try_local()` — the offline fast path: power, media, volume, brightness, screenshot, lock, windows, clipboard, battery/status/time/date, screen reading, folders, YouTube, dictated commands and app open/close. Search verbs return `None` for the agent. Also holds the spoken `CAPABILITIES` list. |
| `desktop.py` | The **OS-agnostic facade**: fuzzy app matching, YouTube/media decisions and the protected-process rules, delegating every platform-specific operation to `platform_adapters`. Media control falls back to the browser tab's own keys when a page's MPRIS cannot skip tracks. |
| `platform_adapters/` | The Platform Adapter Pattern. `base.py` defines the `AudioController`/`WindowController`/`SystemController` interfaces; `common.py` holds the shared runner/process helpers; `linux/`, `windows/` and `macos/` implement them (`arecord`/`pactl`/`playerctl`/`wmctrl`/`xdotool`/`brightnessctl` on Linux, `sounddevice`/`pycaw`/`pywinctl` on Windows, `sounddevice`/`osascript`/`pywinctl` on macOS). `__init__.py` detects the OS and exports `Audio`, `Window` and `System`. |
| `install.sh` / `install.ps1` | One-line installers: create the venv, install `requirements.txt`, then the platform's helper tools. |
| `vision.py` | Screen capture, `look()` (ask about the screen) and `act()` (bounded perceive→act loop). |
| `net.py` | Keyless internet: DuckDuckGo search, page fetch, Wikipedia, weather. |
| `shell.py` | The guarded terminal (`run`, `explain_refusal`, `_is_risky`). |
| `timers.py` | In-process timers and reminders. |
| `confirm.py` | Pending "Are you sure?" state: yes runs, anything else cancels, 15 s expiry. |
| `agent.py` | Groq orchestration with an optional Ollama backend: response cache, token accounting, tool schemas from function signatures, English-only system instruction, bounded tool loop, long-term fact injection, 44 tools. |
| `tools.py` | The tool functions (see the list below). |
| `memory.py` | SQLite conversation history plus the persistent `user_facts` table for long-term personal memory. |
| `main.py` | Entry point and `handle()` dispatch (`--text`, `--offline`, `--local`, `--screen`, `--gui`, `--wake`, `--quit`, `--check`, `--version`). Enforces a single instance via `instance.py`. |
| `gui.py` | PyQt5 ChatGPT-Voice-style card + system tray; the listen/think/speak cycle runs on a `QThread` — and with continuous chat on, that thread loops turn after turn until you go quiet, say an exit word, press Stop or mute. The breathing `Orb` and 13-bar `Waveform` are driven by live mic levels / the TTS loudness envelope; the worker arms a `voice.BargeInMonitor` while speaking, so talking over a reply cuts it short and returns to listening. Frameless, **resizable** rounded card (corner `QSizeGrip` + edge dragging) whose ✕/close **hides to the tray** instead of quitting. Owns the wake listener's lifecycle (start/pause/resume/stop) and registers the optional global **Ctrl+Shift+J** / **Ctrl+Alt+J** wake hotkey (`pynput`). |
| `instance.py` | Single-instance lock (`~/.jarvis/jarvis.lock`) and wake socket (`~/.jarvis/jarvis.sock`). A second launch (or `main.py --wake`) signals the running GUI to wake and then exits, so there is only ever one Jarvis; `main.py --quit` closes it. POSIX uses `flock` + a Unix socket, Windows a named mutex + a loopback port, and both are released by the OS if the process is killed. |
| `icon_art.py` | The vector artwork for the app icon (charcoal tile, azure orb, stylised "J"), shared by the GUI, the tray and `gen_icon.py`. |
| `gen_icon.py` | Renders `assets/jarvis-{32,48,64,128,256}.png` from `icon_art.py`, headless and PyQt-only. |
| `wake.py` | Optional offline **"Hey Jarvis"** wake word (openWakeWord ONNX). `Detector` re-frames audio into 80 ms frames and applies the threshold; `Listener` streams the mic on a daemon thread, hands the device over before any listen cycle, and stays silent while the mic is muted. Importable without openWakeWord. |
| `doctor.py` | Readiness self-check. Prints a copy-paste install line for missing tools (never runs it), and the wake-word status. |
| `install_desktop.py` | Adds/removes the application-menu entry (and optionally autostart), pointing at this checkout and the regenerated icon. Writes only inside your own home directory; `--uninstall` is exact. |
| `run.sh` | Starts the GUI through the project's virtualenv without needing `cd`. |
| `assets/jarvis-*.png` | The glowing-orb/"J" app icon at 32-256 px, generated by `gen_icon.py`. |
| `test_mic.py` | Standalone mic diagnostic. |

### Tools (43)

Apps & windows: `fn_open_app`, `fn_close_app`, `fn_list_running`, `fn_list_windows`,
`fn_focus_window`, `fn_minimize_window`, `fn_maximize_window`.
Media: `fn_youtube`, `fn_media`, `fn_media_seek`, `fn_now_playing`, `fn_volume`,
`fn_brightness`. Screen: `fn_screenshot`, `fn_see_screen`, `fn_screen_do`,
`fn_click`, `fn_move_mouse`, `fn_type_text`, `fn_press_keys`, `fn_scroll`.
Internet: `fn_web_search`, `fn_web_lookup`, `fn_fetch_page`, `fn_wikipedia`,
`fn_weather`. Terminal: `fn_run_command`, `fn_run_in_terminal`.
System & files: `fn_open_path`, `fn_find_files`, `fn_clipboard`, `fn_notify`,
`fn_lock_screen`, `fn_system_status`, `fn_system_info`, `fn_kill_process`.
Notes & timers: `fn_save_note`, `fn_read_file`, `fn_set_timer`, `fn_set_reminder`.
Long-term memory: `fn_remember_fact`, `fn_recall_facts`, `fn_forget_fact`.

---

## Troubleshooting

- **Microphone problem / want to try commands instantly.** Type them instead of
  speaking (no mic needed):

  ```bash
  JARVIS_TEXT=1 ./venv/bin/python main.py     # add JARVIS_MUTE=1 to skip TTS
  ```

  and print a readiness report first with `./venv/bin/python doctor.py`.
- **No internet.** Everything offline still works. Force it with
  `./venv/bin/python main.py --offline`; say "what can you do offline" for the list.
- **`Listening…` repeats and nothing is recognised.** Run with diagnostics on:

  ```bash
  JARVIS_DEBUG=1 ./venv/bin/python main.py
  ```

  This prints the device, the RMS gate, the per-0.1 s chunk RMS (with state and
  calibrated `floor=`), the captured length/peak, and whether the outcome was a
  timeout, an unrecognised capture, or a network (`RequestError`) failure.
- **Input is clipping** (debug prints `peak at full scale (32768)`): lower the
  microphone gain in your system mixer.
- **Jarvis answers before you finish your sentence.** It thought you had stopped:
  a pause ended the phrase. Give yourself more room:

  ```bash
  JARVIS_SILENCE_END=3 JARVIS_PHRASE_LIMIT=30 ./venv/bin/python main.py
  ```

  and run with `JARVIS_DEBUG=1` to see the exact `state=ended reason=…`
  (`silence` = it thought you finished; `max_time`/`max_length` = the cap).
- **The screen flashes when Jarvis looks at it.** It shouldn't — capture uses
  `scrot`/`import`, never `gnome-screenshot`. If `scrot` and ImageMagick's
  `import` are both missing, install one of them.
- **Phrases never end / music keeps the gate open:** move the mic away from the
  speakers, or set `JARVIS_PTT=1` and end phrases by hand with Enter.
- **"playerctl/brightnessctl is not installed"**: the message (and
  `doctor.py`) prints the exact install command for you to copy-paste; Jarvis
  never runs it.
- **Screen control says it needs X11 / "not supported":** you are on Wayland.
- **"play …" opens YouTube but nothing starts, or "pause" says nothing is
  playing.** Browsers block autoplay until you interact with the site, and a
  paused tab publishes no media session at all. Jarvis checks `playerctl status`,
  taps play on the YouTube window itself and verifies (needs `xdotool` + X11);
  if it is still paused it says so instead of claiming it is playing. If you
  opened the tab by hand, click the video
  once and pause/next will work from then on. A browser's MPRIS cannot skip
  tracks (`CanGoNext = false` in Chromium/Brave), so `next`/`previous` are sent
  to the YouTube page as its own keyboard shortcuts instead.
- **A command was refused:** that is the terminal guard. It refuses `sudo`, every
  power/session command, and destructive operations by design; risky ones only run
  after you answer "yes".
- **Agent replies are cut off:** raise `MAX_OUTPUT_TOKENS` in `config.py`.
- **`RequestError` from STT:** speech-to-text needs network access; the local
  router fast-path still works offline.
- **"Hey Jarvis" does nothing.** Check `./venv/bin/python doctor.py`: it says
  whether the wake word is installed, listening or switched off, and prints the
  exact `pip install` line when it is missing. If it is installed but Jarvis never
  reacts, say the phrase a little closer to the mic, or lower the bar:
  `JARVIS_WAKE_THRESHOLD=0.35 ./venv/bin/python main.py --gui`. Run with
  `JARVIS_DEBUG=1` to see each detection and its score.
- **It wakes up when it shouldn't** (e.g. the TV says something similar): raise it,
  `JARVIS_WAKE_THRESHOLD=0.7`, add voice-activity gating with `JARVIS_WAKE_VAD=0.5`,
  or switch the wake word off entirely with `JARVIS_WAKE=0`.
- **The chime is annoying:** `JARVIS_WAKE_DING=0`.

---

## Security notes

- The router and agent only call fixed functions in `desktop.py`, `vision.py`,
  `net.py`, `shell.py` and `timers.py`. Jarvis **never runs `sudo`** (it may only
  *print* an install command for you to run yourself), and the only shell access
  is the guarded terminal described above.
- `fn_read_file` reads any user-supplied path and `fn_save_note` writes to
  `~/jarvis_notes.md` — there is no sandboxing. Review before exposing Jarvis to
  untrusted input.
- Screen actions are an explicit, whitelisted set (move/click/type/key/scroll);
  there is no path from a screenshot to arbitrary code execution.
- The optional wake word runs entirely on this machine (`openWakeWord`, ONNX): the
  microphone is only opened while Jarvis is idle, nothing is sent anywhere, and
  muting the mic stops it listening. It is your choice to install it, and
  `JARVIS_WAKE=0` (or the menu item) turns it off.
- Never commit `.env`, API keys or `TOKEN.txt`. `.gitignore` covers `.env`,
  `venv/`, caches, audio artifacts and token files.

## License

MIT © 2026 Jakir Hussain Khan. See [LICENSE](LICENSE).

The optional wake word uses [`openWakeWord`](https://github.com/dscripka/openWakeWord)
(Apache-2.0) with its pretrained models, which are licensed **CC-BY-NC-SA 4.0**
(non-commercial use only) by their authors. They are fetched by `pip`/at first run,
never bundled in this repository.
