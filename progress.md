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
| Local command router | Working |
| Groq agent + tools | Working (needs `GROQ_API_KEY`) |
| Conversation memory | Working |
| GUI widget (`gui.py`) | Working |
| Microphone diagnostic | Working |
| Tests | `pytest` suite — 39 tests |
| Published | GitHub, tag `v1.0.0` (README + MIT LICENSE) |

## Repository & releases

- Remote: <https://github.com/jakirhussainkhan/jarvis-voice-assistant>
- Default branch: `main`
- Release tag: **`v1.0.0`** — contains the full project, `README.md`, and `LICENSE`.
- Secrets are **not** committed (see *Security* below).

## How it works

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

Two front ends share the same brain:

- `main.py` — terminal loop. Prints `[STAT]` lines with token counts, and tracks
  local vs. agent vs. cache hits.
- `gui.py` — floating Arc Reactor widget. Left-click talks, drag moves,
  right-click/Esc quits. The listen/think/speak cycle runs on a `QThread` so the
  UI never freezes.

## Module map

- `config.py` — loads `.env`, holds `API_KEY` (from `GROQ_API_KEY`), `BASE_URL`
  (`https://api.groq.com/openai/v1`), `MODEL_NAME` (`openai/gpt-oss-120b`),
  `STT_LANG` (`en-US`), `TTS_VOICE` (`en-US-AriaNeural`), and the
  history/output/tool limits.
- `voice.py` — `listen()` and `speak()`. `listen()` captures through
  `arecord -D <device> -f S16_LE -r 16000 -c 1` (no PyAudio/PortAudio), reads
  0.1 s chunks, computes RMS per chunk, keeps a ~0.3 s **pre-roll**, starts on
  speech above **300**, then uses **hysteresis** (keeps recording while RMS stays
  above **150**), ends after ~**1.2 s** below that, caps phrases at 8 s, and waits
  at most 4 s for speech to start. Bytes are wrapped in
  `sr.AudioData(data, 16000, 2)` and sent to Google STT (`en-US`). `speak()` is
  edge-tts + pygame (`en-US-AriaNeural`). Device defaults to `default`; override
  with `JARVIS_MIC_DEVICE`.
- `router.py` — `try_local()`: offline regex handling for volume, system status,
  time, date, YouTube and app launching, plus `close/quit/stop <app|browser>`.
  A YouTube play/search only fires with an explicit media verb; `close youtube`
  returns a "can't close browser tabs" reply instead of searching for "close".
  Any command containing a search verb (`search`, `look up`, `find`) returns
  `None` so it reaches the agent instead of being intercepted.
- `agent.py` — Groq chat orchestration over the OpenAI-compatible client, md5
  response cache, token accounting, tool schemas built from function signatures,
  and an English-only system instruction.
- `tools.py` — tool functions the model can call: open app, system status,
  YouTube, web search, save note, read file. Signatures + docstrings become the
  schema (no hand-written schema).
- `memory.py` — rolling `deque` window of recent turns, rendered as a transcript.
- `main.py` — terminal entry point and `handle()` dispatch.
- `gui.py` — PyQt5 Arc Reactor widget (`QThread` worker).
- `test_mic.py` — standalone mic diagnostic (arecord-based): lists capture
  devices/PCMs with `arecord -l` / `-L`, records a WAV with the same
  `arecord_command()` as `listen()`, reports RMS/peak, plays it back with `aplay`,
  recognises it, and checks the gate in `voice.py`. No PyAudio.
- `test_routing.py`, `test_exit.py`, `test_memory.py`, `test_agent_cache.py`,
  `test_flow.py`, `test_voice_capture.py` — pytest suite (see Verification).
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
- `MAX_OUTPUT_TOKENS = 220` may truncate long answers from the reasoning model —
  raise it if replies are cut off.
- The arecord rewrite was verified against a *recorded* speech WAV and the local
  device's ambient noise, not the real voice: run `main.py` once on the actual
  machine to confirm the 76/6172 RMS separation at 40 % gain.

## Next steps

1. Consider persisting notes/history and adding a wake word.
2. Decide on sandboxing for file/shell tools.
3. Optionally add CI (run `pytest` on push).

## How to run

```bash
python -m venv venv
./venv/bin/pip install -r requirements.txt
echo "GROQ_API_KEY=your-key" > .env   # Groq key, starts with gsk_

./venv/bin/python main.py       # terminal voice loop
./venv/bin/python gui.py        # floating reactor widget
./venv/bin/python test_mic.py   # mic diagnostic (add --list to only list devices)
./venv/bin/python -m pytest     # test suite
```

Requires a working microphone and, for the agent path, network access plus a
valid Groq `GROQ_API_KEY`. `.env`, `venv/`, caches, `TOKEN.txt` and audio
artifacts are gitignored.

Microphone tips (this machine):

```bash
amixer -c 1 sset Capture 40%                       # 100% clips; 40% keeps the floor under the 300 gate
JARVIS_MIC_DEVICE=plughw:1,0 ./venv/bin/python main.py  # force an arecord -L device (default: "default")
JARVIS_DEBUG=1 ./venv/bin/python main.py           # print per-chunk RMS / capture diagnostics
```
