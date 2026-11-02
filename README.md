![CI](https://github.com/jakirhussainkhan/jarvis-ai-voice-assistant/actions/workflows/ci.yml/badge.svg)

# JARVIS — Cross-Platform Voice Assistant

**Version 3.0.2**

> 🎙️ **What can I say?** Open the complete **[Voice Commands Guide](docs/voice_commands/COMMANDS.md)** — every app, media, YouTube, memory, screen, terminal, MCP and Python command, with examples.

JARVIS is a voice-controlled desktop assistant for **Linux, Windows and macOS**.
You speak a command; JARVIS either handles it **locally and offline** (a regex
router — no API call) or hands it to a **Groq-backed agent** that can call 44
native tools plus any configured **MCP** tools, then answers out loud in
English. Speech recognition and replies are **English-only by design**.

The desktop experience is a frameless **Iron Man-style HUD** with live
telemetry, transcript and activity rails, a command bar and a system tray. An
**80×80 MiniOrb** floats above the desktop, so JARVIS stays one click away when
the HUD is hidden. Say **"Hey Jarvis"** once the optional offline wake word is
installed, or use the global hotkey.

---

## Download and run (no Python needed)

Prebuilt apps are attached to the
[latest GitHub release](https://github.com/jakirhussainkhan/jarvis-ai-voice-assistant/releases/latest):

| Platform | Download | First launch |
| --- | --- | --- |
| Windows 10/11 | `JARVIS.exe` | Double-click it. If SmartScreen appears, choose **More info → Run anyway**. |
| Linux (Debian/Ubuntu) | `jarvis_3.0.2_amd64.deb` | Double-click it, or run `sudo apt install ./jarvis_3.0.2_amd64.deb`. |
| macOS 12+ (Intel or Apple Silicon) | `JARVIS.dmg` | Open it, drag **JARVIS** to **Applications**, then right-click the app and choose **Open** the first time. |

The macOS build is Intel; Apple Silicon runs it through Rosetta 2, which macOS
offers to install automatically the first time.

The app opens the HUD and asks for a free
[Groq API key](https://console.groq.com/keys) on first run. The key is stored
only in your own home folder (`~/.jarvis/.env`). Microphone permission is
requested automatically; see the platform guides for screen/accessibility
permissions.

The packaged Windows build bundles `ffmpeg.exe`, and the macOS DMG bundles a
static ffmpeg, so audio playback works without a separate install. The frozen
builds do **not** include the optional offline wake word: press
**Ctrl+Shift+J** (or **Ctrl+Alt+J**), click the Arc Reactor, or click the
MiniOrb to talk. The first launch unpacks roughly 150 MB, so the window can take
10–20 seconds to appear — do not double-click it twice.

> The macOS build is ad-hoc signed (without a paid Apple Developer ID), so
> Gatekeeper asks for a one-time confirmation. The Windows EXE is unsigned, so
> SmartScreen may do the same. These prompts are expected and safe to accept.

---

## What's new in 3.0.2

### Iron Man HUD and MiniOrb

- **The Iron Man HUD.** `gui.py` and `hud.py` provide a frameless command
  centre with the live Arc Reactor, CPU/RAM/disk telemetry, GitHub and inbox
  panels, transcript and activity rails, a command bar and a tray icon. Custom
  `QPainter` widgets draw the obsidian surfaces, cyan hairlines, reactor glow,
  sparklines and instrument-style gauges, while `QPropertyAnimation` drives the
  metric changes and the breathing states.
- **The MiniOrb.** A frameless, always-on-top 80×80 floating orb (with a 50 px
  reactor face) sits at the bottom-right of the desktop. Drag it anywhere;
  left-click opens the HUD, right-click offers **Show** and **Quit**. It is
  deliberately static while Idle and only breathes during Listening, Thinking
  and Speaking, so a resting JARVIS uses no animation CPU. Toggle it with
  **JARVIS_SHOW_MINI_ORB=0** or the tray/session menu.
- **Hotkey without stealing focus.** **Ctrl+Shift+J** (or **Ctrl+Alt+J**)
  toggles listening from any application without raising the HUD; only an
  explicit tray or MiniOrb click brings the window forward.

### Intelligence

- **MCP (Model Context Protocol).** `mcp_client.py` starts configured MCP
  servers as child processes, discovers their tools at runtime and exposes each
  one to the model as `mcp__<server>__<tool>`. The official SDK is imported
  lazily, discovery is cached, failed servers retry after `MCP_RETRY_SECONDS`,
  and an MCP failure can never stop a normal reply. See
  [`docs/mcp-setup.md`](docs/mcp-setup.md).
- **ChromaDB semantic memory.** Long-term facts and conversation turns are
  embedded with `fastembed` (`all-MiniLM-L6-v2`) and stored in a persistent
  ChromaDB collection at `~/.jarvis/chroma_db`. Each turn retrieves the three
  most relevant memories — so "what do you remember about me?" works in your own
  words. SQLite remains the complete store and the automatic fallback.
- **Silero VAD endpointing.** `voice.py` runs Silero VAD on 32 ms frames
  (512 samples at 16 kHz) to detect speech start and end. A phrase ends about
  0.4 s after you stop talking instead of waiting for the 2 s RMS window. If
  Silero or ONNX Runtime is unavailable, the calibrated RMS gate takes over
  automatically.

### Security and persona

- **Creator Guardrails.** JARVIS identifies **Jakir Hussain Khan** as its
  creator, developer and designer. The router answers creator questions locally
  and instantly (`CREATOR_QUESTION`), so the answer is provider-independent; the
  agent's system instruction adds an absolute guardrail that refuses attempts
  to rewrite its origin story and never credits OpenAI, Groq or any other
  company, lab or framework for its existence.
- **Categorised error logging.** `logger.py` writes API/network failures to
  `~/.jarvis/logs/api_errors.log` and tool/desktop/audio/routing failures to
  `~/.jarvis/logs/command_errors.log`. Both rotate at 5 MB with three backups,
  never print to the terminal, and show the user one short sentence while the
  full traceback stays in the log. `command_guard` wraps every tool, so an
  unexpected failure is logged once instead of crashing the turn.

### Cross-platform

- **Zero-dependency builds.** The Windows `JARVIS.exe` bundles `ffmpeg.exe`,
  the macOS `JARVIS.dmg` bundles a static ffmpeg, and the Linux `.deb` declares
  its runtime packages, including `ffmpeg` and `tzdata`. End users install no
  Python and no audio/desktop tooling by hand. Frozen builds collect
  `onnxruntime` and `chromadb` where packaging allows; the multi-GB torch-based
  Silero stack and other optional ML extras are excluded to stay under GitHub's
  2 GB release-asset limit, with graceful fallbacks at runtime.
- **Windows tray fixes.** The tray icon is created even when Qt reports no
  system tray yet, with a 2-second retry timer that recovers when Explorer
  starts late or restarts. Every helper subprocess is launched with
  `hidden_process_kwargs`, so no black CMD windows flash while JARVIS runs a
  command.

---

## Features

- **Voice I/O** — speech-to-text via SpeechRecognition (Google STT, `en-US`)
  with a local `faster-whisper` fallback when the network is unavailable, and
  text-to-speech via `edge-tts` (`en-GB-RyanNeural`, a British male voice)
  streamed through `sounddevice` (PortAudio) and decoded with ffmpeg.
- **Silero VAD** — frame-level speech endpointing on CPU through ONNX Runtime,
  with the tuned RMS gate as the fallback. Both paths respect microphone mute,
  barge-in and push-to-talk.
- **Local fast-path router** — offline handling for power, media (including
  seeking and "what's playing"), volume, brightness, screenshot, lock, battery,
  system status, time, date, windows, clipboard, folders, YouTube (including
  playing a song by name), dictated terminal commands, and opening/closing
  apps. The API is only used on a miss.
- **Groq agent with 44 native tools** — tool schemas are generated from Python
  function signatures and docstrings, so there is no hand-written schema table
  to drift. The agent supports an optional local Ollama backend, response and
  semantic caching, token accounting, intent-based tool pruning, a bounded tool
  loop, long-term fact injection and MCP tools.
- **MCP integration** — any stdio MCP server can add tools at runtime. Servers
  are declared in `MCP_SERVERS`; broken configuration or a missing SDK is
  logged and simply contributes no tools.
- **ChromaDB semantic memory** — SQLite stores the full history and
  `user_facts` table; ChromaDB plus `fastembed` provides semantic retrieval.
  `fn_remember_fact`, `fn_recall_facts` and `fn_forget_fact` manage durable
  personal facts, and the agent receives relevant memories before each prompt.
- **Real-time screen vision** — reads the screen ("what does this error say?",
  "summarise this page") and operates it ("click Save", "type my name in the
  box", or pick something you are pointing at) through whitelisted
  mouse/keyboard actions only. Screenshots are taken silently with `scrot` or
  ImageMagick `import`.
- **Internet intelligence** — `fn_web_lookup` (live search), `fn_fetch_page`,
  `fn_wikipedia` and `fn_weather`. No API keys are required for any of them.
- **Guarded terminal and CodeAct** — `fn_run_command` (hidden, output
  returned), `fn_run_in_terminal` (visible) and `fn_execute_python` (a guarded
  CodeAct subprocess with an AST allow/deny pass and a hard timeout). Privilege
  escalation and every power/session command are refused in code; anything that
  mutates state is confirmed first.
- **Desktop control** — open any installed app (fuzzy match over `.desktop`
  files), graceful close, list/focus/minimise/maximise windows,
  click/type/scroll, YouTube play/search, media, volume, brightness,
  screenshots, clipboard, notifications, file search, notes, timers and
  reminders.
- **Safety everywhere** — protected-process list, "Did you mean <app>?" on weak
  matches, and an "Are you sure?" confirmation (15 s, "yes"-only) before
  shutdown/restart/logout/suspend, before risky terminal commands, and before
  destructive screen-automation goals.
- **Iron Man HUD and MiniOrb** — the command-centre window and the floating orb
  share one voice pipeline and one state machine. The HUD has a live reactor,
  telemetry, transcript/activity panels, a command bar, settings, mic mute,
  Stop, barge-in, minimise/maximise/restore and silent close-to-tray.
- **Optional offline wake word** — say **"Hey Jarvis"** instead of clicking.
  The GUI uses `openWakeWord`'s pretrained ONNX model: no network, no account,
  no API key, roughly 1–2% of one CPU core. The wake listener and `voice.listen`
  never hold the microphone at the same time, and muting the mic stops the wake
  word too.
- **Usable without a microphone** — `JARVIS_TEXT=1 ./venv/bin/python main.py`
  (or `main.py --text`) takes typed commands through the same router/agent path.
- **Readiness report** — `./venv/bin/python doctor.py` (or `main.py --check`)
  prints the session type, which tools are installed and what each missing one
  costs, the model and vision model, the app count, and a copy-paste install
  line for whatever is missing (for you to run — JARVIS never runs it).
- **Categorised logging** — separate rotating API and command error logs keep
  unrelated failures apart without spraying tracebacks into the console.
- **Tests** — **672 passed, 3 skipped** (675 collected) with `pytest`, covering
  routing, app matching, the protected list, confirmation, terminal guardrails,
  screen routing, clipboard fallbacks, offline behaviour, internet tools, vision
  parsing, timers, YouTube, text mode, the doctor, the HUD and MiniOrb
  (offscreen), the wake word, MCP, ChromaDB memory, Silero VAD, categorised
  logging, the desktop launcher and the real router→agent dispatch path.

---

## Requirements

- **Linux, Windows or macOS**, with a working microphone.
- Python 3.10+ (developed on 3.12).
- A Groq API key for the agent, internet tools and screen vision.

Audio capture and playback come from the platform adapter: **Linux** uses
`arecord`/`aplay` (`alsa-utils`), while **Windows and macOS** use `sounddevice`
(installed from `requirements.txt`). Volume is `pactl` on Linux, `pycaw` on
Windows and `osascript` on macOS.

Optional desktop tools — JARVIS degrades gracefully when these are missing, but
these features need them. On Linux install them with your system package
manager; `doctor.py` prints the exact `sudo apt install …` line for whatever is
missing (JARVIS never runs it):

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
| `scrot` / ImageMagick `import` | screenshots (preferred — no screen flash) |
| `convert` / `ffmpeg` | shrinking screenshots for the vision model |
| `notify-send` | desktop notifications |
| `xdg-open` | opening files, folders and URLs |
| `xclip` / `xsel` | reading the clipboard (falls back to Qt/PyQt5) |
| `cinnamon-screensaver-command` / `loginctl` | lock screen |

> On **Wayland**, `wmctrl` and `xdotool` do not work. Screen automation and
> window control report that plainly; app control falls back to process-based
> closing and media to `playerctl`.

### Optional Python extras

`requirements.txt` already includes the optional runtime integrations:
`mcp`, `chromadb`, `fastembed`, `silero-vad`, `onnxruntime`, `faster-whisper`,
`pynput`, `sounddevice` and `pywinctl`. Each is imported lazily:

- missing `mcp` → no MCP tools;
- missing `chromadb`/`fastembed` → SQLite fact retrieval;
- missing `silero-vad`/`onnxruntime` → calibrated RMS endpointing;
- missing `faster-whisper` → online STT only;
- missing `pynput` → no global hotkey, but the app still starts.

**Optional: the "Hey Jarvis" wake word.** It is deliberately not in
`requirements.txt` because it is a few hundred MB of ONNX runtime and only the
GUI uses it:

```bash
./venv/bin/pip install openwakeword onnxruntime   # ~1-2% CPU, fully offline
```

On Windows the same install is:

```powershell
.\venv\Scripts\python.exe -m pip install openwakeword onnxruntime
```

The first run downloads the pretrained model (a one-time network fetch); after
that it never touches the network. `doctor.py` prints this exact line for you
when the wake word is missing, and JARVIS itself never installs anything.

---

## Installation

The installers create `venv/`, install `requirements.txt`, and add the
platform's audio and desktop helper tools. Expect about 3 minutes on broadband.

### 1. Clone the repository

```bash
git clone https://github.com/jakirhussainkhan/jarvis-ai-voice-assistant.git
cd jarvis-ai-voice-assistant
```

### 2. Run your platform's one-liner

**Linux (curl):**

```bash
curl -fsSL https://raw.githubusercontent.com/jakirhussainkhan/jarvis-ai-voice-assistant/main/install.sh | bash
```

**Windows (PowerShell `irm`):**

```powershell
irm https://raw.githubusercontent.com/jakirhussainkhan/jarvis-ai-voice-assistant/main/install.ps1 | iex
```

**macOS (curl):**

```bash
curl -fsSL https://raw.githubusercontent.com/jakirhussainkhan/jarvis-ai-voice-assistant/main/install.sh | bash
```

Each installer is a small shell/PowerShell script in this repository, so you can
[read `install.sh`](install.sh) or [read `install.ps1`](install.ps1) before
running it.

### 3. Add your Groq API key

Get a free key from [console.groq.com/keys](https://console.groq.com/keys), then:

**Linux / macOS:**

```bash
echo "GROQ_API_KEY=your-key-here" > .env
```

**Windows PowerShell:**

```powershell
"GROQ_API_KEY=your-key-here" | Out-File -Encoding ascii .env
```

The packaged GUI also asks for the key on first run and stores it in
`~/.jarvis/.env`.

### 4. Check readiness and start

```bash
./venv/bin/python doctor.py          # Linux / macOS
```

```powershell
.\venv\Scripts\python.exe main.py --check   # Windows
```

Then follow [How to Run](#how-to-run) below.

| Platform | Detailed guide | Prebuilt download |
| --- | --- | --- |
| **Linux** | [Linux Instructions](docs/install-linux.md) | `jarvis_3.0.2_amd64.deb` |
| **Native Windows** | [Windows Instructions](docs/install-windows.md) | `JARVIS.exe` |
| **macOS** | [macOS Instructions](docs/install-macos.md) | `JARVIS.dmg` |
| **All releases** | [Latest release](https://github.com/jakirhussainkhan/jarvis-ai-voice-assistant/releases/latest) | — |

Platform-specific notes (WSL2 setup, native-Windows scheduled-task service,
desktop prerequisites, manual install) are covered in the three guides above.

---

## Configuration

All configuration lives in `config.py` and can be overridden from `.env`. See
[`.env.example`](.env.example) for a commented starting point.

| Variable | Default | Purpose |
| --- | --- | --- |
| `GROQ_API_KEY` | *(required)* | Groq API key, `gsk_…` |
| `GROQ_BASE_URL` | `https://api.groq.com/openai/v1` | OpenAI-compatible endpoint |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Main model ID (must support tools) |
| `GROQ_FALLBACK_MODEL` | `llama3-8b-8192` | Fallback model when the main one is unavailable |
| `GROQ_VISION_MODEL` | `qwen/qwen3.8-27b` | Multimodal model for screen vision |
| `USE_LOCAL_LLM` | `False` | Send agent requests to local Ollama instead of Groq |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible Ollama endpoint |
| `OLLAMA_MODEL` | `llama3` | Model pulled in Ollama |
| `MCP_SERVERS` | *(unset)* | JSON object of stdio MCP servers; see [MCP setup](docs/mcp-setup.md) |
| `MCP_TIMEOUT` | `15` | Seconds allowed for MCP startup and each tool call |
| `MCP_RETRY_SECONDS` | `30` | Delay before retrying a failed MCP server |
| `JARVIS_SEMANTIC_MEMORY` | `1` | Enable ChromaDB semantic memory |
| `JARVIS_CHROMA_DB` | `~/.jarvis/chroma_db` | Persistent ChromaDB directory |
| `JARVIS_SILERO_VAD` | *(on if installed)* | `0` forces the calibrated RMS endpointing |
| `JARVIS_VAD_THRESHOLD` | `0.5` | Silero speech-probability threshold |
| `JARVIS_VAD_START_MS` | `64` | Consecutive speech needed to start a phrase |
| `JARVIS_VAD_END_MS` | `400` | Consecutive silence needed to end a phrase |
| `JARVIS_SHOW_MINI_ORB` | `1` | `0` hides the floating 80×80 MiniOrb |
| `JARVIS_LOG_DIR` | `~/.jarvis/logs` | Directory for the categorised error logs |
| `JARVIS_GITHUB_TOKEN` | *(unset)* | Optional token for the HUD GitHub panel |
| `JARVIS_SCREEN_MAX_WIDTH` | `1280` | Downscale width for vision screenshots |
| `JARVIS_SCREEN_STEPS` | `6` | Max perceive→act steps per screen goal |
| `JARVIS_SHELL_TIMEOUT` | `20` | Seconds before a terminal command is killed |
| `JARVIS_SHELL_MAX_OUTPUT` | `4000` | Output characters returned to the model |
| `JARVIS_OFFLINE` | *(unset)* | `1` disables the agent (router only) |
| `JARVIS_MIC_DEVICE` | `default` | ALSA PCM name for `arecord -D` |
| `JARVIS_LISTEN_TIMEOUT` | `30` | Seconds to wait for you to start speaking |
| `JARVIS_SILENCE_END` | `2.0` | RMS-mode pause that ends the phrase |
| `JARVIS_PHRASE_LIMIT` | `15` | Max seconds of a single utterance |
| `JARVIS_DEBUG` | *(unset)* | `1` prints per-chunk RMS / capture diagnostics |
| `JARVIS_PTT` | *(unset)* | `1` for push-to-talk (Enter to start/stop) |
| `JARVIS_TEXT` | *(unset)* | `1` to type commands instead of speaking |
| `JARVIS_MUTE` | *(unset)* | `1` to skip text-to-speech (replies still print) |
| `JARVIS_TTS_VOICE` | `en-GB-RyanNeural` | Spoken voice (any edge-tts name) |
| `JARVIS_CHAT` | *(on)* | `0` turns continuous voice conversation off |
| `JARVIS_CHAT_TIMEOUT` | `60` | Seconds of silence that end a continuous session |
| `JARVIS_BARGE_THRESHOLD` | `1200` | RMS level that counts as talking over a reply |
| `JARVIS_WAKE` | *(on if installed)* | `0` disables the "Hey Jarvis" wake word |
| `JARVIS_WAKE_MODEL` | `hey jarvis` | Wake phrase/model name (`alexa`, `hey_mycroft`, …) |
| `JARVIS_WAKE_THRESHOLD` | `0.5` | Score (0-1) a frame needs to count |
| `JARVIS_WAKE_DING` | *(on)* | `0` silences the "I heard you" chime |
| `JARVIS_WAKE_VAD` | *(off)* | Voice-activity threshold to ignore non-speech noise |

**Optional local LLM:** install and start [Ollama](https://ollama.com), pull a
model (`ollama pull llama3`), then set `USE_LOCAL_LLM=True` in `.env` or pass
`--local` for one session. JARVIS uses Ollama's OpenAI-compatible `/v1`
endpoint, so no extra Python package is needed. With the flag unset, Ollama is
never contacted during normal Groq operation.

---

## How to Run

After installation, start the graphical HUD (the normal way to use JARVIS):

```bash
./run.sh          # Linux / macOS
```

```powershell
.\start.bat       # Windows (no console window)
```

The HUD opens on screen and adds a tray icon plus the MiniOrb. Click the Arc
Reactor or the MiniOrb — or say **"Hey Jarvis"**, press **Ctrl+Shift+J** /
**Ctrl+Alt+J**, or use the tray menu — to talk. Closing the HUD hides it to the
tray; use **Quit** in the tray menu or `main.py --quit` to stop it.

You can also run the terminal voice loop or the individual tools:

```bash
./run.sh                                     # the HUD (same as above)
./venv/bin/python main.py                    # terminal voice loop
./venv/bin/python main.py --text             # type commands instead of speaking
./venv/bin/python main.py --offline          # router only (no network)
./venv/bin/python main.py --local            # this session uses local Ollama
./venv/bin/python main.py --check            # readiness report (same as doctor.py)
./venv/bin/python main.py --version          # print the version
./venv/bin/python main.py --help             # every option, with a short description
./venv/bin/python main.py --screen "what is on my screen?"   # one screen answer
./venv/bin/python main.py --gui              # HUD + MiniOrb + system tray
./venv/bin/python main.py --wake             # wake the running JARVIS (or start it)
./venv/bin/python main.py --quit             # stop the running JARVIS
./venv/bin/python install_desktop.py         # add JARVIS to the app menu
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

**HUD controls.** Left-click the Arc Reactor, the MiniOrb or the tray icon to
talk. Press **Ctrl+Shift+J** or **Ctrl+Alt+J** from any application to toggle
listening without raising the window (the second press stops the current turn).
Drag the HUD to move it, drag an edge/corner or the grip to resize, double-click
the title bar to maximise/restore, right-click for the menu. `Esc`, **✕** or the
window close hide it to the tray; only **Quit** exits. The MiniOrb is a separate
always-on-top window: drag it anywhere, left-click to open the HUD, right-click
for **Show**/**Quit**. Launching JARVIS twice is safe: the second launch wakes
the running one and exits, so there is only ever one tray icon (a lock file at
`~/.jarvis/jarvis.lock`).

**Barge-in.** Start talking while JARVIS speaks and it stops immediately and
returns to listening. Echo is rejected with a high, sustained RMS threshold;
`JARVIS_BARGE_THRESHOLD`, `JARVIS_BARGE_HOLD` and `JARVIS_BARGE_IN=0` tune it,
and push-to-talk (`JARVIS_PTT=1`) disables it.

**Continuous chat.** With **Continuous chat** on (the default; `JARVIS_CHAT=0`
for one-shot behaviour), JARVIS keeps the microphone open after each reply.
It stops when you go quiet (60 s, `JARVIS_CHAT_TIMEOUT`), say
**"bye"/"stop"/"exit"**, press Stop, or mute. The **Hey Jarvis**, **MiniOrb**
and **Continuous chat** menu items switch those features while JARVIS runs.

**Desktop launcher.** `./run.sh` starts the GUI with the right virtualenv on
Linux/macOS, and `./venv/bin/python install_desktop.py` adds JARVIS to your
application menu with the Arc Reactor icon (`--autostart` also starts it at
login; `--uninstall` removes exactly what it wrote). On Windows use `start.bat`
and `stop.bat`, or add JARVIS to the login items with
`main.py --autostart-enable`.

---

## Voice commands

📖 **[Open the complete Voice Commands Guide →](docs/voice_commands/COMMANDS.md)**
It covers app control, system telemetry, media and YouTube, memory, web and
vision, terminal dictation, CodeAct Python, timers, MCP and confirmations.

A quick sample:

| You say | Handled by |
| --- | --- |
| "open telegram" / "open files" / "open terminal" / "open settings" | local router (fuzzy app match) |
| "open downloads" / "open my home folder" / "open ~/notes.txt" | local router (folder / path) |
| "close telegram" / "quit files" / "kill calculator" | local router (graceful close) |
| "what windows are open" / "list windows" | local router (`wmctrl`) |
| "open youtube" | local router |
| "play lo-fi on youtube" | local router (`yt-dlp`) |
| "play kesariya" / "play the song kesariya" | local router (`yt-dlp`; resolves the title and asks before playing) |
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
| "run this Python script" | Groq agent → **guarded CodeAct** |
| "shut down" / "restart" / "log out" / "suspend" | local router → **"Are you sure?"** |
| "what's on my screen?" / "read the screen" / "describe the screen" | local router → **vision** |
| "search for lo-fi music" / "look up the weather" / "latest python version" | Groq agent (live internet) |
| "click the login button" | Groq agent → screen automation |
| "open the first result" | local router → **vision** |
| "run ls in the terminal and show me the output" | Groq agent → terminal tool |
| "set a 5 minute timer" / "remind me to call mom in 10 minutes" | Groq agent → timers |
| "remember my name is Jakir" / "what do you remember about me?" | Groq agent → semantic memory |
| "who created you?" / "who is Jakir?" | local router → Creator Guardrails |
| "use the filesystem MCP server to list my documents" | Groq agent → MCP tool |
| "turn off the mic" / "turn on the mic" | local router (microphone mute) |

`close youtube` closes the *focused tab* (Ctrl+W) when possible, and never
closes the whole browser unless you say "close the browser" / "close brave".

---

## Terminal safety

Speech is unreliable, so a command must pass **three layers** in `shell.py`
before it runs:

1. **Refused outright.** Privilege escalation (`sudo`, `su`, `pkexec`, `doas`)
   and anything that can take the machine down: `shutdown`/`reboot`/`poweroff`/
   `halt`/`telinit`, `power off` in any spelling, `suspend`/`hibernate`,
   `systemctl` power verbs, `loginctl` session termination, session-logout
   binaries (`gnome-session-quit --power-off`, `xfce4-session-logout`, …),
   kernel `sysrq`/`kexec`, writes to `/sys/power/` or
   `/proc/sysrq-trigger`, `kill`/`pkill` of init/systemd, recursive
   force-deletes (`rm -rf` in every spelling), disk partitioning/formatting,
   `dd of=/dev/…`, fork bombs, piping *or chaining* a download into a shell,
   and account/credential changes. Quotes and backslashes are stripped before
   matching, so `s\hutdown` and `sh'utdown'` are caught too.
2. **Confirmed first.** Anything that mutates state — `rm`, `mv`, `chmod`,
   `kill`, package managers, `systemctl`, `ssh`/`scp`/`rsync`, `docker`,
   `git push`, and writing to a file with `>` — is read back to you and only
   runs after you say **"yes"** (a 15 s pending confirmation).
3. **Runs.** Everything else (`ls`, `cat`, `grep`, `df`, `git status`, …) runs
   with a timeout (`JARVIS_SHELL_TIMEOUT`) and a capped output size.

**The terminal can never shut down, restart or power off the device.** These
rules are enforced in code, not in the prompt.

---

## Safety

- **Protected, never touched:** JARVIS itself and its entire ancestor chain (so
  the terminal running it), the display server (`Xorg`/`Xwayland`), the session
  manager, `systemd`/`dbus`, and the desktop shell (`cinnamon`, `gnome-shell`,
  `plasmashell`, …).
- **Confirmation-gated:** shutdown, restart, logout and suspend; risky terminal
  commands; and destructive/financial screen-automation goals (delete,
  uninstall, format, buy, pay, send, …). Only a clear "yes" runs them; anything
  else cancels, and an unanswered request expires after ~15 s.
- **Weak matches:** if an app name is only a weak match, JARVIS asks
  "Did you mean <app>?" before acting.
- **Whitelist only:** the agent's tools and the router both call fixed functions
  in `desktop.py`, which delegates to fixed adapter methods in
  `platform_adapters/`. Screen actions are limited to move/click/type/key/scroll.
  The only way to reach a shell is the guarded terminal above.
- **Creator Guardrails:** creator questions are answered by a local regex
  before any model call, and the agent prompt forbids crediting another
  company, lab or framework for JARVIS's existence.
- **MCP servers are separate processes** with the same permissions as your
  user. Only configure servers you trust.

---

## Architecture

```
listen() ─► confirm.resolve() ─► try_local() ─hit─► speak()
 (voice.py)    (confirm.py)       (router.py)         (voice.py)
    │                                 │
    │ Silero VAD / RMS                miss
    ▼                                 ▼
 microphone                    run_agent() ─► Groq / Ollama (agent.py)
                                      │
                                      ├─► tools.py ─┬─► desktop.py  (apps, windows, media, screen)
                                      │             ├─► vision.py   (see / operate the screen)
                                      │             ├─► net.py      (search / wiki / weather)
                                      │             ├─► shell.py    (guarded terminal)
                                      │             └─► timers.py   (timers / reminders)
                                      ├─► mcp_client.py (external MCP tools)
                                      └─► memory.py    (SQLite + ChromaDB semantic memory)

 gui.py + hud.py ─► JarvisWorker (QThread) ─► main.handle() ─► the same router/agent path
        │
        └─► MiniOrbWidget, system tray, global hotkey, wake listener, settings
```

`main.handle()` is the single dispatch path: it resolves any pending
confirmation first, then tries the router, and only sends a miss to the agent.

### Module map

| File | Role |
| --- | --- |
| `config.py` | Loads `.env`; holds `__version__` (`3.0.2`), models, feature flags and limits. |
| `voice.py` | `listen()` (arecord/sounddevice + SpeechRecognition, `en-US`, with a local `faster-whisper` fallback) and `speak()` (edge-tts streamed through sounddevice). Contains the Silero VAD endpointing, the calibrated RMS fallback, runtime mic mute, the live level sink and the TTS loudness envelope. |
| `router.py` | `try_local()` — the offline fast path for power, media, volume, brightness, screenshot, lock, windows, clipboard, battery/status/time/date, screen reading, folders, YouTube, dictated commands, app open/close and Creator Guardrail answers. Search verbs return `None` for the agent. |
| `desktop.py` | The OS-agnostic facade: fuzzy app matching, YouTube/media decisions and protected-process rules, delegating every platform-specific operation to `platform_adapters`. |
| `platform_adapters/` | The Platform Adapter Pattern. `base.py` defines the `AudioController`/`WindowController`/`SystemController` interfaces; `common.py` holds the shared runner and `hidden_process_kwargs` helpers; `linux/`, `windows/` and `macos/` implement them. |
| `hud.py` | Presentation-only HUD widgets: obsidian panels, Arc Reactor mark, system telemetry, GitHub/inbox panels, transcript/activity rails, command bar and title bar, all painted with `QPainter`. Never imported by backend modules. |
| `gui.py` | The PyQt5 window and application shell: `JarvisWidget`, `JarvisWorker` (`QThread`), `MiniOrbWidget`, `JarvisTray`, `SettingsDialog` and the global hotkey. Owns the voice pipeline, wake listener, continuous chat, barge-in, mic mute, Stop and close-to-tray. |
| `agent.py` | Groq orchestration with an optional Ollama backend: response cache, semantic cache, token accounting, tool schemas from function signatures, English-only system instruction with Creator Guardrails, bounded tool loop, MCP tools and long-term fact injection. |
| `tools.py` | The 44 native tool functions, guarded by `logger.command_guard`. |
| `mcp_client.py` | Optional MCP client: parses `MCP_SERVERS`, starts stdio servers, discovers and exposes `mcp__<server>__<tool>`, caches discovery and retries failed servers. |
| `memory.py` | SQLite conversation history and persistent `user_facts`, plus lazy ChromaDB semantic indexing/retrieval with SQLite fallback. |
| `semantic_cache.py` | Semantic answer cache using `fastembed` (`all-MiniLM-L6-v2`) with an exact-match fallback. |
| `logger.py` | Categorised rotating logs: `api_errors.log` and `command_errors.log` under `~/.jarvis/logs`, plus `command_guard` for tools. |
| `main.py` | Entry point and `handle()` dispatch (`--text`, `--offline`, `--local`, `--screen`, `--gui`, `--wake`, `--quit`, `--check`, `--version`). Enforces a single instance via `instance.py`. |
| `instance.py` | Single-instance lock (`~/.jarvis/jarvis.lock`) and wake socket, so there is only ever one JARVIS. |
| `icon_art.py` / `gen_icon.py` | Render the canonical Arc Reactor SVG and regenerate launcher PNGs/Windows ICO from it. |
| `wake.py` | Optional offline "Hey Jarvis" wake word (openWakeWord ONNX) with mic hand-over and mute awareness. |
| `doctor.py` | Readiness self-check and copy-paste install hints. |
| `install_desktop.py` | Application-menu entry and optional autostart; writes only inside your home directory. |
| `build_deb.sh` / `build_exe.ps1` / `build_mac.sh` / `jarvis.spec` | Zero-dependency packaging for Linux, Windows and macOS. |
| `assets/jarvis-reactor-source.svg` | The sole tracked app-icon artwork; generated PNG/ICO variants are recreated by builds. |
| `test_mic.py` | Standalone mic diagnostic. |

### Tools (44 native, plus MCP)

Apps & windows: `fn_open_app`, `fn_close_app`, `fn_list_running`, `fn_list_windows`,
`fn_focus_window`, `fn_minimize_window`, `fn_maximize_window`.
Media: `fn_youtube`, `fn_media`, `fn_media_seek`, `fn_now_playing`, `fn_volume`,
`fn_brightness`. Screen: `fn_screenshot`, `fn_see_screen`, `fn_screen_do`,
`fn_click`, `fn_move_mouse`, `fn_type_text`, `fn_press_keys`, `fn_scroll`.
Internet: `fn_web_search`, `fn_web_lookup`, `fn_fetch_page`, `fn_wikipedia`,
`fn_weather`. Terminal & CodeAct: `fn_run_command`, `fn_run_in_terminal`,
`fn_execute_python`.
System & files: `fn_open_path`, `fn_find_files`, `fn_clipboard`, `fn_notify`,
`fn_lock_screen`, `fn_system_status`, `fn_system_info`, `fn_kill_process`.
Notes & timers: `fn_save_note`, `fn_read_file`, `fn_set_timer`, `fn_set_reminder`.
Long-term memory: `fn_remember_fact`, `fn_recall_facts`, `fn_forget_fact`.

MCP tools are added at runtime and named `mcp__<server>__<tool>`.

---

## Troubleshooting

- **Microphone problem / want to try commands instantly.** Type them instead of
  speaking (no mic needed):

  ```bash
  JARVIS_TEXT=1 ./venv/bin/python main.py     # add JARVIS_MUTE=1 to skip TTS
  ```

  and print a readiness report first with `./venv/bin/python doctor.py`.
- **No internet.** Everything offline still works. Force it with
  `./venv/bin/python main.py --offline`; say "what can you do offline" for the
  list.
- **`Listening…` repeats and nothing is recognised.** Run with diagnostics on:

  ```bash
  JARVIS_DEBUG=1 ./venv/bin/python main.py
  ```

  This prints the device, the RMS gate, the per-chunk RMS (with state and
  calibrated `floor=`), the captured length/peak, and whether the outcome was a
  timeout, an unrecognised capture, or a network (`RequestError`) failure.
- **Input is clipping** (debug prints `peak at full scale (32768)`): lower the
  microphone gain in your system mixer.
- **JARVIS answers before you finish your sentence.** It thought you had
  stopped. Give yourself more room:

  ```bash
  JARVIS_SILENCE_END=3 JARVIS_PHRASE_LIMIT=30 ./venv/bin/python main.py
  ```

  With Silero VAD installed, tune `JARVIS_VAD_END_MS` instead (default 400 ms).
- **Silero VAD is not being used.** Check `command_errors.log`; if the model
  could not load, install `silero-vad` and `onnxruntime` (or keep the RMS
  fallback with `JARVIS_SILERO_VAD=0`).
- **The screen flashes when JARVIS looks at it.** It shouldn't — capture uses
  `scrot`/`import`, never `gnome-screenshot`. If both are missing, install one.
- **Phrases never end / music keeps the gate open:** move the mic away from the
  speakers, or set `JARVIS_PTT=1` and end phrases by hand with Enter.
- **"playerctl/brightnessctl is not installed"**: the message and `doctor.py`
  print the exact install command for you to copy-paste; JARVIS never runs it.
- **Screen control says it needs X11 / "not supported":** you are on Wayland.
- **"play …" opens YouTube but nothing starts, or "pause" says nothing is
  playing.** Browsers block autoplay until you interact with the site, and a
  paused tab publishes no media session at all. JARVIS checks `playerctl
  status`, taps play on the YouTube window itself and verifies (needs `xdotool`
  + X11); if it is still paused it says so instead of claiming it is playing.
  A browser's MPRIS cannot skip tracks (`CanGoNext = false` in
  Chromium/Brave), so `next`/`previous` are sent to the YouTube page as its own
  keyboard shortcuts instead.
- **A command was refused:** that is the terminal guard. It refuses `sudo`,
  every power/session command, and destructive operations by design; risky ones
  only run after you answer "yes".
- **Agent replies are cut off:** raise `MAX_OUTPUT_TOKENS` in `config.py`.
- **`RequestError` from STT:** speech-to-text needs network access unless
  `faster-whisper` is installed; the local router fast-path still works offline.
- **"Hey Jarvis" does nothing.** Check `./venv/bin/python doctor.py`: it says
  whether the wake word is installed, listening or switched off, and prints the
  exact `pip install` line when it is missing. If it is installed but JARVIS
  never reacts, say the phrase a little closer to the mic, or lower the bar:
  `JARVIS_WAKE_THRESHOLD=0.35 ./venv/bin/python main.py --gui`. Run with
  `JARVIS_DEBUG=1` to see each detection and its score.
- **It wakes up when it shouldn't** (e.g. the TV says something similar): raise
  it, `JARVIS_WAKE_THRESHOLD=0.7`, add voice-activity gating with
  `JARVIS_WAKE_VAD=0.5`, or switch the wake word off with `JARVIS_WAKE=0`.
- **The chime is annoying:** `JARVIS_WAKE_DING=0`.
- **The MiniOrb is distracting:** set `JARVIS_SHOW_MINI_ORB=0` or uncheck it in
  the tray/session menu.
- **The Windows tray icon is missing after an Explorer restart.** v3.0.2
  recreates the tray and retries for 30 seconds; if it still does not appear,
  quit and relaunch JARVIS.
- **No MCP tools appear.** Validate that `MCP_SERVERS` is a JSON object in
  `.env`, install the official `mcp` SDK in the same virtualenv, and check
  `command_errors.log` for `MCP server '…' failed to start`. The first `npx`
  run may need extra time; increase `MCP_TIMEOUT`. See
  [`docs/mcp-setup.md`](docs/mcp-setup.md).
- **Memory recall is keyword-only.** ChromaDB or `fastembed` failed to load;
  the full traceback is in `command_errors.log`, and SQLite retrieval keeps
  working.
- **Where are the logs?** `~/.jarvis/logs/api_errors.log` and
  `~/.jarvis/logs/command_errors.log` (override with `JARVIS_LOG_DIR`). Each
  rotates at 5 MB and keeps three backups.

---

## Security notes

- The router and agent only call fixed functions in `desktop.py`, `vision.py`,
  `net.py`, `shell.py` and `timers.py`. JARVIS **never runs `sudo`** (it may
  only *print* an install command for you to run yourself), and the only shell
  access is the guarded terminal described above.
- `fn_read_file` reads any user-supplied path and `fn_save_note` writes to
  `~/jarvis_notes.md` — there is no sandboxing. Review before exposing JARVIS
  to untrusted input.
- Screen actions are an explicit, whitelisted set (move/click/type/key/scroll);
  there is no path from a screenshot to arbitrary code execution.
- `fn_execute_python` is the deliberate exception: it runs an agent-authored
  script in a subprocess behind an AST allow/deny pass and a hard timeout.
- MCP servers run as separate processes with your user's permissions. Only
  configure servers you trust, and pass secrets through their `env` object.
- The optional wake word runs entirely on this machine (`openWakeWord`, ONNX):
  the microphone is only opened while JARVIS is idle, nothing is sent anywhere,
  and muting the mic stops it listening.
- Error logs can contain paths and command text. Review them before sharing.
- Never commit `.env`, API keys or `TOKEN.txt`. `.gitignore` covers `.env`,
  `venv/`, caches, audio artifacts and token files.

## License

MIT © 2026 Jakir Hussain Khan. See [LICENSE](LICENSE).

The optional wake word uses [`openWakeWord`](https://github.com/dscripka/openWakeWord)
(Apache-2.0) with its pretrained models, which are licensed **CC-BY-NC-SA 4.0**
(non-commercial use only) by their authors. They are fetched by `pip`/at first
run, never bundled in this repository.
