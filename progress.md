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
| Voice I/O (STT + TTS) | Working, English-only |
| Local command router | Working |
| Groq agent + tools | Working (needs `GROQ_API_KEY`) |
| Conversation memory | Working |
| GUI widget (`gui.py`) | Working |
| Microphone diagnostic | Working |
| Tests | `pytest` suite — 24 tests |
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
- `voice.py` — `listen()` (SpeechRecognition + Google STT, `en-US`) and `speak()`
  (edge-tts + pygame, `en-US-AriaNeural`). Suppresses ALSA/JACK spam, selects the
  **system default input** (PipeWire / `default`, with a `JARVIS_MIC_INDEX`
  override and `ALC245` / `hw:1,0` only as a last resort), uses a **fixed** energy
  threshold with a **warm-up flush**, and bounds every call with timeouts so a
  dead mic can never stall the loop.
- `router.py` — `try_local()`: offline regex handling for volume, system status,
  time, date, YouTube (open/play only) and app launching. Any command containing
  a search verb (`search`, `look up`, `find`) returns `None` so it reaches the
  agent instead of being intercepted.
- `agent.py` — Groq chat orchestration over the OpenAI-compatible client, md5
  response cache, token accounting, tool schemas built from function signatures,
  and an English-only system instruction.
- `tools.py` — tool functions the model can call: open app, system status,
  YouTube, web search, save note, read file. Signatures + docstrings become the
  schema (no hand-written schema).
- `memory.py` — rolling `deque` window of recent turns, rendered as a transcript.
- `main.py` — terminal entry point and `handle()` dispatch.
- `gui.py` — PyQt5 Arc Reactor widget (`QThread` worker).
- `test_mic.py` — standalone mic diagnostic: lists devices, records a WAV,
  reports RMS/peak, plays back, recognises, and checks `energy_threshold`.
- `test_routing.py`, `test_exit.py`, `test_memory.py`, `test_agent_cache.py`,
  `test_flow.py` — pytest suite (see Verification).
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
`RequestError` is printed loudly instead of being swallowed; `WaitTimeoutError` /
`UnknownValueError` / capture errors log distinct messages; debug output is gated
behind `JARVIS_DEBUG=1`.

## Verification (2026-10-18)

- `pytest`: **24 passed**.
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
amixer -c 1 sset Capture 40%                    # 100% clips; 40% keeps the floor under the 300 gate
JARVIS_MIC_INDEX=7 ./venv/bin/python main.py    # force an input device index
JARVIS_DEBUG=1 ./venv/bin/python main.py        # print capture / recognition diagnostics
```
