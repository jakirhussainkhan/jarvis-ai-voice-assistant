# Jarvis — Progress

Last updated: 2026-10-19

A voice-controlled cross-platform desktop assistant. You speak a command; Jarvis either
handles it locally (regex, offline) or forwards it to a Groq-backed agent that
can call tools, then answers out loud in English. STT and replies are
English-only by design. A PyQt5 ChatGPT-Voice-style card provides a click-to-talk
GUI — or a hands-free **"Hey Jarvis"** wake word once the optional offline engine
is installed.

## Status at a glance

| Area | State |
| --- | --- |
| CLI loop (`main.py`) | Working |
| Voice I/O (STT + TTS) | Working, English-only (STT via `arecord` + Google; continuous sounddevice streaming TTS) |
| Local command router (offline-first) | Working (apps, folders, windows, media, volume, brightness, clipboard, screenshot, lock, status, time, dictated commands) |
| Groq agent + 43 tools | Working (needs `GROQ_API_KEY`) |
| Screen vision (read + operate) | Working on the real screen (`qwen/qwen3.8-27b`, verified live) |
| Live internet (search/page/Wikipedia/weather) | Working (keyless, verified live) |
| Guarded terminal | Working (3-layer guard; power/session commands refused in code) |
| Offline mode (`main.py --offline`) | Working |
| Conversation memory | Working |
| GUI widget + system tray (`gui.py`) | Working (offscreen-tested); **ChatGPT-Voice-style dark card, breathing orb, live 13-bar waveform, mic mute/unmute, Stop, barge-in, and the "Hey Jarvis" wake word** |
| Microphone diagnostic | Working |
| Desktop control (apps/YouTube/media/volume/brightness/screenshot/lock) | Working |
| Confirmation-gated actions | Working (power, risky terminal, risky screen goals) |
| Timers / reminders | Working |
| Offline wake word ("Hey Jarvis") | **Built + unit-tested; NOT yet verified live** (needs `pip install openwakeword onnxruntime`) |
| Desktop launcher (`run.sh` + app-menu entry) | Working (verified: `desktop-file-validate` clean, GUI starts) |
| Tests | `pytest` suite — **477+ passed, 1 skipped** |
| Spoken voice | `en-GB-RyanNeural` (British male); override with `JARVIS_TTS_VOICE` |
| Published | GitHub, tags **`v1.0.1`**…**`v2.0.0`** (README + MIT LICENSE) |

## The Ultimate v2.0.0 Release (2026-10-19)

Jarvis v2.0.0 is the largest milestone since the first public release. It turns
the original Linux voice loop into a complete, cross-platform AI desktop
assistant with persistent memory, safer local automation, and a production CI/CD
pipeline.

- **Cross-platform architecture:** The platform adapter layer now supports Linux,
  Windows, and macOS with OS-specific audio, window, and system controllers while
  keeping the agent, router, memory, TTS, and GUI core OS-agnostic.
- **ChatGPT-style UI with streaming audio:** A floating PyQt5 card with a
  breathing orb, live waveform, mic/Stop controls, barge-in, and continuous
  `sounddevice` streaming at 24 kHz for smooth, low-latency speech.
- **Advanced intelligence:** Intent-based Tool Pruning cuts tool payload tokens
  by about 87%, the semantic cache serves paraphrased stable questions, and the
  sub-agent digest condenses large web/vision outputs before they reach the main
  context.
- **CodeAct:** A guarded `fn_execute_python` tool lets Jarvis write and run short
  Python programs for multi-step tasks while an AST allow/deny pass and timeout
  block dangerous operations.
- **Circuit Breaker:** A shared CLOSED/OPEN/HALF_OPEN breaker stops repeated
  transient API failures, avoids retry storms, and recovers automatically after a
  cooldown.
- **Long-Term Memory:** SQLite-backed `user_facts` persistence remembers names,
  preferences, and other personal details across sessions. The agent can save
  and recall facts through tools without changing the static prefix-cached system
  prompt.
- **Ollama local LLM integration:** Optional, offline, and opt-in local inference
  through an OpenAI-compatible Ollama endpoint, with a graceful fallback message
  when the local server is unavailable.
- **Full CI/CD pipeline:** GitHub Actions runs the test suite on Ubuntu, Windows,
  and macOS across Python 3.10 and 3.11, including offscreen Qt and platform
  dependency setup.
- **Tests:** **488 passed, 1 skipped** at release time.

## Audio Overhaul & Critical Bug Fixes (2026-10-19, v1.7.0)

- **Audio:** Replaced Pygame chunking with a continuous `sounddevice.RawOutputStream`
  (24000 Hz, int16) with bounded backpressure for smooth, stutter-free TTS.
- **Vision:** Updated Groq vision handling after `llama-3.2-11b` was
  decommissioned; live-verified `qwen/qwen3.8-27b` and added a graceful
  API-key/rate-limit fallback message.
- **Barge-in:** Lowered the RMS threshold to **1200** and hold to **0.3 s**.
  Added explicit voice-stop phrases ("stop", "shut up", "quiet").
- **Exit & Mic:** Added voice commands for "close yourself", "shut down Jarvis",
  "turn off the mic", "turn on the mic", and related variants.
- **Media:** `play_youtube()` now mutes the mic and pauses barge-in to prevent
  audio conflicts, with GUI button/tray synchronization.
- **Tests:** Expanded suite to **466 passed, 1 skipped.**

## Creator Identity & CI/CD Pipeline (2026-10-19, v1.6.0)

- **Identity:** Added strict rules in the system prompt and offline router to
  ensure Jarvis proudly identifies **Jakir Hussain Khan** as his creator.
- **CI/CD:** Added GitHub Actions (`.github/workflows/ci.yml`) to automatically
  run the 448+ test suite across Linux, Windows, and macOS on every push.
- **Tests:** **448 passed, 1 skipped.**

## Advanced Intelligence: Semantic Cache, Sub-agent & CodeAct (2026-10-19, v1.5.0)

- **Semantic Cache:** Added `semantic_cache.py` using `fastembed`
  (`all-MiniLM-L6-v2`) and SQLite. It returns cached answers for semantically
  similar queries (cosine similarity >= 0.90) while bypassing dynamic queries
  such as time, weather, and system status.
- **Sub-agent Digest:** Added `digest.py` to condense large outputs (>= 12,000
  chars) from vision and web scraping into ~50-token summaries using a cheap LLM
  or a local extractive summary, preventing context bloat.
- **CodeAct:** Added the `fn_execute_python` tool. Jarvis can now write and
  execute temporary Python scripts for multi-step tasks. It is secured with an
  AST-based safety guard that blocks dangerous imports (`os`, `subprocess`,
  `shutil`, etc.) and `eval`/`exec`.
- **Tests:** Expanded the suite to cover the new features and safety guards;
  **441 passed, 1 skipped**.

## Streaming TTS & API Circuit Breaker (2026-10-19, v1.4.0)

- **Streaming TTS:** Refactored TTS to pipe `edge_tts` MP3 chunks into `ffmpeg`
  and play 0.2 s PCM blocks via `pygame` immediately, reducing audio latency
  from ~2-3 s to near-zero. Stop and barge-in still cut the stream instantly,
  and the GUI waveform is driven by the decoded PCM envelope.
- **Circuit Breaker:** Added `resilience.py` with CLOSED, OPEN, and HALF_OPEN
  states. Trips after 3 transient API failures, skips API calls for 30 s, and
  falls back to the local router or a graceful apology. A successful call resets
  the failure count; after the cooldown, one half-open probe tests recovery.
- **Tests:** Expanded suite to **414 passed, 1 skipped**.

## ChatGPT-Style UI, Barge-in & New Icon (2026-10-19, v1.3.0)

- **UI Overhaul:** Frameless, always-on-top dark theme window (ChatGPT Voice Mode
  style) with smooth pill controls and a live transcript.
- **Animations:** Breathing glowing orb (`QPropertyAnimation`) and a live 13-bar
  audio waveform synced to mic/TTS levels.
- **Barge-in:** Users can interrupt Jarvis by speaking. Implemented via a daemon
  `BargeInMonitor` with a 0.5 s high-threshold (`2600`) RMS check and echo
  mitigation.
- **Icon:** New futuristic `J` orb icon generated via `gen_icon.py`.
- **Tests:** Updated `test_gui.py` and the suite now passes with **396 passed,
  1 skipped**.

## P0 Stability & P1 Groq Optimization (2026-10-19, v1.2.0)

- **Security:** Sandboxed `fn_read_file` with an allowlist and `Path.resolve()` to
  prevent directory traversal.
- **Memory:** Migrated from an in-memory deque to SQLite (`~/.jarvis/memory.db`)
  with thread-safe locking (`RLock`).
- **STT Fallback:** Added local `faster-whisper` (`base`, `int8`) as a fallback
  when Google STT fails due to network issues.
- **Models:** Primary model set to Groq `openai/gpt-oss-120b` with a 429
  rate-limit fallback to `llama3-8b-8192`. Vision remains on `qwen/qwen3.8-27b`.
- **Tool Pruning:** Reduced the 40+ tool schemas sent to the LLM down to 3-5 based
  on intent classification, saving ~87.5% of tool payload tokens.
- **Prefix Cache:** Ensured the system prompt is static and placed at index 0.
- **Tests:** Expanded the suite to **386 passed, 1 skipped**.

## Cross-Platform Refactor & Installers (2026-10-19, v1.1.0)

Jarvis is no longer Linux-only. The whole platform layer was refactored behind a
**Platform Adapter Pattern**, and the install path became a one-liner on every OS.

- Refactored the entire Linux-only architecture into a **Platform Adapter Pattern**.
- Created `platform_adapters/` with `base.py` (the `AudioController` /
  `WindowController` / `SystemController` interfaces), `common.py` (the shared
  process/session helpers) and the `linux/`, `windows/` and `macos/`
  implementations, picked at runtime by the factory in `__init__.py`.
- `desktop.py` and `voice.py` are now OS-agnostic facades: every hard-coded Linux
  command (`arecord`, `pactl`, `playerctl`, `wmctrl`, `xdotool`, `brightnessctl`,
  …) lives only in the adapters, and a missing library degrades to a clear message
  instead of crashing.
- Added `install.sh` (Linux/macOS: venv + apt/brew dependencies) and
  `install.ps1` (Windows: venv + the `pycaw`/`pywinctl`/`pyautogui` extras).
- Tests passed: **`363 passed, 1 skipped`**.

## Repository & releases

- Remote: <https://github.com/jakirhussainkhan/jarvis-ai-voice-assistant>
- Default branch: `main`
- Release tags: **`v1.0.0`** (initial), **`v1.0.1`** (screen vision, live
  internet, guarded terminal, offline-first, terminal hardening),
  **`v1.0.2`** (voice-chat widget: mic mute/unmute, Stop, live reactive orb),
  **`v1.0.3`** (play a song by name / pick one off the screen; 30 s listen window;
  Stop cancels a wait; copy-paste install hints), **`v1.0.4`** (no more
  mid-sentence cut-offs; silent screen reading, no flash), **`v1.0.5`**
  (optional offline "Hey Jarvis" wake word), **`v1.0.6`** (desktop launcher:
  `run.sh`, an application-menu entry and autostart), **`v1.0.7`** (a stronger,
  Iron-Man voice: the British male `en-GB-RyanNeural`, env-switchable) and
  **`v1.0.8`** (continuous conversation: Jarvis keeps the mic open after every
  reply until you go quiet, say "bye", press Stop or mute) and **`v1.0.9`**
  (an Iron Man arc-reactor icon: gunmetal housing, ten glowing coil slots and
  a white-hot core, regenerated into all `assets/*.png`) and
  **`v1.0.10`** (YouTube tab intelligence: reuse the open tab in place,
  close it from anywhere, verified-focus keyboard delivery) and
  **`v1.0.11`** (the clickable orb now paints the same Iron Man reactor
  as the icon, through one shared painter, tinted per state).
- Secrets are **not** committed (see *Security* below).

## The clickable orb becomes the Iron Man reactor (2026-10-19, v1.0.11)

The icon was Iron Man, but the orb the user actually clicks still painted its
own old flat-blue look -- two artists for one robot, and they had drifted.

- **One painter for both.** The whole treatment (carbon backplate, gunmetal
  housing annulus, ten coil slots, rims, halo, white-hot core, specular glint)
  moved into `gui._draw_reactor_face(painter, center, radius, glow,
  core_light, energy, breathe)`. `make_icon()` calls it statically at full
  energy; `Reactor.paintEvent()` calls it every frame with the state colours
  and the live breathe/energy values. (Done with the manually verified state
  renders for all five states -- idle, listening, thinking, speaking, muted --
  eyeballed before anything shipped.)
- **State signalling survived the redesign.** `glow` tints the coil light and
  the halo, `core_light` cools the core edge, so Listening still reads green,
  Thinking amber, Speaking purple, Muted a desaturated grey -- plus the same
  slash badge. The breathing shimmer and the energy pulse now scale the halo
  and core radius instead of an old-style background glow.
- Also caught: the truncation step for this extraction initially left the old
  make_icon body behind after its `return`; the dead block was removed and the
  module re-compiled. Pixel tests pin the orb: the listening slot is
  green-dominant, the core white-hot, and the muted orb desaturated.
- `pytest`: **363 passed, 1 skipped** (was 361).

## YouTube tab intelligence (2026-10-19, v1.0.10)

The user's media complaints were about *tab hygiene*: with YouTube already
open, "play X" opened yet another tab, and "close YouTube" answered "Which
window?" instead of just closing the tab.

- **Reuse in place.** `_reuse_youtube_tab(url)` focuses the visible YouTube
  window, presses ctrl+l, types the url and hits Return -- so "play despacito"
  replaces the tab the user was browsing (play and search reuse it; "open
  youtube" brings the tab up). Fallbacks keep the old `open_url` behaviour
  whenever this is not possible, so nothing ever regresses to silence.
- **Found on the live machine:** Cinnamon's focus-stealing prevention silently
  swallowed `xdotool windowactivate` -- the activation rc was 0 but input focus
  stayed on the terminal, and the typed URL landed nowhere. Typing *without*
  verified focus is unacceptable, so every keyboard action now verifies
  `xdotool getwindowfocus` (with `windowfocus` as the direct-X fallback probe)
  and refuses honestly when the window never took focus.
- **Close from anywhere.** `close_youtube()` targets the visible YouTube window
  directly; without a visible YouTube tab it closes the focused browser tab
  only when a browser media session is live there (MPRIS is published by the
  playing tab alone), and answers "There is no YouTube tab open." otherwise.
  A regression test pins that a browser focused on an essay is never ctrl+w'd.
- Live-verified on the machine: the YouTube window is found and the test tab
  was opened and closed through the new code paths; the typed navigation is
  stub-tested (7 unit tests) because focusing another window from a test run
  is inherently machine-valid only.
- `pytest`: **361 passed, 1 skipped** (was 355).

## An Iron Man arc-reactor icon (2026-10-19, v1.0.9)

The user asked for an icon with a real "Iron Man vibe" — to look at it and feel
like Tony Stark. The old icon was a flat blue orb with a silver ellipse: it read
as a porthole. The new one is a cinematic arc reactor, drawn by `gui.make_icon()`:

- **A dark gunmetal housing** (a machined bevelled annulus, painted as a
  `QPainterPath` with an interpolated radial-gradient ring) so the only bright
  pixels are light, not metal.
- **Ten glowing coil slots** — true annular sectors (0.615–0.96 of the radius,
  10.5 degree half-angles) filled white-hot to electric blue, plus two slim
  translucent edge strokes that read as light bleeding off the coils.
- **A white-hot core**: a solid white disc inside a fading light halo, with a
  small specular glint.
- **One real bug found while iterating:** `QPainter.drawPath()` both strokes and
  fills, and the painter was still holding the housing brush when the slot
  strokes were drawn — the housing gradient was painting over the slot fill,
  which is why earlier renders read as dark tiles. `setBrush(Qt.NoBrush)` before
  the strokes fixed it.
- All five `assets/jarvis-*.png` regenerated and `install_desktop.py` re-run, so
  the app-menu entry shows the new icon immediately. New pixel tests pin the
  look: a white-hot core (min(R,G,B) > 230), an electric-blue top slot
  (B > 190) and a slot that clearly out-glows its housing gap.
- `pytest`: **355 passed, 1 skipped** (was 354).

## Continuous conversation, like ChatGPT's voice mode (2026-10-19, v1.0.8)

The user was frustrated at having to re-trigger Jarvis for every sentence: click
(or "Hey Jarvis"), speak, get an answer — and click again. The ask was explicit:
"a ChatGPT-voice-like system: I keep talking, it keeps listening, and it only
turns the mic off when I say nothing for a minute."

- **The worker now runs a conversation loop.** `JarvisWorker` had exactly one
  turn (listen → handle → speak → sleep). It now loops turn after turn while
  continuous mode is on: after each reply it emits `Listening` and opens the
  microphone again. The first turn of a session still waits for a click/wake;
  every turn after that waits `JARVIS_CHAT_TIMEOUT` (**60 s**, default on,
  `JARVIS_CHAT=0` for the old behaviour) instead of the 30 s one-shot window —
  implemented as a `listen(wait=…)` parameter threaded down to
  `_capture_from_stream`, so the arecord stream is bounded either way.
- **Four ways out, all of them verified:** an exit word spoken by voice
  ("bye"/"stop"/"exit" — `main.is_exit`, closing the session with
  "Session over. See you soon, sir."), silence past the chat timeout, the Stop
  button (checked after `speak()` returns — checking it after the *next* listen
  would have left Jarvis holding the mic for 60 s after a Stop), and the mic
  mute (a muted listen returns "" which ends the loop). A stale Stop flag is
  cleared once at the start of a session so it cannot kill a fresh chat.
- **"Are you sure?" stays, by voice.** The user explicitly wants confirmation
  before shutdown — *and* wants to be able to answer it by mouth. Verified
  end-to-end and safely through the real router with a scripted pipe:
  "shut down" → "Are you sure you want to shutdown?" → "no" → "Cancelled."
  (nothing executed). A spoken "yes" within the 15 s window runs the command.
- **Wake word co-exists.** The wake listener stays paused for the whole session
  (trigger() owns the mic), and the existing Idle → resume logic hands the mic
  back when the conversation ends.
- Both menu surfaces gained a checkable **Continuous chat** item, mirroring the
  wake-word toggle; it switches on/off from the next session onward.
- `pytest`: **354 passed, 1 skipped** (was 350). New GUI worker tests: a
  multi-turn session that ends on silence, waiting 60 s (not 30) between turns,
  ending on a spoken exit word, Stop ending the conversation without grabbing
  the mic again, and the wake listener being resumed when the session drops to
  Idle. The single-turn worker test became a one-exchange-then-silence session.

## A stronger, Iron-Man voice (2026-10-19, v1.0.7)

The spoken voice is the assistant's personality, and it was a woman. The user
asked for a "strong man voice like Iron Man's Jarvis", so it is now **British
male Ryan (`en-GB-RyanNeural`)** — the closest neural match to Iron Man's Jarvis
(a British English accent, as in the films).

- **An audition, not a guess.** Four male candidates (Ryan, Thomas, Guy, Davis)
  were rendered with the same line both natural and pitch-shifted (−25/−30 Hz)
  and **played through the speakers**; Davis turned out to be unavailable
  (`NoAudioReceived`). The user picked **Ryan, natural** — deepened clips read as
  slightly artificial, so the chosen voice ships with no pitch shift.
- **It is now configurable.** `TTS_VOICE` was hardcoded to `en-US-AriaNeural`;
  it reads `JARVIS_TTS_VOICE` (default `en-GB-RyanNeural`), consistent with
  `MODEL_NAME`/`VISION_MODEL`, so any other edge-tts voice is a one-line `.env`
  change, e.g. `JARVIS_TTS_VOICE=en-US-AriaNeural` to go back.
- **Verified live through the real path:** `voice.speak(...)` ran end to end with
  the new voice (synthesis, envelope, pygame playback) with no error.
- Tests unchanged (350 passed, 1 skipped): speech helpers test the envelope and
  interruption, not the voice name.

## Desktop launcher: `run.sh` + an app-menu entry (2026-10-18, v1.0.6)

Starting the GUI needed `cd /home/jakir/my-project/jarvis && ./venv/bin/python
main.py --gui` every time, with no icon in the menu. Now:

- **`run.sh`** — resolves its own directory, checks the virtualenv exists (and
  prints the exact `python3 -m venv` + `pip install -r requirements.txt` lines if
  it does not), then `exec`s `main.py --gui`, so Ctrl+C works and no shell is left
  hanging. It is `chmod +x`, so `./run.sh` just works.
- **`install_desktop.py`** — writes
  `~/.local/share/applications/jarvis.desktop` pointing at *this* checkout
  (`Exec`, `Path` and `Icon` are all absolute), so Jarvis appears in the Cinnamon
  application menu. `--autostart` also writes `~/.config/autostart/jarvis.desktop`
  so it is already in the tray after login; `--uninstall` deletes **exactly** the
  two files it wrote and nothing else. It only ever touches the user's own home
  directory — no root, no `sudo` — and it can be pointed elsewhere with `--home`,
  which is what makes it testable without touching the real `~`.
- **`assets/jarvis-{32,48,64,128,256}.png`** — the arc-reactor icon, rendered from
  the same `gui.make_icon()` the tray uses, so the menu icon and the tray icon can
  never drift apart.
- **Icon fix found meanwhile:** `make_icon()` used a fixed `1.5 px` margin, which
  is fine at 64 px but visibly **clipped the sphere at 256 px**. The margin is now
  proportional (`max(1.5, size * 0.04)`), and a test asserts the corners and edge
  midpoints stay transparent.

### Verification

- `pytest`: **350 passed, 1 skipped** (was 328). New `test_launcher.py` (21 tests)
  plus an icon-clipping test in `test_gui.py`. Installs go to a `tmp_path` "home"
  and the menu-cache refresh is stubbed, so the suite still never touches `~`.
- `desktop-file-validate` reports **no errors** on the generated entry.
- `./run.sh` really starts the GUI (checked offscreen), and with no `venv/` it
  exits 1 with a copy-paste fix instead of a traceback.

## Offline "Hey Jarvis" wake word (2026-10-18, v1.0.5)

The GUI no longer needs a click to start talking: say **"Hey Jarvis"**.

### How it works

- **Engine:** `openWakeWord` — a small ONNX model from a purpose-built
  wake-word project (`dscripka/openWakeWord`). It scores 80 ms frames of 16 kHz
  audio between 0 and 1 and runs **entirely on this machine**: no network, no
  account, no API key, ~1-2 % of one CPU core. The pretrained `hey_jarvis` model
  is English, which matches Jarvis's English-only rule.
- **`wake.py`** holds both pieces, and neither imports the engine until it is
  actually used, so `import wake` works on a machine without it:
  - `Detector` — re-frames the raw mic bytes into the model's 80 ms frames
    (`1280` samples = `2560` bytes), applies `JARVIS_WAKE_THRESHOLD` (default
    `0.5`, the value the models are trained for), and drops the rest of the
    buffer on a hit so one phrase means exactly one wake.
  - `Listener` — a daemon thread that streams `arecord` into the detector and
    calls `on_wake()` on a hit.
- **The microphone is never fought over.** `voice.listen()` spawns its own
  `arecord`, so the two must never hold the device at once. The listener
  therefore **closes its stream before calling `on_wake`**, and then waits (up to
  90 s) for the GUI to hand the device back when the cycle returns to *Idle*.
  `pause()` blocks until the stream is really closed, so `listen()` can never
  start while the wake listener still owns the mic. Symmetrically, `trigger()`
  pauses the listener **first** — whether the cycle came from the wake word or a
  click.
- **Muting means muting.** The listener also stops on its own while
  `voice.is_mic_muted()` is true, and the mic button calls `pause()`, so a muted
  microphone is genuinely silent (no wake word).
- **Feedback:** a soft two-tone chime (generated in memory with `wave` and played
  through the existing `pygame` mixer — no new asset, no new dependency),
  `JARVIS_WAKE_DING=0` to silence it.
- **GUI wiring (`gui.py`):** the listener runs on its own thread, so it must
  never touch Qt — it emits a `wake_heard` signal that Qt delivers on the GUI
  thread, and that slot simply calls `trigger()`. So the wake word path is
  *identical* to a click: same `JarvisWorker`, same confirmation, same routing.
  Both menus (widget and tray) get a checkable **Hey Jarvis** item that starts or
  stops the listener at runtime; a failed microphone or a missing model only
  prints a hint and leaves click-to-talk working.

### Honesty about the state

- **Written and unit-tested on this machine, never run live.** `openwakeword` +
  `onnxruntime` are a few hundred MB and are **not** in `requirements.txt`; the
  user installs them with
  `./venv/bin/pip install openwakeword onnxruntime`. Until then, `wake.available()`
  is False, `doctor.py` prints that exact line, and nothing else changes.
- What the tests *do* cover (37 new tests): the framing/threshold logic against a
  fake scoring backend, the hit-drops-the-buffer rule, a broken backend, and the
  whole `Listener` thread against a fake microphone — including the two rules that
  matter most: **the mic is released before `on_wake`** and **a muted mic is never
  opened**. The GUI wiring is covered offscreen with a fake listener and a fake
  worker. The one thing only the real machine can prove is whether the model
  recognises *your* voice — `JARVIS_WAKE_THRESHOLD` tunes that.
- The pretrained models are **CC-BY-NC-SA 4.0** (non-commercial). openWakeWord's
  code is Apache-2.0. Neither is bundled here; `pip` fetches them.

### Bugs found and fixed while building it

1. **`threading.Thread` already has a private `_stop` method**, so naming the
   shutdown event `self._stop` made `join()` raise `TypeError: 'Event' object is
   not callable`. Renamed to `_stop_event`.
2. **A typo in `.env` could stop Jarvis starting.** `float(os.getenv(...))` on the
   threshold ran at import time, so `JARVIS_WAKE_THRESHOLD=loud` would have
   crashed `import wake` → `import gui` → Jarvis. Added `_env_float()`, which falls
   back to the default.
3. **Unmuting could leave the wake word dead.** Muting mid-cycle moves the status
   label off *Muted* (the worker resets it to *Idle*), so the unmute branch's
   `set_state(IDLE)` never ran and the mic was never handed back. Unmuting now
   always calls `_resume_wake()`.
4. **A wedged caller could kill the wake word for good.** After a hit the listener
   waits for `resume()`; a 90 s timeout used to leave it paused *forever*. It now
   re-arms itself and keeps listening.
5. **An empty read could hot-spin the thread.** A stalled or fd-less stream now
   naps 20 ms instead of spinning the loop at 100 % CPU.
6. **`openwakeword.utils` is not imported by the package's `__init__`** — relying
   on `openwakeword.utils.download_models()` was luck. It now imports
   `from openwakeword.utils import download_models` explicitly.

### Verification

- `pytest`: **328 passed, 1 skipped** (was 286). New `test_wake.py` (29 tests) and
  8 new wake-word tests in `test_gui.py` + 3 in `test_doctor.py`.
- The API was read from the installed-shape source, not guessed: `openWakeWord`'s
  `Model(wakeword_models=[...], inference_framework="onnx")` resolves `"hey jarvis"`
  to `hey_jarvis_v0.1` by replacing the space, `predict()` takes a numpy `int16`
  array and returns `{name: score}`, and `reset()` exists.
- `main.py --version` → `Jarvis 1.0.5`; `doctor.py` prints the wake-word line and
  the copy-paste `pip install openwakeword onnxruntime` when it is missing.
- **Live on this machine, real microphone** (the model faked, everything else
  real): the listener streamed a real `arecord` (3 × 3200-byte chunks), the mic
  was **provably free the moment `on_wake` fired** — a *second* real `arecord`
  captured 3200 bytes while it was paused — `resume()` made it take the device
  back (the detector saw a 4th chunk), `stop()` ended the thread cleanly, and the
  chime played without raising.
- **Still not run live:** the pretrained model recognising a real voice (needs the
  ~hundreds-of-MB `pip install`), i.e. whether *your* "Hey Jarvis" scores above
  the threshold.

## No more cut-offs, and silent screen reading (2026-10-18, v1.0.4)

Two things the user hit while actually using it.

- **It answered before the sentence was finished.** Two hard limits were too
  tight: the phrase ended after **1.2 s** below the calibrated floor, and the whole
  utterance was **capped at 6 s**. A natural thinking pause (e.g. between "play …"
  and the song name) therefore truncated the request. Both are now generous and
  tunable: `JARVIS_SILENCE_END` (default **2.0 s**) and `JARVIS_PHRASE_LIMIT`
  (default **15 s**). The silence chunk count is derived at capture time (as the
  phrase cap already was), so an env change always applies. `_RECOGNIZE_TIMEOUT`
  went 8 s → 15 s to match the longer audio, and env parsing moved into small
  `_env_int` / `_env_float` helpers that clamp and never raise on bad input.
- **Reading the screen made the screen flash.** `desktop.capture_screenshot()`
  tried `gnome-screenshot` **first**, and on Cinnamon it plays a bright shutter
  animation — worse, `vision.act()` captures once per step (up to 6 times). That
  version has no flag to disable the flash, so the order is now **silent first**:
  `scrot`, then ImageMagick `import`, and `gnome-screenshot` only as a last resort.
  The failure message and `doctor.py` now say so too.

Verified live: `vision.capture_png()` runs `scrot` → `convert` in **0.37 s** and
invokes `gnome-screenshot` **not at all**; new tests assert a silent tool is used
whenever one is present, and that `gnome-screenshot` is only reached when nothing
else exists.

Also fixed while here: the synthetic capture tests fed 1.5 s of trailing silence and
relied on the old 1.2 s window to end the phrase; with a 2 s window they ran to the
stream deadline instead (the suite went 11 s → 69 s). The test pipe now **closes its
write end** once the writer is done, so the reader gets a real EOF and finishes
promptly — the suite is back to **2.3 s** — and the `_capture` helper pins the
windows so the tests stay deterministic.

**Files:** `voice.py`, `desktop.py`, `doctor.py`, `config.py`
(`__version__ = "1.0.4"`), `test_voice_capture.py`, `test_screen.py`, `README.md`.

## YouTube flow, a 30 s listening window & install hints (2026-10-18, v1.0.3)

The user's YouTube feed shows songs; asking Jarvis to play one by name had to
work, and pause/next had to work on it. Also: Jarvis gave up on listening after
only **4 s**, which felt far too fast.

- **Play a song by name.** A bare `play <name>` (no "youtube" word) now plays the
  top `yt-dlp` result: `"play kesariya"` and `"play the song kesariya"` both call
  `desktop.play_youtube("kesariya")`. A filler-only "play a song" / "play the
  song" still asks **"What should I play?"**.
- **Pick a video off the screen.** Reference phrases — `"play the second song"`,
  `"play this song"`, `"play that one"`, `"play it"`, `"play the video on my
  screen"` — route to the vision screen-action path (`tools.screen_do`), so Jarvis
  looks at your screen and clicks the thing you mean. A *named* play never does
  (it goes straight to `yt-dlp`), and `close/quit/kill X` still never reaches the
  search branch.
- **30 s to start speaking.** `_LISTEN_TIMEOUT` went from **4 s → 30 s**
  (`JARVIS_LISTEN_TIMEOUT` overrides it). This is the window to *begin* speaking;
  a single phrase is still capped at 6 s (**15 s** since v1.0.4).
- **Stop cancels the wait.** New `voice.stop_listening()` sets an event checked
  every 0.1 s chunk, so the GUI **Stop** button (or `Space`) abandons a listen
  that is still waiting for you instead of making you wait out the window. The
  Stop button is now live for both `LISTENING` and `SPEAKING`.
- **Copy-paste install hints (policy change).** The user asked: if Jarvis cannot
  install something without `sudo`, print a pure copy-paste command for them to
  run in their own terminal. So `media_control` / `brightness_control` and
  `doctor.py` now print the exact `sudo apt install …` line (doctor de-duplicates
  the packages of everything missing). **Jarvis itself still never runs `sudo`** —
  it only prints the command. (This reverses the earlier v1.0.1 decision to keep
  the doctor sudo-free; the user asked for it explicitly.)
- `desktop.py` `playerctl`/`brightnessctl` messages were also brought back from
  "isn't installed, so I can't…" to the informative form with the install command
  (which is what the existing tests assert).

Verified live: the default wait prints **30 s**; with `JARVIS_LISTEN_TIMEOUT=6` a
silent `listen()` returned in ~6.2 s (a cold first run took ~9.6 s while ALSA
opened the device); a `stop_listening()` fired 1.5 s into a 30 s wait returned in
**1.7 s**; `yt-dlp` resolves `kesariya` → `BddP6PYo2gs | Kesariya - Brahmāstra…`;
and the whole YouTube flow was run end-to-end in **Brave** (see below).

### Live end-to-end test found two real bugs (and they are fixed)

Running `desktop.play_youtube("kesariya")` on the real machine exposed problems
that no unit test would have caught:

1. **The video opened but never played, and then nothing could control it.**
   Browsers refuse unmuted autoplay until the *site* has seen a user gesture, and
   a spoken command is not one — so the tab sat paused. Worse, a paused tab
   publishes **no MPRIS session at all** (`playerctl -l` → *No players found*),
   so "pause" answered *"There is no media player to control."*
   **Fix:** `desktop.ensure_browser_playing()` — after opening the watch URL,
   `play_youtube` starts a daemon thread that polls `playerctl -l` for a browser
   media session and, if none appears, finds the YouTube window and sends it the
   page's own play/pause key (`k`) once. It runs off-thread so the spoken reply is
   never delayed, and it never touches a video that autoplayed on its own (a
   second `k` would *pause* it). Verified live: MPRIS appeared ~4 s after
   `play_youtube("kesariya")` and the status was `Playing`.
2. **`next` could never work through playerctl.** Brave's MPRIS advertises
   **`CanGoNext = false`**, so `playerctl next` fails with *"No player could
   handle this command"* (rc 1) and Jarvis said "There is no media player to
   control."
   **Fix:** `media_control` now falls back to the page's own shortcut in the
   YouTube window (`shift+n` / `shift+p`) when playerctl refuses — activating the
   window first (Chromium ignores synthetic keys sent to an unfocused window) and
   restoring the previous focus afterwards. Only `next`/`previous` use the
   fallback: `k` is a *toggle*, so using it for "pause" could start a stopped
   video instead of pausing it — that case stays honest instead.

Verified live in Brave, from a clean state (no MPRIS player, no YouTube tab):

| Step | Result |
| --- | --- |
| `play_youtube("kesariya")` | *"Playing kesariya on YouTube."*, MPRIS appeared ~4 s later |
| status | `Playing` — *Kesariya - Brahmāstra…* |
| `media_control("pause")` | *"Media pause."* → status `Paused` |
| `media_control("next")` | *"Media next."* → the new video loaded and played (*Gerua…*) |
| `media_control("resume")` | *"Media resume."* → status `Playing` |
| `now_playing()` | the live title, read from MPRIS |

The new tests (`test_desktop.py`): the `shift+n`/`shift+p` fallback, the honest
failure when there is no YouTube window, "pause never toggles a stopped video",
`ensure_browser_playing` pressing `k` exactly once, leaving autoplay alone, and
requiring X11 + the tools; plus one asserting `play_youtube` runs the nudge in the
background.

**Remaining honest limits.** The autoplay tap needs `xdotool` and X11 (on Wayland
it is skipped, so a video may stay paused until you click it once). If you opened
the YouTube tab yourself, Jarvis still fixes "next" but cannot start a video it
did not open. `vision.act` for the on-screen picks ("play the second song") was
still **not run against the real UI** — it moves the real mouse — though its
routing, whitelist and risky-goal confirmation are tested.

**Files:** `voice.py`, `gui.py`, `router.py`, `tools.py`, `agent.py`, `desktop.py`,
`doctor.py`, `config.py` (`__version__ = "1.0.3"`), `test_routing.py`,
`test_screen_routing.py`, `test_voice_capture.py`, `test_voice_speech.py`,
`test_gui.py`, `test_doctor.py`, `test_desktop.py`, `README.md`.

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
  guarantees, a PyQt5 + clipboard-read check, and how to try every mode. (In
  v1.0.3, at the user's request, it gained a copy-paste `sudo apt install` line
  for missing tools. Jarvis never runs it — it only prints it.)

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
  floor, the phrase ends after ~**2 s** below it (`JARVIS_SILENCE_END`, was 1.2 s),
  caps phrases at **15 s** (`JARVIS_PHRASE_LIMIT`, was 6 s), and
  waits at most **30 s** (`JARVIS_LISTEN_TIMEOUT`, was 4 s) for speech to start.
  Playing media is paused before the mic
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
  Media control uses `playerctl`, with a browser-tab fallback (`k` to start a
  freshly opened video, `shift+n`/`shift+p` for next/previous) when a page's MPRIS
  cannot do it. X11/Cinnamon here; on Wayland window ops degrade to "Which window?".
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
- `install_desktop.py` — app-menu launcher entry (`--autostart`, `--uninstall`,
  `--home`); writes only inside the user's own home directory.
- `run.sh` — starts the GUI through the venv without needing `cd`.
- `assets/jarvis-*.png` — the arc-reactor icon, rendered from `gui.make_icon()`.
- `gui.py` — PyQt5 Arc Reactor widget (`QThread` worker); shares `main.handle()`
  so confirmations work in the GUI too. Owns the wake listener's lifecycle
  (start / pause / resume / stop) and exposes a checkable **Hey Jarvis** item in
  both menus.
- `wake.py` — optional offline **"Hey Jarvis"** wake word (`openWakeWord` ONNX).
  `Detector` re-frames raw mic bytes into the model's 80 ms frames and applies the
  threshold; `Listener` streams `arecord` on a daemon thread, releases the mic
  *before* a listen cycle and takes it back when Jarvis is idle, and stays silent
  while the mic is muted. Imports fine without openWakeWord installed.
- `test_mic.py` — standalone mic diagnostic (arecord-based): lists capture
  devices/PCMs with `arecord -l` / `-L`, records a WAV with the same
  `arecord_command()` as `listen()`, reports RMS/peak, plays it back with `aplay`,
  recognises it, and checks the gate in `voice.py`. No PyAudio.
- `test_routing.py`, `test_desktop.py`, `test_confirm.py`, `test_exit.py`,
  `test_memory.py`, `test_agent_cache.py`, `test_flow.py`, `test_voice_capture.py`,
  `test_wake.py`
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
faster-than-realtime stream still obeys the cap). The wait-to-start timeout is
kept (4 s then; **30 s** since v1.0.3), and `select()` bounds each read so a
stalled `arecord` can never block past it. Captured bytes are wrapped in `sr.AudioData(data, 16000, 2)` and passed to
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
- Phrases are capped at **15 s** (was 6 s, originally 8 s); a pause of **2 s**
  (`JARVIS_SILENCE_END`) now ends the phrase instead of 1.2 s, so a thinking pause
  mid-sentence is no longer treated as the end. Both are env-tunable (v1.0.4).
- Jarvis waits up to **30 s** for you to start speaking (was 4 s); set
  `JARVIS_LISTEN_TIMEOUT` to tune it, and the GUI **Stop** button abandons a
  pending wait.
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
- **Pause / next** were exercised live against real Brave playback (see the
  v1.0.3 section): `pause`, `resume` and `next` all work. `next`/`previous` need
  `xdotool` + X11 because they are delivered to the YouTube page (`shift+n`) —
  a browser's MPRIS cannot skip tracks.
- The **autoplay tap** after `play_youtube` needs `xdotool` + X11; on Wayland it
  is skipped and the video may stay paused until you click it once.
- The **on-screen picks** ("play the second song", "play that one") route to
  `vision.act`, which was **not run against the real UI** (it moves the real
  mouse). The routing, the whitelist and the risky-goal confirmation are tested.
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
short or abandons a listen that is still waiting, the orb reacts to your voice while listening and moves with its own speech
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
JARVIS_LISTEN_TIMEOUT=60 ./venv/bin/python main.py # wait longer for you to start speaking (default 30)
JARVIS_SILENCE_END=3 JARVIS_PHRASE_LIMIT=30 ./venv/bin/python main.py  # don't cut a long sentence
JARVIS_TEXT=1 JARVIS_MUTE=1 ./venv/bin/python main.py  # type commands, no mic / no TTS
```
