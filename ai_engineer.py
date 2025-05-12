import openai
import speech_recognition as sr
import pyttsx3

# 1. Set your API key
openai.api_key = "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"  # Keep this secret in actual use

# 2. Initialize voice engine
engine = pyttsx3.init()
engine.setProperty('rate', 175)
engine.setProperty('voice', 'com.apple.speech.synthesis.voice.daniel')

# 3. Initialize recognizer
r = sr.Recognizer()

# 4. Simulated race data
race_data = {
    'last_lap_time': '1:32.4',
    'car_ahead_gap': 2.3,
    'tire_wear': 61,
    'position': 4,
    'strategy': 'Hold position and box in 5 laps'
}

# 5. Prompt generator
def generate_race_prompt(user_input, race_data):
    if "lap" in user_input.lower():
        return f"Driver asked about lap times. Last lap was {race_data['last_lap_time']}. Car ahead is {race_data['car_ahead_gap']}s ahead."

    if "tire" in user_input.lower():
        return f"Driver asked about tire wear. Current tire wear is {race_data['tire_wear']}%. Strategy is: {race_data['strategy']}."

    if "position" in user_input.lower():
        return f"Driver asked about position. Currently P{race_data['position']}, {race_data['car_ahead_gap']}s behind the car ahead."

    if "strategy" in user_input.lower():
        return f"Driver asked for race strategy. Strategy: {race_data['strategy']}."

    return f"Driver query: {user_input}. Use current data: {race_data} to respond calmly and briefly."


# 6. Main loop
while True:
    with sr.Microphone() as source:
        print("\n🎙️ Speak...")
        audio = r.listen(source)

    try:
        user_input = r.recognize_google(audio)
        print(f"You said: {user_input}")

        # Build dynamic prompt
        race_prompt = generate_race_prompt(user_input, race_data)

        # Chat API call
        client = openai.OpenAI()
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are an F1 race engineer. Be calm, British, and precise."},
                {"role": "user", "content": race_prompt}
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