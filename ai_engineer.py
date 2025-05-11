## Project: AI Race Engineer (Voice Assistant for F1-style Telemetry Feedback)

### File: ai_engineer.py

import os
import openai
import speech_recognition as sr
import pyttsx3 
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

# Initialize TTS engine
engine = pyttsx3.init()
engine.setProperty('rate', 175)

# Function to get voice input
def listen_to_voice():
    recognizer = sr.Recognizer()
    with sr.Microphone() as source:
        print("Ready. Say something...")
        audio = recognizer.listen(source)
    try:
        command = recognizer.recognize_google(audio)
        print(f"You said: {command}")
        return command
    except sr.UnknownValueError:
        print("Could not understand audio")
        return ""
    except sr.RequestError as e:
        print(f"Speech recognition error: {e}")
        return ""

# Function to call GPT-3.5

def get_ai_response(prompt):
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a Formula 1 race engineer. Respond with short, focused radio updates using race telemetry."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=200,
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"[Error] {str(e)}"

# Main interaction loop
while True:
    user_input = listen_to_voice()
    if user_input.lower() in ["exit", "quit"]:
        break

    ai_reply = get_ai_response(user_input)
    print(f"Engineer: {ai_reply}")
    engine.say(ai_reply)
    engine.runAndWait()

