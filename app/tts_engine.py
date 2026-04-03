### app/tts_engine.py
import re
import pyttsx3
from app.config import RATE, VOICE

engine = pyttsx3.init()
engine.setProperty('rate', RATE)
engine.setProperty('voice', VOICE)

def clean_for_speech(text):
    """Fix abbreviations that TTS mispronounces."""
    # P14 → Position 14
    text = re.sub(r'\bP(\d+)\b', r'Position \1', text)
    # DRS → D R S (spoken as letters)
    text = text.replace('DRS', 'D R S')
    return text

def speak(text):
    engine.say(clean_for_speech(text))
    engine.runAndWait()