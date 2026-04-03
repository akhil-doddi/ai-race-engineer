### app/config.py
import os
from dotenv import load_dotenv

load_dotenv(override=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = "gpt-4o-mini"
VOICE = 'com.apple.eloquence.en-GB.Eddy'
RATE = 175
