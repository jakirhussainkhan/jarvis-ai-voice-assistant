"""Configuration and environment loading for Jarvis."""

import os

from dotenv import load_dotenv

load_dotenv()

__version__ = "1.0.4"

# Groq (https://console.groq.com) exposes an OpenAI-compatible Chat Completions
# API. The key is read from GROQ_API_KEY in .env; Groq keys start with "gsk_".
API_KEY = os.getenv("GROQ_API_KEY")
BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

# Model ID confirmed at https://console.groq.com/docs/models (supports tools).
MODEL_NAME = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
# Multimodal model used so Jarvis can actually *see* the screen. Verified to
# accept image_url input on this account (see progress.md).
VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.8-27b")

STT_LANG = "en-US"  # English-only by design.
TTS_VOICE = "en-US-AriaNeural"
MAX_HISTORY_MSGS = 8
# Headroom so a reasoning model's thinking tokens never crowd out the answer
# (too low a cap makes it return empty and Jarvis says "I couldn't finish that").
MAX_OUTPUT_TOKENS = 600
# How many tool round-trips one spoken request may chain (open a folder, then
# read a file, then summarise it -> 3). Plenty for real multi-step requests.
MAX_TOOL_ROUNDS = 6

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
