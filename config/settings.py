"""
config/settings.py

Central configuration for the AI Race Engineer system.

All environment variables and tunable constants live here.
No other module should read from .env directly — always import from this file.
This makes it easy to swap configs (e.g. local vs Docker vs CI) without
touching source code.
"""

import os
from dotenv import load_dotenv

# Load .env file, overriding any shell-level environment variables.
# override=True ensures the .env value always wins, preventing stale
# keys set in ~/.zshrc from silently breaking authentication.
load_dotenv(override=True)

# --- OpenAI ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = "gpt-4o-mini"   # Cost-efficient, low-latency model suitable for real-time use

# --- Voice Output ---
# Voice ID for macOS pyttsx3. Change this to switch the engineer's voice.
# Available British male voices: com.apple.voice.compact.en-GB.Daniel
#                                 com.apple.eloquence.en-GB.Eddy
#                                 com.apple.eloquence.en-GB.Reed
VOICE = "com.apple.eloquence.en-GB.Eddy"
RATE = 175   # Words per minute. 175 is natural conversation pace.

# --- Telemetry (Phase 1: Simulator) ---
# These will be replaced by UDP network config in Phase 2.
BASE_LAP_TIME = 92.0    # Baseline lap time in seconds (92.0 = 1:32.000)
TOTAL_LAPS = 50         # Total race distance in laps

# --- UDP Telemetry (Phase 2: PS5 Live) ---
# Uncomment and configure when connecting to PS5.
# UDP_HOST = "0.0.0.0"      # Listen on all network interfaces
# UDP_PORT = 20777           # Default F1 game telemetry port
