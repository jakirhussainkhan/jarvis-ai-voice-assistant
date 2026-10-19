"""Configuration and environment loading for Jarvis."""

import os

from dotenv import load_dotenv

load_dotenv()

__version__ = "2.1.1"

# Groq (https://console.groq.com) exposes an OpenAI-compatible Chat Completions
# API. The key is read from GROQ_API_KEY in .env; Groq keys start with "gsk_".
# Normalise both values so a blank key is treated as missing and a trailing
# slash on the base URL cannot produce "//chat/completions" paths.
API_KEY = (os.getenv("GROQ_API_KEY") or "").strip() or None
BASE_URL = ((os.getenv("GROQ_BASE_URL") or "https://api.groq.com/openai/v1")
            .strip().rstrip("/"))

# Primary text/agent model -- the 120B open-weight model on the Groq free tier.
# Model IDs confirmed at https://console.groq.com/docs/models (both support tools).
MODEL_NAME = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
# Multimodal model used so Jarvis can actually *see* the screen. Verified live
# against this account: Qwen accepts OpenAI-style image_url content, while
# `llama-3.2-11b-vision-preview` is decommissioned on Groq. Override it in .env
# (GROQ_VISION_MODEL=...) if the account exposes a different multimodal model.
VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.8-27b")
# Cheaper model used only when the primary one is rate-limited (HTTP 429). The
# 120B model has a strict requests-per-minute cap on the free tier; falling back
# to a small 8B model keeps the turn from failing outright.
FALLBACK_MODEL = os.getenv("GROQ_FALLBACK_MODEL", "llama3-8b-8192")

# --- optional local Ollama fallback ---------------------------------------- #
# Off by default: Jarvis keeps using Groq and never contacts Ollama unless this
# is explicitly enabled (USE_LOCAL_LLM=1 or `main.py --local`), or the Groq
# circuit breaker opens. Ollama is OpenAI-compatible, so no extra SDK is needed.
USE_LOCAL_LLM = os.getenv("USE_LOCAL_LLM", "").strip().lower() in (
    "1", "true", "yes", "on",
)
OLLAMA_BASE_URL = (
    (os.getenv("OLLAMA_BASE_URL") or "").strip().rstrip("/")
    or "http://localhost:11434/v1"
)
OLLAMA_MODEL = (os.getenv("OLLAMA_MODEL") or "llama3").strip() or "llama3"

STT_LANG = "en-US"  # English-only by design.
# The Iron-Man Jarvis voice: a British male neural voice ("Ryan"). Override it
# (or go back to a female voice) from .env, e.g. JARVIS_TTS_VOICE=en-US-AriaNeural.
TTS_VOICE = os.getenv("JARVIS_TTS_VOICE", "en-GB-RyanNeural")


def _env_int(name, default, minimum=None):
    """Read an int env var, falling back to ``default`` when it is unusable."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if minimum is not None and value < minimum:
        return default
    return value


def _env_float(name, default, minimum=None):
    """Read a float env var, falling back to ``default`` when it is unusable."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if minimum is not None and value < minimum:
        return default
    return value


# Continuous conversation (ChatGPT-like voice mode). After Jarvis replies it
# keeps the microphone open so the next sentence flows without another click.
# It sleeps when you go quiet, and leaves when you say an exit word, press Stop
# or mute the mic. JARVIS_CHAT=0 gets the old click-to-talk behaviour back.
CHAT_ON = os.getenv("JARVIS_CHAT", "1").strip().lower() not in ("0", "false", "no", "off")
CHAT_TIMEOUT = _env_int("JARVIS_CHAT_TIMEOUT", 60, minimum=10)

# Barge-in sensitivity: speaking over Jarvis for this long cuts the reply.
# 1200 is normal conversational volume (the old 2600 was too high for many
# microphones), and 0.3 s keeps the interruption feeling immediate. Raise the
# threshold if speaker echo causes false interruptions.
BARGE_THRESHOLD = _env_int("JARVIS_BARGE_THRESHOLD", 1200, minimum=300)
BARGE_HOLD = _env_float("JARVIS_BARGE_HOLD", 0.3, minimum=0.1)
# How many recent turns the transcript handed to the agent may contain. Kept
# small on purpose: the Groq free tier is token-limited, and older turns add
# cost without adding much context.
MAX_HISTORY_MSGS = _env_int("JARVIS_HISTORY_MSGS", 10, minimum=2)
# Send only the tools relevant to the detected intent instead of all ~40 schemas
# on every call. This is the single biggest token saver. Set JARVIS_TOOL_PRUNING=0
# to fall back to the full tool list if the intent classifier ever misjudges.
TOOL_PRUNING = os.getenv("JARVIS_TOOL_PRUNING", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
# Headroom so a reasoning model's thinking tokens never crowd out the answer
# (too low a cap makes it return empty and Jarvis says "I couldn't finish that").
MAX_OUTPUT_TOKENS = 600
# How many tool round-trips one spoken request may chain (open a folder, then
# read a file, then summarise it -> 3). Plenty for real multi-step requests.
MAX_TOOL_ROUNDS = 6

# --- remote API resilience --------------------------------------------------#
# A hung request must not block a spoken turn for minutes (the OpenAI SDK's
# default). One timeout counts as one failure for the circuit breaker below.
API_TIMEOUT = _env_int("JARVIS_API_TIMEOUT", 20, minimum=1)
# After this many consecutive transient failures (5xx, timeout, connection),
# stop calling the API for the cooldown instead of making every turn wait.
API_FAILURE_THRESHOLD = _env_int("JARVIS_API_FAILURES", 3, minimum=1)
# How long the circuit stays open before allowing one half-open probe request.
API_RECOVERY_SECONDS = _env_int("JARVIS_API_RECOVERY", 30, minimum=1)

# --- screen vision --------------------------------------------------------- #
# Downscale screenshots before sending them to the vision model (tokens/bandwidth).
SCREEN_MAX_WIDTH = int(os.getenv("JARVIS_SCREEN_MAX_WIDTH", "1280"))
# Max perceive->act steps for one screen-automation goal ("click the login button").
SCREEN_STEPS = int(os.getenv("JARVIS_SCREEN_STEPS", "6"))

# --- terminal -------------------------------------------------------------- #
# A shell command may not run longer than this (seconds) before it is killed.
SHELL_TIMEOUT = int(os.getenv("JARVIS_SHELL_TIMEOUT", "20"))
# Output returned to the model is capped to this many characters.
SHELL_MAX_OUTPUT = int(os.getenv("JARVIS_SHELL_MAX_OUTPUT", "4000"))
