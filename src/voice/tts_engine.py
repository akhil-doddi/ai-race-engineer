"""
src/voice/tts_engine.py

Text-to-speech engine — converts engineer responses to spoken audio.

WHY THIS EXISTS AS A SEPARATE MODULE:
Voice output is an I/O concern, entirely separate from AI reasoning.
Isolating it here means the TTS provider can be swapped (e.g. from pyttsx3
to ElevenLabs or Azure TTS in Phase 6) without touching any other layer.

SPEECH CLEANING:
The AI returns text formatted for reading, not speaking. Abbreviations like
'P14' are read as 'page 14' by TTS engines. The clean_for_speech() function
normalises these before audio playback.
"""

import re
import pyttsx3
from config.settings import RATE, VOICE

# Initialise the TTS engine once at module load.
# Repeated calls to pyttsx3.init() can cause instability on macOS.
_engine = pyttsx3.init()
_engine.setProperty("rate", RATE)
_engine.setProperty("voice", VOICE)


def clean_for_speech(text: str) -> str:
    """
    Normalise text so the TTS engine pronounces it correctly.

    Transformations applied:
    - P14 → Position 14  (prevents 'page fourteen')
    - DRS → D R S        (spoken as individual letters, not as a word)
    """
    # Match 'P' followed by 1+ digits as a whole word (not inside longer words)
    text = re.sub(r"\bP(\d+)\b", r"Position \1", text)
    text = text.replace("DRS", "D R S")
    return text


def speak(text: str) -> None:
    """
    Speak the given text aloud using the configured voice.

    Blocks until the engineer finishes speaking — this is intentional.
    The app should not show the next input prompt while the engineer is
    still mid-sentence, as this would break the radio conversation feel.

    Args:
        text: The engineer's response text to be spoken.
    """
    _engine.say(clean_for_speech(text))
    _engine.runAndWait()
