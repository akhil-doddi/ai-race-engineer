### app/config.py
import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = "gpt-3.5-turbo"
VOICE = 'com.apple.speech.synthesis.voice.daniel'
RATE = 175