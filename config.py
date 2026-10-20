"""Configuration and environment loading for Jarvis."""

import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv

# Keys that look like a key but are only placeholders from .env.example or a
# tutorial. Treating them as missing is what makes the first-run wizard appear.
PLACEHOLDER_KEYS = frozenset({
    "",
    "your_key_here",
    "your-key-here",
    "your_key",
    "your-key",
    "yourkeyhere",
    "gsk_your_key_here",
    "gsk-your-key-here",
    "your_groq_api_key",
    "api_key_here",
    "changeme",
    "change-me",
    "none",
    "null",
})


def user_env_file():
    """Return the per-user ``.env`` path used for first-run setup.

    ``JARVIS_ENV_FILE`` exists so tests (and portable installs) can point the
    wizard at a temporary file. The default lives in the user's home directory
    so a key survives replacing or updating the checkout.
    """
    override = (os.getenv("JARVIS_ENV_FILE") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".jarvis" / ".env"


_UNSET = object()


def is_api_key_configured(key=_UNSET):
    """True when ``key`` is present and is not a known placeholder.

    Called with no argument, it checks the current environment. Passing an
    explicit ``None`` (or an empty string) reports "not configured" instead of
    falling back to the environment, which keeps validation call sites clear.
    """
    if key is _UNSET:
        key = os.getenv("GROQ_API_KEY", "")
    value = (key or "").strip()
    return bool(value) and value.lower() not in PLACEHOLDER_KEYS


# Load the user-level file first, then the project's .env. python-dotenv keeps
# the first value it sees (unless override=True), so precedence is:
#   process environment  >  ~/.jarvis/.env  >  project .env
# The user file wins over the checkout, which is what makes a wizard-saved key
# persist across updates, while an existing project .env keeps working for users
# who already configured one.
USER_ENV = user_env_file()
PROJECT_ENV = Path(__file__).resolve().parent / ".env"
load_dotenv(USER_ENV)
load_dotenv(PROJECT_ENV)

__version__ = "2.2.1"

# Groq (https://console.groq.com) exposes an OpenAI-compatible Chat Completions
# API. The key is read from GROQ_API_KEY in .env; Groq keys start with "gsk_".
# Normalise both values so a blank key is treated as missing and a trailing
# slash on the base URL cannot produce "//chat/completions" paths.
API_KEY = (os.getenv("GROQ_API_KEY") or "").strip() or None
if API_KEY and not is_api_key_configured(API_KEY):
    # A placeholder must behave exactly like a missing key.
    API_KEY = None

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


# --- first-run setup / secure key storage ---------------------------------- #
def apply_api_key(key):
    """Update the in-process key and refresh clients created at import time.

    ``agent`` builds its OpenAI client once, when the module is imported. If the
    key arrives later (first-run wizard), that client is ``None`` and every turn
    would fail even though the key was saved. Rebuilding it here keeps a running
    process usable immediately, without restarting the app.
    """
    global API_KEY
    value = (key or "").strip()
    API_KEY = value or None
    if value:
        os.environ["GROQ_API_KEY"] = value
    else:
        os.environ.pop("GROQ_API_KEY", None)

    # Import lazily: agent imports config, so a top-level import would cycle.
    import sys

    module = sys.modules.get("agent")
    refresh = getattr(module, "refresh_client", None) if module else None
    if callable(refresh):
        refresh()
    return API_KEY


def save_api_key(key, path=None):
    """Persist ``key`` to a private ``.env`` file and apply it in-process.

    The file is written atomically with mode ``0600`` inside a ``0700``
    directory, so other local users cannot read it. Existing settings in the
    file are preserved; only ``GROQ_API_KEY`` is replaced.

    Returns the path written. Raises ``ValueError`` for an empty or placeholder
    key, and ``OSError`` if the file cannot be written.
    """
    value = (key or "").strip()
    if not is_api_key_configured(value):
        raise ValueError("A non-placeholder Groq API key is required.")
    if "\n" in value or "\r" in value:
        raise ValueError("The API key must be a single line.")

    target = Path(path).expanduser() if path else user_env_file()
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(target.parent, 0o700)
    except OSError:
        # A shared/odd filesystem may refuse chmod; the 0600 file below is the
        # important part. Never fail setup just because the directory mode is
        # managed elsewhere.
        pass

    lines = []
    if target.exists():
        lines = target.read_text(encoding="utf-8").splitlines()

    key_line = f"GROQ_API_KEY={value}"
    output = []
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("GROQ_API_KEY=") or stripped == "GROQ_API_KEY":
            if not replaced:
                output.append(key_line)
                replaced = True
            continue
        output.append(line)
    if not replaced:
        output.append(key_line)

    data = "\n".join(output).rstrip("\n") + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=".env.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)

    apply_api_key(value)
    return target
