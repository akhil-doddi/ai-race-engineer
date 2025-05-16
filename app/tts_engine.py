### app/tts_engine.py
import pyttsx3
from app.config import RATE, VOICE

engine = pyttsx3.init()
engine.setProperty('rate', RATE)
engine.setProperty('voice', VOICE)

def speak(text):
    engine.say(text)
    engine.runAndWait()