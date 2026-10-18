# Jarvis — Voice Assistant for Linux

**Version 1.0.3**

A voice-controlled desktop assistant for Linux that can *see* your screen, use the
internet, and operate the terminal. You speak a command; Jarvis either handles it
**locally and offline** (regex router — no API call) or hands it to a
**Groq-backed agent** that can call 40 tools, then answers out loud in English.
Speech recognition and replies are **English-only by design**. A PyQt5 "Arc
Reactor" widget lives in the system tray and talks when you click it.

Jarvis **never runs `sudo`** and never runs arbitrary shell from speech — except
through a guarded terminal tool that refuses anything that could power the device
off and asks before anything risky. Power actions and other sensitive steps are
confirmation-gated everywhere. When a tool is missing it will *print* a
copy-paste install command for you to run yourself, but it never executes one.

---

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
- **Playback that actually starts, and `next` that works** — a freshly opened
  video usually sits paused (browsers block autoplay without a real click), and a
  paused tab publishes no MPRIS session at all, so nothing could control it.
  Jarvis now gives the new tab a background play tap, and **`next`/`previous`
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
  text-to-speech via `edge-tts` (`en-US-AriaNeural`) played through `pygame`.
- **Local fast-path router** — offline handling for power, media (incl. seeking by
  seconds and "what's playing"), volume, brightness, screenshot, lock, battery,
  system status, time, date, windows, clipboard, folders, YouTube (including
  **playing a song by name**, and a plain "play <name>"), dictated terminal
  commands, and opening/closing apps. The API is only used on a miss.
- **Real-time screen vision** — reads the screen ("what does this error say?",
  "summarise this page") and operates it ("click Save", "type my name in the box",
  or pick something you are pointing at: "play the second song", "play that one")
  through whitelisted mouse/keyboard actions only.
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
- **Two front ends** — a terminal loop (`main.py`) and a floating, transparent,
  always-on-top reactor widget with a system tray (`gui.py`). Both share
  `main.handle()`, so confirmation and routing behave identically in each. The
  widget is a mini voice-chat console: it reacts to your voice, moves while it
  speaks, and has a mic mute button and a Stop button.
- **Usable without a microphone** — `JARVIS_TEXT=1 ./venv/bin/python main.py`
  (or `main.py --text`) takes *typed* commands through the exact same
  router/agent path.
- **Readiness report** — `./venv/bin/python doctor.py` (or `main.py --check`)
  prints the session type, which tools are installed and what each missing one
  costs, the model and vision model, the app count, how to try every command, and
  a **copy-paste `sudo apt install …` line** for whatever is missing (for you to
  run — Jarvis never runs it).
- **Robust audio capture** — capture runs through `arecord -D default -f S16_LE
  -r 16000 -c 1` (PyAudio/PortAudio never saw this machine's mic), read in 0.1 s
  chunks and RMS-gated. The stream-open spike is discarded, the stop floor is
  auto-calibrated from the room noise (`1.5 × median`, clamped to 150–300), a
  ~0.3 s pre-roll keeps quiet onsets, and hysteresis ends the phrase after ~1.2 s
  of silence. Phrases are capped at 6 s, and Jarvis waits up to **30 s**
  (`JARVIS_LISTEN_TIMEOUT`) for you to start speaking — Stop abandons a pending
  wait. Playing media is paused while Jarvis listens and resumed afterwards (best
  effort), and `JARVIS_PTT=1` switches to push-to-talk for noisy rooms.
- **Tests** — a `pytest` suite of **282 tests** (1 skipped) covering routing, app
  matching, the protected list, the confirmation flow, the terminal guardrails,
  screen routing, clipboard fallbacks, offline behaviour, the internet tools,
  vision parsing, timers, the YouTube path (play-by-name and on-screen picks),
  text mode, the doctor, the GUI (offscreen), the live mic-level/TTS-envelope
  hooks and the real router→agent dispatch path.

---

## Requirements

- Linux with a working microphone (PipeWire or PulseAudio).
- `arecord` / `aplay` from `alsa-utils` for capture and playback.
- Python 3.10+ (developed on 3.12).
- A Groq API key for the agent, internet tools and screen vision.

Optional desktop tools — Jarvis degrades gracefully when these are missing, but
these features need them. Install them with your system package manager
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
| `gnome-screenshot` / `scrot` / ImageMagick `import` | screenshots |
| `convert` / `ffmpeg` | shrinking screenshots for the vision model |
| `notify-send` | desktop notifications |
| `xdg-open` | opening files, folders and URLs |
| `xclip` / `xsel` | reading the clipboard (falls back to Qt/PyQt5) |
| `cinnamon-screensaver-command` / `loginctl` | lock screen |

> On **Wayland**, `wmctrl` and `xdotool` do not work. Screen automation and
> window control report that plainly; app control falls back to process-based
> closing and media to `playerctl`.

---

## Setup

```bash
python -m venv venv
./venv/bin/pip install -r requirements.txt

# Create .env with your Groq API key (starts with "gsk_")
echo "GROQ_API_KEY=your-key-here" > .env
```

Get a key at <https://console.groq.com/keys>. `.env`, `venv/`, `TOKEN.txt` and
caches are gitignored and must never be committed.

### Configuration

All configuration lives in `config.py` and can be overridden from `.env`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `GROQ_API_KEY` | *(required)* | Groq API key, `gsk_…` |
| `GROQ_BASE_URL` | `https://api.groq.com/openai/v1` | OpenAI-compatible endpoint |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Main model ID (must support tools) |
| `GROQ_VISION_MODEL` | `qwen/qwen3.8-27b` | Multimodal model for screen vision |
| `JARVIS_SCREEN_MAX_WIDTH` | `1280` | Downscale width for vision screenshots |
| `JARVIS_SCREEN_STEPS` | `6` | Max perceive→act steps per screen goal |
| `JARVIS_SHELL_TIMEOUT` | `20` | Seconds before a terminal command is killed |
| `JARVIS_SHELL_MAX_OUTPUT` | `4000` | Output characters returned to the model |
| `JARVIS_OFFLINE` | *(unset)* | `1` disables the agent (router only) |
| `JARVIS_MIC_DEVICE` | `default` | ALSA PCM name for `arecord -D` |
| `JARVIS_LISTEN_TIMEOUT` | `30` | Seconds to wait for you to start speaking |
| `JARVIS_DEBUG` | *(unset)* | `1` prints per-chunk RMS / capture diagnostics |
| `JARVIS_PTT` | *(unset)* | `1` for push-to-talk (Enter to start/stop) |
| `JARVIS_TEXT` | *(unset)* | `1` to type commands instead of speaking |
| `JARVIS_MUTE` | *(unset)* | `1` to skip text-to-speech (replies still print) |

STT language (`en-US`), TTS voice (`en-US-AriaNeural`), history window, output
token cap and tool-round limit are constants in `config.py`.

---

## Usage

```bash
./venv/bin/python main.py                    # terminal voice loop
./venv/bin/python main.py --text             # type commands instead of speaking
./venv/bin/python main.py --offline          # router only (no network)
./venv/bin/python main.py --check            # readiness report (same as doctor.py)
./venv/bin/python main.py --version          # print the version
./venv/bin/python main.py --screen "what is on my screen?"   # one screen answer
./venv/bin/python main.py --gui              # floating widget + system tray
./venv/bin/python doctor.py                  # readiness report
./venv/bin/python test_mic.py                # microphone diagnostic
./venv/bin/python -m pytest                  # run the test suite
```

In the CLI loop, `[STAT]` lines show token counts and track local vs. agent vs.
cache hits. Say **"exit"**, **"quit"**, **"bye"** or **"stop"** (on their own) to
end a session.

**GUI:** left-click the reactor (or the tray icon) to talk, drag to move it,
right-click for the menu, `Esc` or closing the window hides it to the tray, and
only **Quit** in the tray menu really exits. The orb **reacts to your voice** while
it listens and **moves while it speaks**; the **Mic** button mutes/unmutes the
microphone instantly (also `M` or the tray's *Mute mic*), and **Stop** (or `Space`)
cuts a reply off mid-sentence — or abandons a listen that is still waiting for you.

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
  `desktop.py` (`xdotool`/`wmctrl`/`pactl`/… wrappers). Screen actions are limited
  to move/click/type/key/scroll/wait. The only way to reach a shell is the guarded
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
| `desktop.py` | Whitelisted desktop actions plus the protected-process list. Media control falls back to the browser tab's own keys when a page's MPRIS cannot skip tracks. |
| `vision.py` | Screen capture, `look()` (ask about the screen) and `act()` (bounded perceive→act loop). |
| `net.py` | Keyless internet: DuckDuckGo search, page fetch, Wikipedia, weather. |
| `shell.py` | The guarded terminal (`run`, `explain_refusal`, `_is_risky`). |
| `timers.py` | In-process timers and reminders. |
| `confirm.py` | Pending "Are you sure?" state: yes runs, anything else cancels, 15 s expiry. |
| `agent.py` | Groq orchestration: response cache, token accounting, tool schemas from function signatures, English-only system instruction, bounded tool loop, 40 tools. |
| `tools.py` | The tool functions (see the list below). |
| `memory.py` | Rolling `deque` window of recent turns. |
| `main.py` | Entry point and `handle()` dispatch (`--text`, `--offline`, `--screen`, `--gui`, `--check`, `--version`). |
| `gui.py` | PyQt5 Arc Reactor widget + system tray; the listen/think/speak cycle runs on a `QThread`. The orb is fed live mic levels (listening) and the TTS loudness envelope (speaking); the widget adds a mic mute/unmute button and a Stop button. |
| `doctor.py` | Readiness self-check. Prints a copy-paste install line for missing tools (never runs it). |
| `test_mic.py` | Standalone mic diagnostic. |

### Tools (40)

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
- **Phrases never end / music keeps the gate open:** move the mic away from the
  speakers, or set `JARVIS_PTT=1` and end phrases by hand with Enter.
- **"playerctl/brightnessctl is not installed"**: the message (and
  `doctor.py`) prints the exact install command for you to copy-paste; Jarvis
  never runs it.
- **Screen control says it needs X11 / "not supported":** you are on Wayland.
- **"play …" opens YouTube but nothing starts, or "pause" says nothing is
  playing.** Browsers block autoplay until you interact with the site, and a
  paused tab publishes no media session at all. Jarvis taps play on the new tab
  itself (needs `xdotool` + X11); if you opened the tab by hand, click the video
  once and pause/next will work from then on. A browser's MPRIS cannot skip
  tracks (`CanGoNext = false` in Chromium/Brave), so `next`/`previous` are sent
  to the YouTube page as its own keyboard shortcuts instead.
- **A command was refused:** that is the terminal guard. It refuses `sudo`, every
  power/session command, and destructive operations by design; risky ones only run
  after you answer "yes".
- **Agent replies are cut off:** raise `MAX_OUTPUT_TOKENS` in `config.py`.
- **`RequestError` from STT:** speech-to-text needs network access; the local
  router fast-path still works offline.

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
- Never commit `.env`, API keys or `TOKEN.txt`. `.gitignore` covers `.env`,
  `venv/`, caches, audio artifacts and token files.

## License

MIT © 2026 Jakir Hussain Khan. See [LICENSE](LICENSE).
