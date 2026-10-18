# Jarvis — Progress

Last updated: 2026-10-18

A voice-controlled Linux desktop assistant. You speak a command; Jarvis either
handles it locally (regex, offline) or forwards it to a Groq-backed agent that
can call tools, then answers out loud in English. STT and replies are
English-only by design. A PyQt5 "Arc Reactor" widget provides a click-to-talk
GUI.

## Status at a glance

| Area | State |
| --- | --- |
| CLI loop (`main.py`) | Working |
| Voice I/O (STT + TTS) | Working, English-only (STT via `arecord` + Google) |
| Local command router (offline-first) | Working (apps, folders, windows, media, volume, brightness, clipboard, screenshot, lock, status, time, dictated commands) |
| Groq agent + 40 tools | Working (needs `GROQ_API_KEY`) |
| Screen vision (read + operate) | Working on the real screen (`qwen/qwen3.8-27b`) |
| Live internet (search/page/Wikipedia/weather) | Working (keyless, verified live) |
| Guarded terminal | Working (3-layer guard; power/session commands refused in code) |
| Offline mode (`main.py --offline`) | Working |
| Conversation memory | Working |
| GUI widget + system tray (`gui.py`) | Working (offscreen-tested); **mic mute/unmute, Stop, and an orb that reacts to live voice + speech** |
| Microphone diagnostic | Working |
| Desktop control (apps/YouTube/media/volume/brightness/screenshot/lock) | Working |
| Confirmation-gated actions | Working (power, risky terminal, risky screen goals) |
| Timers / reminders | Working |
| Tests | `pytest` suite — **264 passed, 1 skipped** |
| Published | GitHub, tags **`v1.0.1`**, **`v1.0.2`** (README + MIT LICENSE) |

## Repository & releases

- Remote: <https://github.com/jakirhussainkhan/jarvis-voice-assistant>
- Default branch: `main`
- Release tags: **`v1.0.0`** (initial), **`v1.0.1`** (screen vision, live
  internet, guarded terminal, offline-first, terminal hardening) and
  **`v1.0.2`** (voice-chat widget: mic mute/unmute, Stop, live reactive orb).
- Secrets are **not** committed (see *Security* below).

## Voice-chat widget: mic mute, Stop & a live reactive orb (2026-10-18, v1.0.2)

The user asked for the ChatGPT voice-chat feel: control the mic yourself, and watch
a live animation that moves when Jarvis talks. All three parts landed:

- **Mic mute / unmute** — `voice.set_mic_muted()` / `is_mic_muted()` /
  `toggle_mic_mute()` (a runtime flag kept deliberately separate from the existing
  `JARVIS_MUTE`, which silences *output*). Reachable from the widget's **Mic**
  button, the tray's checkable **Mute mic** item, and the **`M`** key. While muted
  the orb dims with a "mic off" badge, `trigger()` refuses to listen, and — because
  `_capture_from_stream()` checks the flag every 0.1 s chunk — *pressing mute
  mid-listen drops the phrase immediately* and `listen()` returns `""` without even
  opening `arecord`.
- **Live, audio-reactive orb** — `voice.set_level_sink(fn)` registers a callback
  fed a 0..1 loudness. While listening that is the real chunk RMS (`_emit_level`);
  while speaking it is the **TTS loudness envelope**. `JarvisWorker` sets the sink
  to a `level = pyqtSignal(float)` and the reactor scales/brightens its glow and
  core from it (a gentle breathe at rest, a springier pulse when loud). The sink is
  optional, so the terminal loop pays nothing.
- **Stop** — `voice.stop_speaking()` sets an event that cuts the current reply
  short (pygame `music.stop()`), exposed as the **Stop** button and the **`Space`**
  key. It also cancels a reply that has not started playing yet (the flag is cleared
  at the *top* of `_play_tts`, so a Stop pressed during synthesis skips playback and
  a stale Stop never silences the *next* reply).
- **Envelope** — `_tts_envelope()` decodes the generated mp3 with ffmpeg to 8 kHz
  mono and computes ~20 ms RMS hops (peak-normalised, sqrt curve); `_pump_envelope()`
  indexes it by real playback position. If ffmpeg is missing or fails it returns `[]`
  and the orb simply keeps its normal pulse — never fatal.

Also in this pass: the widget grew a one-line **"you: …"** transcript, the status
line gained a **Muted** state, and the window is now 230×261 with the two buttons.
`doctor.py` describes the new controls in its "try the GUI" hint.

Verified live: the widget builds offscreen (230×261), mute toggles everywhere,
`trigger()` refuses while muted, the reactor paints at every state/energy, a real
edge-tts mp3 produced a 262-sample / ~5.2 s envelope (mean 0.31, decaying to 0), a
real `speak()` drove 69 level frames, and a Stop at t≈3 s cut a ~9 s clip to 3.04 s.
Found and fixed one real bug doing so (the Stop was cleared *after* synthesis, so a
Stop pressed during synthesis was lost, and pressing Stop early was silently
ignored). Documented above.

**Files:** `voice.py`, `gui.py`, `doctor.py`, `config.py` (`__version__ = "1.0.2"`),
`test_voice_capture.py`, new `test_voice_speech.py`, `test_gui.py`, `README.md`.

## Screen vision, live internet, guarded terminal & offline-first (2026-10-18, v1.0.1)

The "super intelligent" pass the user asked for: Jarvis can now *see* the screen,
*use* the internet, *run* the terminal, and still works **perfectly with no
internet** for everything that does not need it.

### What was added

- **`vision.py` — screen vision.**
  - `look(question)` screenshots the screen, downscales it (`JARVIS_SCREEN_MAX_WIDTH`,
    default 1280) and asks the multimodal model `qwen/qwen3.8-27b` for an answer.
    The offline router routes pure screen-reading phrases ("what's on my screen",
    "read this", "describe the screen") straight to it, so reading the screen is
    fast and deterministic.
  - `act(goal)` is a bounded **perceive → decide → act** loop for operating the
    UI ("click the login button"): each step screenshots at full resolution, asks
    for exactly one JSON action, executes it through whitelisted `desktop.py`
    calls (move/click/type/key/scroll/wait only), and stops on `done`, `fail`, or
    `JARVIS_SCREEN_STEPS` (default 6).
  - Verified live on the real screen: `main.py --screen "what is on my screen?"`
    correctly read the terminal contents.
- **`net.py` — live internet, keyless.** DuckDuckGo HTML search
  (`fn_web_lookup`), full-page reading (`fn_fetch_page`), Wikipedia summaries
  (`fn_wikipedia`) and weather via wttr.in (`fn_weather`). All use `urllib` with
  timeouts and never raise. Verified live: web search returned the current Python
  release, Wikipedia returned the kernel summary, weather returned Tokyo's.
  (`fn_web_search` still opens the results page in a browser.)
- **`shell.py` — a guarded terminal.** `fn_run_command` (hidden, output returned)
  and `fn_run_in_terminal` (visible) with a three-layer guard (see *Terminal
  hardening* below). The system prompt teaches the model to use it like an expert
  (`ls`, `grep`, `df`, `git`, `ffmpeg`, …).
- **Offline-first.** The router now also handles, with no network: folders/paths
  ("open downloads", "open ~/notes.txt"), window listing, clipboard
  (copy/cut/paste/read), "what's playing" and "go back 10 seconds" (media seek),
  and dictated commands. A spoken **capability list** ("help", "what can you do
  offline") is answered locally, and `main.py --offline` (or `JARVIS_OFFLINE=1`)
  disables the agent entirely. When the network is genuinely down, a failed agent
  call degrades to the offline list instead of a raw error.
- **`timers.py` — timers and reminders** (`fn_set_timer`, `fn_set_reminder`) on
  daemon threads; they speak through the registered speaker.
- **GUI as a real desktop app.** `gui.py` is now a floating reactor **plus a
  system tray icon**: click the icon to talk, `Esc` / closing hides to the tray,
  and only *Quit* in the menu exits. Launched with `main.py --gui`.
- **`main.py` flags:** `--text`, `--offline`, `--screen "<question>"`, `--gui`,
  `--check`, `--version`.
- **`doctor.py` updates:** shows the vision model, the offline/terminal
  guarantees, a PyQt5 + clipboard-read check, and how to try every mode. It no
  longer suggests `sudo` anywhere (the user asked for a sudo-free assistant).

### Terminal hardening (the safety-critical part)

The guard lives in `shell.py` and is enforced in code, not in the prompt:

1. **Refused outright.** `sudo`/`su`/`pkexec`/`doas`; every way to power off,
   reboot, suspend, hibernate or end the session — `shutdown`/`reboot`/`poweroff`/
   `halt`/`telinit`, `power off` in any separator spelling, `systemctl` power
   verbs, `loginctl` session termination, the session-logout binaries
   (`gnome-session-quit --power-off`, `xfce4-session-logout`, …), kernel
   `sysrq`/`kexec`, writes to `/sys/power/` or `/proc/sysrq-trigger`, and
   `kill`/`pkill` of init/systemd (pid 1); plus recursive force-deletes
   (`rm -rf` in every spelling, via a token walk), disk formatting/partitioning,
   `dd of=/dev/…`, fork bombs, piping *or chaining* a download into a shell, and
   account/credential changes. Quotes and backslashes are stripped before
   matching, so `s\hutdown` and `sh'utdown'` are still caught.
2. **Confirmed first.** `rm`, `mv`, `chmod`, `kill`, package managers,
   `systemctl`, `ssh`/`scp`/`rsync`, `docker`, `git push`, and writes with `>`
   are read back and only run after a spoken "yes" (15 s expiry).
3. **Runs.** Everything else, with `JARVIS_SHELL_TIMEOUT` and a capped output.

**The terminal can never shut down, restart or power off the device.** A probe of
39 dangerous spellings was refused with zero false denials (see Verification).

### Bugs found and fixed in this pass

- **`media_seek` did nothing.** `playerctl position -10` is parsed as an option
  flag (returns rc 0), so seeking silently failed and Jarvis falsely said
  "Seeked back 10 seconds." Fixed to the correct suffix form (`10-`/`30+`) and now
  reports honestly when no player exists.
- **`weather` returned raw HTML.** wttr.in serves a web page to browser-like
  agents; the fetch now uses a curl-style `User-Agent`, falls back to
  `format=3`, and rejects HTML. (Also defaults to Dhaka for an empty city.)
- **Clipboard read was impossible** with no `xclip`/`xsel`. Added a lazy
  **PyQt5** clipboard fallback (reusing the GUI's `QApplication` when present).
- **Volume/brightness always claimed success** even when `pactl`/`brightnessctl`
  failed; they now check the result and say so.
- **Routing collision:** "run &lt;command&gt;" was mistaken for "launch an app called
  &lt;command&gt;". Commands with an explicit marker ("terminal ls", "run the command
  df -h") now run locally through the guarded terminal; a bare "run firefox" still
  reaches the agent (which opens the app).

### Verification

- `pytest`: **264 passed, 1 skipped**.
  New: `test_vision.py`, `test_screen.py`, `test_screen_routing.py`, `test_tools_extra.py`,
  `test_net.py`, `test_timers.py`, `test_offline.py`, `test_gui.py` (offscreen), plus
  a much larger `test_shell.py` (power/session bypasses, quoted commands,
  `rm` spellings, download-into-shell, redirection).
- **Live on this machine (real network, real screen):** `net.search` /
  `net.wikipedia` / `net.weather` returned real data; the full agent answered
  "latest python version" → *Python 3.15.0*, "weather in Tokyo" → *23 °C light
  rain*, "about the linux kernel" → Wikipedia summary; `main.py --screen` read the
  actual desktop; the offline battery (`--offline`) handled help / time / date /
  status / battery / windows / now-playing / clipboard / dictated commands with
  zero agent calls; the terminal guard refused a battery of 39 destructive forms.
- **GUI** validated offscreen (`make_icon`, widget, state label, quit path) and by
  import; the tray itself was not clicked in this session.

**Not verified live (honest).** Screen *automation* (`vision.act`) was not run
end-to-end against a real UI, because it would move the real mouse and click real
things on the user's screen; the action parsing/whitelist and the risky-goal
confirmation gate are unit-tested and the perceive step (`look`) was verified
live. Clicking the tray icon, a real media seek with a player, and the real
microphone capture likewise remain for the user's own run.

## How it works

```
listen() ─► confirm.resolve() ─► try_local() ──► speak()
   (voice.py)   (router.py)          (voice.py)
                    │
                   miss
                    ▼
             run_agent() ─► Groq (agent.py) ─► tools.py ─┬─► desktop.py (apps/windows/media/screen)
                                                          ├─► vision.py  (see / operate the screen)
                                                          ├─► net.py     (search / wiki / weather)
                                                          ├─► shell.py   (guarded terminal)
                                                          └─► timers.py  (timers / reminders)
                    │
                    ▼
             memory.add() (memory.py)
```

`main.handle()` is the single dispatch path: it resolves any pending
confirmation first, then tries the router and only sends a miss to the agent.

Two front ends share the same brain:

- `main.py` — terminal loop. Prints `[STAT]` lines with token counts, and tracks
  local vs. agent vs. cache hits. Flags: `--text`, `--offline`, `--screen`,
  `--gui`, `--check`, `--version`.
- `gui.py` — floating Arc Reactor widget **plus a system tray icon**. Left-click
  talks, drag moves, right-click/Esc hides to the tray, and only *Quit* exits.
  The listen/think/speak cycle runs on a `QThread` so the UI never freezes.

## Module map

- `config.py` — loads `.env`, holds `API_KEY` (from `GROQ_API_KEY`), `BASE_URL`
  (`https://api.groq.com/openai/v1`), `MODEL_NAME` (`openai/gpt-oss-120b`),
  `STT_LANG` (`en-US`), `TTS_VOICE` (`en-US-AriaNeural`), and the
  history/output/tool limits.
- `voice.py` — `listen()` and `speak()`. `listen()` captures through
  `arecord -D <device> -f S16_LE -r 16000 -c 1` (no PyAudio/PortAudio), reads
  0.1 s chunks and computes RMS per chunk. The stream-open spike (first chunk,
  ~6000) is discarded (and its decaying tail, bounded at 0.5 s); the room floor
  is then **auto-calibrated** to `1.5 × median(background)` clamped to
  **150–300**. Speech starts on RMS > **300**, a ~0.3 s **pre-roll** keeps quiet
  onsets, **hysteresis** keeps recording while RMS stays above the calibrated
  floor, the phrase ends after ~**1.2 s** below it, caps phrases at **6 s**, and
  waits at most 4 s for speech to start. Playing media is paused before the mic
  opens and resumed after capture (best effort, `playerctl` then `pactl`), and
  `JARVIS_PTT=1` switches to push-to-talk (Enter to start, Enter/silence to
  stop). Bytes are wrapped in `sr.AudioData(data, 16000, 2)` and sent to Google
  STT (`en-US`). `speak()` is edge-tts + pygame (`en-US-AriaNeural`). Device
  defaults to `default`; override with `JARVIS_MIC_DEVICE`.
- `router.py` — `try_local()`: offline regex handling for **power (confirmation-
  gated)**, media (`playerctl`), volume (`pactl`), brightness (`brightnessctl`),
  screenshot, lock, battery, system status, time, date, YouTube (open/play),
  open/close/quit/kill apps. A play only fires with an explicit media verb;
  `close youtube` targets the tab (never a search). A weak app-name match asks
  "Did you mean <app>?" instead of guessing; an unknown target defers to the
  agent. Any command containing a search verb returns `None` (agent handles it).
- `desktop.py` — the whitelisted desktop actions: app index from `.desktop`
  files (`gtk-launch`/`gio launch`), fuzzy matching with weighted keys, graceful
  close (`wmctrl -c` → SIGTERM → SIGKILL after ~3 s), `list_running()`, YouTube
  (`yt-dlp` top result / search / homepage / tab close), media, volume,
  brightness, screenshot, lock, and confirmed power actions. Holds the protected
  list (Jarvis + ancestors, display server, session manager, systemd, shell).
  X11/Cinnamon here; on Wayland window ops degrade to "Which window?".
- `vision.py` — screen vision: `capture_png`, `look(question)` (ask about the
  screen) and `act(goal)` (bounded perceive→act loop). Only whitelisted
  move/click/type/key/scroll/wait actions; no shell.
- `net.py` — keyless internet: DuckDuckGo search, page fetch, Wikipedia, weather.
- `shell.py` — the guarded terminal: `run()`, `explain_refusal()`, `_is_risky()`,
  and `_recursive_force_rm()`. Enforces the three-layer guard.
- `timers.py` — in-process timers/reminders on daemon threads, speaking through a
  registered speaker.
- `confirm.py` — tiny pending-action state machine for "Are you sure?": a "yes"
  runs the action, anything else cancels, and it expires after 15 s.
- `agent.py` — Groq chat orchestration over the OpenAI-compatible client, md5
  response cache, token accounting, tool schemas built from function signatures,
  and an English-only system instruction.
- `tools.py` — the **40** tool functions the model can call: apps/windows, media,
  screen (screenshot/see/act/click/type/keys/scroll), internet, terminal, files,
  clipboard, notifications, system info, notes and timers. Signatures +
  docstrings become the schema (no hand-written schema).
- `memory.py` — rolling `deque` window of recent turns, rendered as a transcript.
- `main.py` — terminal entry point and `handle()` dispatch (confirmation first,
  then router, then agent). `--text` / `JARVIS_TEXT=1` reads typed commands and
  `--check` prints the readiness report; `handle()` catches agent/network errors.
- `doctor.py` — read-only readiness self-check (`main.py --check`): session type,
  installed tools and what each missing one costs, apps indexed, API-key status.
- `gui.py` — PyQt5 Arc Reactor widget (`QThread` worker); shares `main.handle()`
  so confirmations work in the GUI too.
- `test_mic.py` — standalone mic diagnostic (arecord-based): lists capture
  devices/PCMs with `arecord -l` / `-L`, records a WAV with the same
  `arecord_command()` as `listen()`, reports RMS/peak, plays it back with `aplay`,
  recognises it, and checks the gate in `voice.py`. No PyAudio.
- `test_routing.py`, `test_desktop.py`, `test_confirm.py`, `test_exit.py`,
  `test_memory.py`, `test_agent_cache.py`, `test_flow.py`, `test_voice_capture.py`
  — pytest suite (see Verification).
- `README.md`, `LICENSE` — docs and MIT license.

## What's done

- End-to-end voice loop (CLI and GUI) works.
- Migrated off Gemini to Groq (`openai/gpt-oss-120b`,
  `https://api.groq.com/openai/v1`).
- English-only throughout: English STT (`en-US`), an English TTS voice
  (`en-US-AriaNeural`), an English-only system instruction, and a router with no
  non-English patterns.
- Router/agent boundary: search commands defer to the agent; simple commands
  (`open youtube`, `open chrome`, …) are still handled locally.
- Offline fast-path for common commands; API only used on a miss.
- Local tools for app control, system stats, search, notes, and file reading.
- Response cache to avoid repeat API calls for identical queries.
- **Microphone capture fixed** (see below): the wrong input device (direct ALSA
  `hw:1,0`) plus a clipping 100 % capture gain made `listen()` always time out /
  return `""`; it now uses the system default input with a sane gain.
- **Capture moved off PyAudio to `arecord`** (see *PyAudio → arecord rewrite*):
  the PyAudio path still timed out on this machine even though arecord captured
  the voice, so `listen()` now RMS-gates raw `arecord` 0.1 s chunks itself and the
  `pyaudio` dependency is gone.
- Floating, transparent, always-on-top reactor widget with live state colours
  (Idle / Listening / Thinking / Speaking).
- pytest suite covering routing, `is_exit`, memory, the response cache, and the
  real router→agent dispatch.
- Published to GitHub with a README and an MIT LICENSE (`v1.0.0`).

### Microphone fix (2026-10-18)

Symptom: `main.py` printed `Listening…` forever and never recognised speech, even
when clearly spoken. There were **two** causes:

**1. Wrong input device.** `voice.py` used the onboard codec directly
(`ALC245` / `hw:1,0`, index 4). While PipeWire owns the device, that direct-ALSA
path returns a loud, **clipped** signal (peak 32768) — or fails to open at all
(`AttributeError: 'NoneType' object has no attribute 'close'`) — so `listen()`
always timed out after discarding 23–40 warm-up buffers. `arecord -D default`
recorded the voice fine, proving the mic works through the **system default /
PipeWire** device.

Fix: `_pick_input_device()` now prefers the system default input (device named
`default`, then `pipewire` / `pulse`), with `ALC245` / `hw:1,0` only as a last
resort. `JARVIS_MIC_INDEX=<n>` overrides the choice.

**2. Capture gain too high.** The ALSA `Capture` control was at **100 % (30 dB)**,
which clipped (peak 32768) and pushed the noise floor to ~3000–14500 RMS — far
above the 300 gate — so `listen()` grabbed noise and returned `""`.

Capture-floor vs gain (`arecord -D default`, RMS):

| Capture | floor RMS | peak |
| --- | --- | --- |
| 100 % | 14559 | 32768 (clip) |
| 70 % | 492 | 4708 |
| 50 % | 158 (p95 1445) | 1131 |
| 40 % | 84 (p50), 165 max | — |
| 35 % | 66 | — |

At 50 % the floor still spikes past 300, so **40 %** is the recommended setting
(`amixer -c 1 sset Capture 40%`); 100 % clips badly.

Also kept (from the earlier threshold fix): `energy_threshold = 300` fixed,
`dynamic_energy_threshold = False`, no `adjust_for_ambient_noise()`, and
`_flush_warmup(src)` which discards the stream-open transient (bounded at 1.5 s).
`RequestError` is printed loudly instead of being swallowed; timeouts,
`UnknownValueError` and capture errors log distinct messages; debug output is
gated behind `JARVIS_DEBUG=1`.

### PyAudio → arecord rewrite (2026-10-18, later)

Even after the device/gain fixes, the **PyAudio path itself** was the remaining
problem: on the real machine `arecord -D default -f S16_LE -r 16000 -d 6` records
the voice cleanly (background RMS **76**, loudest **6172**, Capture gain at 40 %),
but `sr.Microphone` (devices 6/7) always raised `WaitTimeoutError` — "no speech
above threshold within 4 s". PortAudio simply does not see this mic the way ALSA
does.

Fix: `listen()` no longer uses `sr.Microphone`/PyAudio at all. It spawns
`arecord -D default -f S16_LE -r 16000 -c 1 -t raw -q`, reads **0.1 s chunks**
(1600 frames / 3200 bytes), computes RMS per chunk with the existing helpers,
starts recording when RMS > **300**, stops after ~**1 s** (10 chunks) below 300,
and caps a phrase at **8 s** (by wall-clock *and* chunk count, so a
faster-than-realtime stream still obeys the cap). The 4 s wait-to-start timeout is
kept, and `select()` bounds each read so a stalled `arecord` can never block past
it. Captured bytes are wrapped in `sr.AudioData(data, 16000, 2)` and passed to
`recognize_google(..., language="en-US")` exactly as before. `RequestError` /
`UnknownValueError` handling is unchanged; `JARVIS_DEBUG=1` now prints the RMS of
every 0.1 s chunk.

The old PyAudio-only pieces were removed: the ALSA/JACK `ctypes` error handlers,
`_pick_input_device()`, `_flush_warmup()` (the arecord raw stream has no PortAudio
stream-open transient) and the `pyaudio` dependency (dropped from
`requirements.txt`). The mic is now selected with `JARVIS_MIC_DEVICE=<arecord -L
name>` (default `default`); the PyAudio `JARVIS_MIC_INDEX` override is obsolete.
`test_mic.py` uses the **same** `arecord_command()` builder (WAV output), lists
devices with `arecord -l`/`-L`, and plays back with `aplay`.

## Desktop control, safety & capture tuning (2026-10-18)

Final feature pass: control the Linux desktop by voice (English only). All new
system actions live in `desktop.py` and are reached either from the offline
router or as agent tools. **No `sudo`, no arbitrary shell** — only fixed,
whitelisted commands.

### Environment check (this machine)

| Tool | Status |
| --- | --- |
| `wmctrl` | present |
| `pactl` | present |
| `yt-dlp` | present |
| `gtk-launch` | present |
| `gio` | present |
| `gnome-screenshot` | present |
| `amixer` | present |
| `loginctl`, `systemctl`, `gdbus` | present |
| `xdotool` | present (installed since the first pass) |
| `playerctl` | present |
| `brightnessctl` | present |
| `scrot`, ImageMagick `import` | present |
| `qdbus` | **missing** (not needed; lock uses `cinnamon-screensaver-command`/`loginctl`) |

Session is **X11 / Cinnamon** (`XDG_SESSION_TYPE=x11`, `XDG_CURRENT_DESKTOP=X-Cinnamon`),
so `wmctrl` works and Wayland caveats do not apply. All the optional desktop
tools are now installed, so media, brightness, screenshot and focused-tab
(`xdotool`) control are live; run `./venv/bin/python doctor.py` for the current
list.

### Voice commands

| You say | What happens |
| --- | --- |
| "open telegram" / "open files" / "open terminal" / "open settings" / "open calculator" | fuzzy-matches the app and launches it |
| "open <name>" (something not installed) | "I couldn't find that app." (never guesses) |
| "close telegram" / "quit files" / "kill calculator" | graceful close, then SIGKILL after ~3 s |
| "close youtube" | closes the focused tab via Ctrl+W if possible, else "Which window?" |
| "open youtube" | opens the YouTube homepage |
| "play lo-fi on youtube" | extracts just the song name, plays the top `yt-dlp` result, says what is playing |
| "play … on youtube" with no song name | asks "What should I play?" |
| "search cats on youtube" | opens the results page (via the agent) |
| "pause" / "resume" / "next song" / "previous track" / "stop the music" | media control (`playerctl`) |
| "volume up" / "volume down" / "set volume to 40" / "mute" / "unmute" | volume (`pactl`, clamped 0–100) |
| "brightness up" / "brightness down" / "set brightness to 40" | brightness (`brightnessctl`) |
| "take a screenshot" | saves `~/Pictures/jarvis-screenshot-<time>.png` and names it |
| "lock the screen" | locks the session |
| "what time is it" / "what's the date" / "battery" / "system status" | time / date / battery / CPU-RAM-disk |
| "shut down" / "restart" / "log out" / "suspend" | asks **"Are you sure?"**; runs only on "yes" (expires in 15 s) |
| "exit" / "quit" / "bye" / "stop" (bare) | ends the session |
| anything with "search", "look up", "find", or otherwise unclear | goes to the Groq agent |

### Safety

- **Protected list** (never closed/touched): Jarvis itself and its whole
  ancestor chain (so the terminal running it), the display server (`Xorg`/
  `Xwayland`), the session manager, `systemd`/`dbus`, and the desktop shell
  (`cinnamon`, `gnome-shell`, `plasmashell`, …).
- **Confirmation flow**: shutdown/restart/logout/suspend (and any future
  delete) register with `confirm.py`; "yes" runs them, anything else cancels,
  and an unconfirmed request expires after 15 s. There is currently **no** delete
  function, so nothing else is destructive.
- **Weak matches**: if the best app-name score is below the strong threshold,
  Jarvis asks "Did you mean <app>?" instead of closing the wrong thing.
- `close/quit/kill X` is handled before the YouTube branch, so it can never
  become a search query.

### Capture tuning

- Warm-up discards the `arecord` stream-open spike (and its decaying tail,
  bounded at 0.5 s) without cutting a phrase that starts immediately.
- The stop floor is auto-calibrated per listen: `1.5 × median(background)`
  clamped to **150–300**, replacing the fixed 150.
- Phrases are capped at **6 s** (was 8 s).
- Playing media is paused before the mic opens and resumed after capture (best
  effort via `playerctl`, falling back to `pactl`), so music does not leak in.
- `JARVIS_PTT=1` enables push-to-talk: press Enter to start, press Enter again
  (or pause speaking) to end the phrase — the escape hatch for noisy rooms.
- `JARVIS_DEBUG` keeps the per-chunk trace (showing `state=warmup|waiting|
  recording` and the live `floor=`) and now also prints the chosen
  `background_median` / `floor`, warning when the median is above 200.

### Honest limitations (not verified on the real machine)

- **Real-voice capture** with the new calibration was validated on synthetic PCM
  and a recorded speech WAV, **not** the live microphone. Run
  `JARVIS_DEBUG=1 ./venv/bin/python main.py` and speak; the per-chunk lines show
  the calibrated `floor=` and whether the phrase was captured whole.
- **Media, brightness and focused-tab closing** are unit-tested with mocked
  subprocess calls; the read-only paths (`playerctl status`, `brightnessctl get`,
  `xdotool getactivewindow`, `pactl get-sink-volume`) were verified live once
  the tools were installed. A real media pause/resume and a real brightness
  change were not run (no player was playing; changing brightness is intrusive).
- **Power actions were deliberately not run** (they would power off / reboot the
  machine). Only the confirmation handshake is tested.
- **Screenshot capture** was not run (it would drop a PNG in `~/Pictures`);
  `gnome-screenshot`, `scrot` and `import` are now all installed.
- App matching was checked against this machine's 126 `.desktop` entries
  (`telegram`, `files`, `terminal`, `settings`, `system settings`,
  `system monitor`, `calculator`, `browser` all resolve correctly); other apps
  were not individually verified.

## Bug fixes: YouTube query extraction & capture hardening (2026-10-18)

Two bugs reported from the real machine while saying *"play &lt;song&gt; on
YouTube"*.

### 1. The whole sentence was used as the search query

*"play … udi udi"* was misheard as a long sentence, and the entire (messy)
transcript was passed to `yt-dlp` / the results URL, so it opened a results page
instead of playing the song.

Fix: a single extractor, `desktop.extract_media_query(text)`, now decides what
actually reaches `yt-dlp`:

- take the text **after** an explicit verb (`play` / `watch` / `listen to`);
- drop a trailing **"on youtube"** (and any other "youtube" mention);
- strip spoken filler / STT noise — `please`, `the`, `a`, `song(s)`, `music`,
  `video(s)`, `track(s)`, `tune(s)`, `player`, `media`, `i am` / `i'm` / `im`,
  `just`, `what do you mean`, `you know`, `like`, `now`, `hey`, `ok`, …
- collapse whitespace and trim punctuation.

`router.try_local()` uses it for the local play path, and `play_youtube()`
applies it again so the agent tool path is safe too. When the cleaned query is
empty, Jarvis asks **"What should I play?"** instead of searching.

Example: `"play what do you mean music player i am i am just please the song udi
udi on youtube"` → `"udi udi"` → plays the top `yt-dlp` result for *udi udi*.

### 2. Phrases never ended by silence when music was playing

With music from the speakers leaking into the mic, background RMS sat between
100 and 250 — above the calibrated `floor=150` — so the silence gate never
closed and capture ran to the length cap (`reason=max_length`).

Fixes:

- **Push-to-talk:** `JARVIS_PTT=1` presses Enter, records, and ends on Enter
  again (or silence). Deterministic end-of-phrase regardless of background.
- **Pause playback while listening:** before the mic opens, if `playerctl` shows
  a player *Playing* it is paused (else `pactl` suspend-sink when a sink-input is
  *RUNNING*), and it is resumed after the phrase is captured. Best effort — it
  never fails listening.
- **Debug calibration report:** `JARVIS_DEBUG=1` now prints
  `calibration: background_median=… floor=… samples=…` and, when the median is
  above **200**, warns that speakers/music are probably leaking into the mic.
- **Shorter phrases:** the max phrase length is now **6 s** (was 8 s).

Verification:

- `pytest`: **94 passed, 1 skipped** (the skip is the opt-in `JARVIS_TEST_WAV`
  recorded-speech test; setting the env var makes it pass).
- New tests: `extract_media_query` on plain and messy sentences
  (`"… music player i am … the song udi udi"` → `"udi udi"`), the empty-query
  "What should I play?" path, router forwarding only the song name, media
  pause/resume (playerctl and pactl paths, and the nobody-is-playing no-op),
  push-to-talk recording below the start gate, the same tone being ignored
  without PTT, and the stop-event ending a phrase.
- Manual synthetic run with `JARVIS_DEBUG=1`: a 220-RMS "music" background
  prints the calibration line and the `background median … > 200 … leaking`
  warning, confirming the new diagnostics.

**What could not be tested on the real machine (honest).** The live microphone
was again unavailable here, so the music-pause/resume and the push-to-talk
Enter flow were verified with synthetic PCM and mocked subprocess calls, **not**
a real utterance with speakers playing. `playerctl` and `pactl` behaviour on the
user's actual PipeWire setup is untested (only the command construction is).
The improved extraction was validated on the exact messy sentence reported plus
hand-written variants; other obscure mishearings may still slip through.

## Hardening pass: testability & real bug fixes (2026-10-18)

Goal (user request): make the whole thing run smoothly out of the box and stop
the "another bug every time" cycle. Approach: audit the router against a battery
of realistic phrases with the desktop actions stubbed, fix everything that
misrouted, and add tools that let you *see* what works.

### Real bugs found and fixed

1. **`yt-dlp` silently failed → "play on YouTube" opened a results page.** The
   installed `yt-dlp` (2024.04.09) errors with *"Requested format is not
   available"* on `--get-id`, so even a clean query fell back to search.
   `play_youtube()` now uses
   `yt-dlp "ytsearch1:<q>" --flat-playlist --print id --no-warnings`, which
   resolves the search hit's id without any format selection. Verified live:
   `udi udi` → `rC-kbRanJQ0`, `despacito` → `kJQP7kiw5Fk`.
2. **"open system settings" / "open system monitor" ran the status command.**
   `SYS_STATUS` matched the bare word "system". It now only matches
   `cpu|ram|memory|disk|resources|system status`.
3. **"close the browser" did nothing.** "browser" was stripped from the target,
   leaving an empty string, so it deferred. It is no longer stripped.
4. **"open lock screen settings" locked the screen.** The lock branch is now
   skipped when the utterance is an open command.
5. **A TTS/network failure killed the whole session** (`speak()` raised before
   printing, and `main` didn't catch it). `speak()` now never raises: it logs
   the TTS error and always prints the reply. Added `JARVIS_MUTE=1` to skip TTS.
6. **An agent/network error crashed the loop.** `main.handle()` now catches it
   and returns a spoken apology instead.
7. **Natural phrasings missed:** "make it louder/quieter" (volume),
   "make it brighter"/"darker" (brightness — `brighter` is a separate word from
   `bright`), and "show me the files" (the stray "me" is now stripped).

### New: test everything without a microphone

- **Text mode** — `JARVIS_TEXT=1 ./venv/bin/python main.py` (or `main.py --text`)
  reads typed commands through the exact same `handle()` path (router +
  confirmation + agent). `JARVIS_MUTE=1` silences TTS for fast testing.
- **Readiness doctor** — `./venv/bin/python doctor.py` (or `main.py --check`)
  prints the session type, every tool (ok / REQ / missing) with what it powers,
  the number of apps indexed, API-key status, and the exact `apt install` line
  for whatever is missing.
- **Better agent instructions** — the system prompt now tells the model to call
  the tools and confirm briefly instead of describing the action.

### Verification

- `pytest`: **110 passed, 1 skipped** (skip = opt-in `JARVIS_TEST_WAV`).
- Live on this machine: `doctor.py` reports all optional tools present (126 apps
  indexed, `GROQ_API_KEY` set); the read-only `playerctl status`,
  `brightnessctl get`, `xdotool getactivewindow`, `pactl get-sink-volume` paths
  all respond; a real `run_agent` call answered (`reply='ok'`, 723 tokens); the
  text-mode loop ran `shut down → Are you sure? → no → Cancelled.`,
  `open system settings → Opening System Settings.` (the app really opened, then
  was closed), and `bye → Shutting down. Session: …`.
- Router audit over ~70 phrases (`/tmp/opencode/route_audit.py`) confirms the
  fixes and no new misroutes.

**Not verified live (honest).** A real media pause/resume and a real brightness
change (no player was playing; changing brightness is intrusive), a real
screenshot (would write a PNG), the power actions (would reboot), and the live
microphone capture. The GUI was not launched (no display in this session); text
mode exercises the identical dispatch path the GUI worker uses.

## Verification (2026-10-18)

### arecord capture rewrite

- `pytest`: **30 collected, 29 passed, 1 skipped** (the skip is the opt-in
  `JARVIS_TEST_WAV` recorded-speech test; setting that env var makes it 30/30).
- New `test_voice_capture.py` (6 tests): synthetic PCM through a real OS pipe
  proves the gate starts on RMS > 300, drops leading silence, stops after ~1 s of
  silence, caps a phrase by chunk count, and that `listen()` wraps the bytes in
  `sr.AudioData(..., 16000, 2)` and passes `language="en-US"`. A missing `arecord`
  returns `""` cleanly.
- End-to-end **without a live voice**: a recorded speech WAV (`edge-tts`
  "Opening YouTube" → `ffmpeg` 16 kHz mono) was fed through the exact
  `_capture_from_stream()` → `sr.AudioData` → `recognize_google` path with
  `JARVIS_DEBUG=1` → captured `55040` bytes, `rms=3101 peak=17107`, recognised
  **`'open YouTube'`**. The same WAV passes as
  `JARVIS_TEST_WAV=… pytest test_voice_capture.py`.
- Live device path (this sandbox, ambient only): `voice.listen()` spawns real
  `arecord -D default`, streams 0.1 s chunks (debug prints per-chunk RMS),
  captures and calls Google, then returns `""` with `UNKNOWN_VALUE` because the
  audio is ambient noise — i.e. the whole subprocess → gate → STT path runs.
- `test_mic.py --list` enumerates `arecord -l`/`-L` (hardware `ALC245`; PCMs
  `default`, `pipewire`, …); `test_mic.py --seconds 2 --no-play` records through
  the same `arecord_command()`, writes `test_mic.wav`, and reports RMS/peak.
- `JARVIS_MIC_DEVICE` overrides the arecord device; the `-d` duration is
  normalised to whole seconds (arecord rejects `2.0`).

**What could not be tested (honest).** The real human voice on the actual machine
was **not** testable here. This sandbox exposes the same `ALC245`/`default`
device but a very different noise floor: ambient RMS measured **~1500–6700**, not
the user's reported background **76** / loudest **6172** / 40 % gain. So the fixed
300 gate fires on ambient noise here and cannot confirm the 76-vs-6172 speech
separation. The recorded-WAV run is the closest substitute and exercises the
identical capture, RMS-gate, `sr.AudioData` and Google STT code — but it is TTS
speech read as PCM, not a live utterance through the mic. Also, the old PyAudio
"warm-up flush" for the stream-open transient was dropped on the assumption that
arecord's raw stream does not pop; if a false trigger shows up on the real
machine, a bounded warm-up discard can be reintroduced.

### Bug fixes: duplicate output, router search, truncated phrases (2026-10-18)

Three bugs reported from the real machine (arecord capture now working):

1. **Duplicate output.** `listen()` prints `👤 <text>` and `speak()` prints
   `🤖 <reply>`, but `gui.JarvisWorker.run()` *also* emitted `message(👤 …)` and
   `message(🤖 …)`, which `_log` printed. So the GUI showed each line twice
   (`👤 open YouTube` from `listen()`, then lowercase `👤 open youtube` from the
   worker). Fix: dropped both duplicate emits; the worker still emits `⚠️` errors.
   `main.py` never duplicated, and `speak()` is called exactly once per reply
   (`main.py:69`, `gui.py:151`).
2. **Router searched on "close".** Any text containing "youtube" ran the search
   branch, and `_clean("close youtube", …)` left `"close"` as the query →
   *"Playing close on YouTube."*. Fix: the YouTube play/search path now requires
   an explicit media verb (`YT_ACTION` = play/watch/listen/search/find/look up);
   bare "open youtube" opens the homepage; a bare "youtube" defers to the agent.
   A new `close/quit/stop <target>` branch runs first: known apps are closed with
   a graceful `pkill -TERM -x`, browser tabs (e.g. `close youtube`) return
   *"I can't close browser tabs yet."*, and unknown targets defer to the agent.
   `is_exit()` in `main.py` is now target-aware (`quit youtube` / `stop the music`
   no longer end the session; `stop`, `please stop`, `stop it` still do).
3. **Truncated phrases** ("what time is it" → "what"). `_capture_from_stream()`
   gained a ~0.3 s **pre-roll** (so quiet word onsets are not clipped), hysteresis
   (start > 300, keep recording while RMS > 150), and a ~1.2 s end-of-phrase pause
   (was 1.0 s). `JARVIS_DEBUG=1` now prints per-chunk `rms=… gate=… state=waiting|
   recording` and a final `state=ended reason=…`.

Fix for a latent bug found while testing: `0.3/0.1` and `1.2/0.1` underflow in
float, so `int()` yielded 2 and 11; both now use `round()` (→ 3 pre-roll chunks,
12 silence chunks).

Verification:

- `pytest`: **39 collected, 38 passed, 1 skipped**; with
  `JARVIS_TEST_WAV=…speech16k.wav` set: **39 passed**.
- Router: `close youtube` → *"I can't close browser tabs yet."* (and opens
  nothing), `open youtube` → *"Opening YouTube."*, `play lo-fi on youtube` →
  *"Playing lo-fi on YouTube."*, `close chrome` → *"Closing Chrome."*,
  `close the door` → `None` (agent).
- `is_exit`: `quit youtube`, `stop the music` → **False**; `stop`, `please stop`,
  `stop it` → **True**; `unstoppable`, `byebye` → **False**.
- Capture on the recorded speech WAV (`JARVIS_DEBUG=1`): per-chunk RMS/state
  printed (`0`, `22` waiting; `5949` recording; … `12/12` silence), full
  `61440` bytes captured, `state=ended reason=stream_end`, recognised
  **`'open YouTube'`**.
- New tests: pre-roll keeps a soft onset (RMS 250 start), a 1.5 s mid-phrase dip
  between 150 and 300 no longer cuts the phrase (would have stopped at 1.4 s
  before), plus the three requested router cases.

**What could not be tested (honest).** Still the real voice: the fixes are proven
on synthetic PCM and the recorded TTS-speech WAV, and on the router/exit logic
directly. I could **not** confirm on the actual machine that the new pre-roll /
hysteresis / 1.2 s pause turn *your* "what time is it" into the whole phrase —
that needs a live `JARVIS_DEBUG=1 ./venv/bin/python main.py` run so you can read
the per-chunk RMS trace. The duplicate-output fix was verified by inspection and
by compiling `gui.py`; the PyQt GUI itself was not launched here.

### Earlier PyAudio-era verification (2026-10-18)
- Real dispatch via `main.handle()`: `"what time is it"` → local, no API call.
- Real dispatch via `main.handle()` with a live Groq call: `"open YouTube and
  search for lo-fi music"` reaches the agent and triggers the `fn_youtube` tool.
- Device selection: `voice._pick_input_device()` → **7 (`default`)**;
  `test_mic.py` now tests the same device that `voice.py` picks and prints the
  index.
- `JARVIS_MIC_INDEX` override verified (`=6` → device 6).
- `listen()` on the chosen device, capture + recognition via the system-output
  loopback (**not** the real voice): captured `157696` bytes,
  `rms=3040 peak=17163` → recognised `'open YouTube'`.
- `listen()` with no speech at 40 % capture gain: clean `WaitTimeoutError`
  ("no speech above threshold") — no false captures.
- `JARVIS_DEBUG=1 ./venv/bin/python main.py`: device `7`, threshold `300`,
  warm-up discards the TTS-gap transient (18 buffers, ~0.42 s), then timeouts
  while silent.
- `test_mic.py` device selection prints: system default `7 (default)`,
  `voice.py would pick 7 (default)`, chosen index `7`.
- Project source has no `gemini` / `GEMINI_API_KEY` / `google.genai` references;
  the unused `google-genai` SDK was uninstalled from `venv/`.

## Security / secret handling

- **Never commit API keys.** `.env` (holds `GROQ_API_KEY`) and `TOKEN.txt` are
  gitignored and are **not** tracked.
- Verified: `git grep` and full `git log -p` history contain **no** Groq key
  value — only placeholders like `GROQ_API_KEY=your-key` and the note that Groq
  keys start with `gsk_`.
- The remote has no `.env` (HTTP 404).
- If a key is ever exposed, **rotate/revoke it** in the provider console
  (Groq: <https://console.groq.com/keys>; GitHub PAT: Settings → Developer
  settings → Personal access tokens).

## Known issues & things to verify

- `fn_read_file` reads any user-supplied path and `fn_save_note` writes to
  `~/jarvis_notes.md` — no sandboxing; review before exposing to untrusted input.
- The response cache is in-memory only and keyed by lowercased query, so it is
  lost on restart and ignores conversation context.
- The agent path needs network access; the router fast-path works offline.
- The voice-chat widget's **mic-level reaction while *listening*** is covered by
  unit tests (a synthetic tone drives the sink) but was not watched on a real mic;
  the *speaking* animation, the Stop button and the envelope were verified live.
- Clicking the **tray** Mute/Talk items was not exercised on screen (the tray is
  unavailable offscreen); the widget buttons, the `M`/`Space` keys and the shared
  `voice` state they drive are all tested.
- The mic mute is per-session (not persisted across restarts) by design.
- `MAX_OUTPUT_TOKENS = 600` keeps the assistant brief while leaving reasoning
  headroom; raise it if long answers are ever cut off.
- The arecord rewrite was verified against a *recorded* speech WAV and the local
  device's ambient noise, not the real voice: run `main.py` once on the actual
  machine to confirm the 76/6172 RMS separation at 40 % gain.

## Next steps

1. Consider persisting notes/history and adding a wake word.
2. Decide on sandboxing for file/shell tools.
3. Optionally add CI (run `pytest` on push).
4. Optional hands-free "voice session": one Start button, then listen/reply in a
   loop using the existing RMS voice-activity gate until the user mutes or stops.

## How to run

```bash
python -m venv venv
./venv/bin/pip install -r requirements.txt
echo "GROQ_API_KEY=your-key" > .env   # Groq key, starts with gsk_

./venv/bin/python main.py             # terminal voice loop
./venv/bin/python main.py --text      # type commands instead of speaking
./venv/bin/python main.py --offline   # router only (no network, no agent)
./venv/bin/python main.py --screen "what is on my screen?"   # one screen answer
./venv/bin/python main.py --gui       # floating widget + system tray
./venv/bin/python main.py --check     # readiness report (tools, apps, config)
./venv/bin/python main.py --version   # print the version
./venv/bin/python doctor.py           # readiness report
./venv/bin/python test_mic.py         # mic diagnostic (add --list to only list devices)
./venv/bin/python -m pytest           # test suite
```

The GUI is a small voice-chat console: click the orb to talk, the **Mic** button
(or `M`) mutes/unmutes the microphone instantly, **Stop** (or `Space`) cuts a reply
short, the orb reacts to your voice while listening and moves with its own speech
while talking, and only **Quit** in the tray menu exits.

Requires a working microphone and, for the agent path, network access plus a
valid Groq `GROQ_API_KEY`. `.env`, `venv/`, caches, `TOKEN.txt` and audio
artifacts are gitignored.

Microphone tips (this machine):

```bash
amixer -c 1 sset Capture 40%                       # 100% clips; 40% keeps the floor under the 300 gate
JARVIS_MIC_DEVICE=plughw:1,0 ./venv/bin/python main.py  # force an arecord -L device (default: "default")
JARVIS_DEBUG=1 ./venv/bin/python main.py           # print per-chunk RMS / capture diagnostics
JARVIS_PTT=1 ./venv/bin/python main.py             # push-to-talk: Enter to start, Enter to stop
JARVIS_TEXT=1 JARVIS_MUTE=1 ./venv/bin/python main.py  # type commands, no mic / no TTS
```
