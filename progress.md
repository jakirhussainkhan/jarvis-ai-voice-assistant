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
  (edge-tts + pygame, `en-US-AriaNeural`). Suppresses ALSA/JACK spam, picks the
  onboard mic (`ALC245` / `hw:1,0`), uses a **fixed** energy threshold with a
  **warm-up flush**, and bounds every call with timeouts so a dead mic can never
  stall the loop.
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
- **Microphone capture fixed** (see below): the mic's stream-open transient used
  to poison the dynamic energy threshold and make `listen()` always time out.
- Floating, transparent, always-on-top reactor widget with live state colours
  (Idle / Listening / Thinking / Speaking).
- pytest suite covering routing, `is_exit`, memory, the response cache, and the
  real router→agent dispatch.
- Published to GitHub with a README and an MIT LICENSE (`v1.0.0`).

### Microphone fix (2026-10-18)

Symptom: `main.py` printed `Listening…` forever and never recognised speech, even
when clearly spoken. Measured on the real mic (`ALC245`, device 4):

- Idle ambient is quiet — RMS ≈ 40.
- Every time the capture stream opens after a few seconds idle, the mic emits a
  loud, **clipped** transient: peak **32768** (full scale, ~2700 clipped samples),
  RMS 32768 for the first ~7 buffers (~150 ms), decaying below the gate only
  after ~0.8 s. This re-appears after TTS/agent gaps.
- With the old `energy_threshold = 300` + `dynamic_energy_threshold = True`, that
  transient tripped the gate, produced a too-short "phrase" that was discarded,
  and ratcheted the threshold from 300 up to ~14000 — after which ambient (≈40)
  never tripped it, so `listen()` raised `WaitTimeoutError` and returned `""`.

Fix (`voice.py`):

- `energy_threshold = 300`, **fixed**; `dynamic_energy_threshold = False`; no
  `adjust_for_ambient_noise()`.
- `_flush_warmup(src)` discards the stream-open transient right after the mic
  opens (until input settles, bounded at 1.5 s) — on the real mic it drops
  34–45 buffers (~0.73–0.96 s) per call.
- `RequestError` (network/STT) is now printed loudly instead of being swallowed;
  `WaitTimeoutError` / `UnknownValueError` / capture errors log distinct messages.
- Debug output is gated behind `JARVIS_DEBUG=1`.

## Verification (2026-10-18)

- `pytest`: **24 passed**.
- Real dispatch via `main.handle()`: `"what time is it"` → local, no API call.
- Real dispatch via `main.handle()` with a live Groq call: `"open YouTube and
  search for lo-fi music"` reaches the agent and triggers the `fn_youtube` tool.
- Mic fix, end-to-end through the real `listen()` path (system-output loopback):
  captured `157696` bytes, `rms=3040 peak=17347` → recognised `'open YouTube'`.
  With no speech it now correctly returns a timeout instead of grabbing garbage.
- Manual chunk scan confirms the transient: first buffers at RMS `32768 …` decaying
  to `~40`; idle ambient `~40`.
- `test_mic.py` on device 4 (ALC245): capture 286 KB, peak 32768, playback OK.
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
