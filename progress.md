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

- `config.py` — loads `.env`, holds `API_KEY` (from `GROQ_API_KEY`), `BASE_URL`,
  `MODEL_NAME`, `STT_LANG`, `TTS_VOICE`, and the history/output/tool limits.
- `voice.py` — `listen()` (SpeechRecognition + Google STT, `en-US`) and `speak()`
  (edge-tts + pygame, `en-US-AriaNeural`). Suppresses ALSA/JACK spam, picks the
  onboard mic (`ALC245` / `hw:1,0`), and bounds every call with timeouts so a
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
- `test_mic.py` — standalone mic diagnostic: lists devices, records a WAV,
  reports RMS/peak, plays back, recognises, and checks `energy_threshold`.
- `test_routing.py`, `test_exit.py`, `test_memory.py`, `test_agent_cache.py`,
  `test_flow.py` — pytest suite (see Verification).

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
- Hardened audio capture: device preference, no `adjust_for_ambient_noise()`
  (it pushed the threshold to ~30000 on this machine), bounded timeouts, and
  error suppression for ALSA/JACK.
- Floating, transparent, always-on-top reactor widget with live state colours
  (Idle / Listening / Thinking / Speaking).
- pytest suite covering routing, `is_exit`, memory, the response cache, and the
  real router→agent dispatch.

## Verification (2026-10-18)

- `pytest`: **24 passed**.
- Real dispatch via `main.handle()`: `"what time is it"` → local, no API call.
- Real dispatch via `main.handle()` with a live Groq call: `"open YouTube and
  search for lo-fi music"` reaches the agent and triggers the `fn_youtube` tool.
- `test_mic.py` on device 4 (ALC245): capture 286 KB, RMS 8830, peak 32768,
  playback OK (recognition returns "no words" until someone speaks).
- Project source has no `gemini` / `GEMINI_API_KEY` / `google.genai` references;
  the unused `google-genai` SDK was uninstalled from `venv/`.

## Known issues & things to verify

- `fn_read_file` reads any user-supplied path and `fn_save_note` writes to
  `~/jarvis_notes.md` — no sandboxing; review before exposing to untrusted input.
- The response cache is in-memory only and keyed by lowercased query, so it is
  lost on restart and ignores conversation context.
- The agent path needs network access; the router fast-path works offline.

## Next steps

1. Consider persisting notes/history and adding a wake word.
2. Decide on sandboxing for file/shell tools.

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
valid Groq `GROQ_API_KEY`. `.env`, `venv/`, and `__pycache__/` are gitignored.
