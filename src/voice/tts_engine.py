"""
src/voice/tts_engine.py

Text-to-speech engine — converts engineer responses to spoken audio.

WHY THIS EXISTS AS A SEPARATE MODULE:
Voice output is an I/O concern, entirely separate from AI reasoning.
Isolating it here means the TTS provider can be swapped (e.g. to ElevenLabs
or Azure TTS in a future phase) without touching any other layer.

MACOS IMPLEMENTATION:
pyttsx3 has a known bug on macOS where runAndWait() raises
"run loop already started" even from a dedicated thread, due to conflicts
with the macOS Core Audio event loop. We bypass it entirely and use
macOS's built-in `say` command via subprocess instead.

`say` runs as a fully separate process — it is thread-safe by definition,
supports the same macOS voices (including Eddy, Daniel, etc.),
and blocks until speech completes. No third-party library required.

SPEECH CLEANING:
The AI returns text formatted for reading, not speaking. Abbreviations like
'P14' are read as 'page 14' by TTS engines. clean_for_speech() normalises
these before audio playback.
"""

import re
import subprocess
import sys
from config.settings import RATE, VOICE


def _extract_voice_name(voice_id: str) -> str:
    """
    Convert a pyttsx3-style voice ID to the short name used by `say -v`.

    pyttsx3 format : 'com.apple.eloquence.en-GB.Eddy'
    say format     : 'Eddy'

    Falls back to 'Daniel' (a reliable British male voice) if parsing fails.
    """
    # The voice name is always the last segment after the final dot
    parts = voice_id.rsplit(".", maxsplit=1)
    if len(parts) == 2 and parts[1]:
        return parts[1]
    return "Daniel"


# Derive the short voice name once at module load
_VOICE_NAME = _extract_voice_name(VOICE)


def clean_for_speech(text: str) -> str:
    """
    Normalise text so the TTS engine pronounces it correctly.

    Transformations applied:
    - P14 → Position 14  (prevents 'page fourteen')
    - DRS → D R S        (spoken as individual letters, not as a word)
    """
    text = re.sub(r"\bP(\d+)\b", r"Position \1", text)
    text = text.replace("DRS", "D R S")
    return text


def speak(text: str) -> None:
    """
    Speak the given text aloud using the macOS `say` command.

    Blocks until speech finishes. This is intentional — the next input
    prompt should not appear while the engineer is still mid-sentence.

    `say` is a subprocess call and is fully thread-safe. It can be called
    from the ProactiveMonitor thread or the main thread without conflict.

    Args:
        text: The engineer's response text to be spoken.
    """
    cleaned = clean_for_speech(text)

    if sys.platform == "darwin":
        # macOS: use the built-in `say` command
        # -v : voice name   (e.g. "Eddy", "Daniel")
        # -r : words per minute
        subprocess.run(
            ["say", "-v", _VOICE_NAME, "-r", str(RATE), cleaned],
            check=False,   # don't raise on non-zero exit; silence is better than crash
        )
    else:
        # Non-macOS fallback: use pyttsx3
        # This path is only reached on Linux/Windows.
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.setProperty("rate", RATE)
            engine.say(cleaned)
            engine.runAndWait()
        except Exception:
            # If TTS fails on non-macOS, print to terminal and continue.
            print(f"[TTS unavailable] {cleaned}")
