"""
src/voice/voice_input.py

Microphone input — captures driver speech and converts it to text.

WHY THIS EXISTS AS A SEPARATE MODULE:
Voice input is an I/O concern independent of strategy or AI reasoning.
In Phase 6, this module could be extended to support push-to-talk activation
(so the mic only opens when the driver presses a button) without any changes
to the rest of the system.

PROVIDER: Google Speech Recognition (via SpeechRecognition library).
Requires an internet connection. In a future offline phase, this could be
replaced with Whisper running locally.
"""

import speech_recognition as sr

# Single Recognizer instance — reused across all calls.
# Creating a new recognizer per call is unnecessary overhead.
_recognizer = sr.Recognizer()


def listen() -> str:
    """
    Open the microphone, capture a spoken utterance, and return transcribed text.

    Blocks until speech is detected and processed.
    Returns an empty string on failure so the caller can simply continue
    the loop without special error handling.

    Returns:
        Transcribed text string, or "" if recognition failed.
    """
    with sr.Microphone() as source:
        print("\n🎙️  Say again, say again..." if False else "\n🎙️  Speak...")
        audio = _recognizer.listen(source)

    try:
        return _recognizer.recognize_google(audio)
    except sr.UnknownValueError:
        # Audio was captured but speech was unclear or inaudible
        print("Say again, say again.")
        return ""
    except Exception as e:
        # Network error, microphone error, or other unexpected failure
        print(f"Voice input error: {e}")
        return ""
