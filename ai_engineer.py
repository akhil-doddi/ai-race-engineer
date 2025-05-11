## Project: AI Race Engineer (Voice Assistant for F1-style Telemetry Feedback)

### File: ai_engineer.py

import openai
import speech_recognition as sr
import pyttsx3
import json

# Load telemetry data
with open('telemetry.json', 'r') as f:
    telemetry_feed = json.load(f)

telemetry_index = 0

# 1. Set your API key
openai.api_key = "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"  # Keep this secret in actual use

# 2. Initialize voice engine
engine = pyttsx3.init()
engine.setProperty('rate', 175)
engine.setProperty('voice', 'com.apple.speech.synthesis.voice.daniel')

# 3. Initialize recognizer
r = sr.Recognizer()

# 4. Simulated telemetry feed
telemetry_feed = [
    {"lap": 15, "position": "P4", "gap_ahead": "2.3s", "gap_behind": "1.1s", "tire_wear": "61%", "lap_time": "1:32.4", "fuel_left": "5 laps"},
    {"lap": 16, "position": "P4", "gap_ahead": "2.1s", "gap_behind": "1.4s", "tire_wear": "63%", "lap_time": "1:32.1", "fuel_left": "4 laps"},
    {"lap": 17, "position": "P3", "gap_ahead": "1.8s", "gap_behind": "0.9s", "tire_wear": "65%", "lap_time": "1:31.9", "fuel_left": "3 laps"},
    {"lap": 18, "position": "P3", "gap_ahead": "1.5s", "gap_behind": "1.2s", "tire_wear": "68%", "lap_time": "1:31.7", "fuel_left": "2 laps"},
]
telemetry_index = 0

# 5. Main loop
while True:
    with sr.Microphone() as source:
        print("\n🎙️ Speak...")
        audio = r.listen(source)

    try:
        user_input = r.recognize_google(audio)
        print(f"You said: {user_input}")

        # Rotate through telemetry
        telemetry = telemetry_feed[telemetry_index % len(telemetry_feed)]
        telemetry_index += 1

        # Build telemetry string
        telemetry_prompt = (
            f"Lap {telemetry['lap']}. You're in {telemetry['position']}, "
            f"{telemetry['gap_ahead']} to car ahead, {telemetry['gap_behind']} behind. "
            f"Tire wear at {telemetry['tire_wear']}, last lap was {telemetry['lap_time']}, "
            f"fuel left for {telemetry['fuel_left']}."
        )

        # OpenAI chat call with telemetry-enhanced system prompt
        client = openai.OpenAI()
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": f"You are an F1 race engineer. Be calm, British, and precise. Use this telemetry: {telemetry_prompt}"
                },
                {"role": "user", "content": user_input}
            ]
        )

        reply = response.choices[0].message.content
        print(f"Engineer: {reply}")

        engine.say(reply)
        engine.runAndWait()

    except sr.UnknownValueError:
        print("Didn't catch that.")
    except Exception as e:
        print(f"Error: {e}")
        