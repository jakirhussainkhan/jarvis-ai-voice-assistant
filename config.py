"""Configuration and environment loading for Jarvis."""

import os

from dotenv import load_dotenv

load_dotenv()

# Groq (https://console.groq.com) exposes an OpenAI-compatible Chat Completions
# API. The key is read from GROQ_API_KEY in .env; Groq keys start with "gsk_".
API_KEY = os.getenv("GROQ_API_KEY")
BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

# Model ID confirmed at https://console.groq.com/docs/models (supports tools).
MODEL_NAME = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
STT_LANG = "en-US"  # English-only by design.
TTS_VOICE = "en-US-AriaNeural"
MAX_HISTORY_MSGS = 6
MAX_OUTPUT_TOKENS = 220
MAX_TOOL_ROUNDS = 3
