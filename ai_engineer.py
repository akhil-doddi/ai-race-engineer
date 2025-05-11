import openai
import speech_recognition as sr
import pyttsx3
import threading
import time
import random

# 1. Set your API key
openai.api_key = "sk-proj-..."  # Replace with your actual key

# 2. Initialize text-to-speech engine
engine = pyttsx3.init()
engine.setProperty('rate', 175)
engine.setProperty('voice', 'com.apple.speech.synthesis.voice.daniel')

# 3. Initialize speech recognizer
r = sr.Recognizer()

# 4. Telemetry simulator
class Telemetry:
    def __init__(self):
        self.running = False
        self.data = {
            'speed': 0,
            'gear': 1,
            'lap': 1,
            'tire_wear': 100.0,
            'position': 10,
            'fuel': 100.0
        }

    def update(self):
        while self.running:
            self.data['speed'] = random.randint(180, 320)
            self.data['gear'] = random.randint(1, 8)
            self.data['lap'] += 1
            self.data['tire_wear'] -= random.uniform(0.5, 2.0)
            self.data['position'] = random.randint(1, 20)
            self.data['fuel'] -= random.uniform(1.0, 2.5)
            time.sleep(5)

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self.update)
        self.thread.start()

    def stop(self):
        self.running = False
        self.thread.join()

    def get_data(self):
        return self.data.copy()

# 5. Prompt generator
def generate_prompt(user_input, telemetry):
    return (
        f"You are an F1 race engineer. Use a calm, precise British tone.\n"
        f"Telemetry data: Speed: {telemetry['speed']} km/h, Gear: {telemetry['gear']}, "
        f"Lap: {telemetry['lap']}, Position: {telemetry['position']}, "
        f"Tire wear: {telemetry['tire_wear']:.1f}%, Fuel: {telemetry['fuel']:.1f}%\n\n"
        f"Driver says: \"{user_input}\"\n\n"
        f"Respond as a race engineer."
    )

# 6. Start telemetry
telemetry = Telemetry()
telemetry.start()

# 7. Main loop
try:
    while True:
        with sr.Microphone() as source:
            print("\n🎙️ Speak...")
            audio = r.listen(source)

        try:
            user_input = r.recognize_google(audio)
            print(f"You said: {user_input}")

            # 7.1 Get telemetry snapshot
            telemetry_snapshot = telemetry.get_data()
            print(f"Telemetry: {telemetry_snapshot}")

            # 7.2 Generate race engineer prompt
            prompt = generate_prompt(user_input, telemetry_snapshot)

            # 7.3 Call OpenAI
            client = openai.OpenAI()
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a calm British F1 race engineer."},
                    {"role": "user", "content": prompt}
                ]
            )

            reply = response.choices[0].message.content.strip()
            print(f"\nEngineer: {reply}")

            engine.say(reply)
            engine.runAndWait()

        except sr.UnknownValueError:
            print("Didn't catch that.")
        except Exception as e:
            print(f"Error: {e}")

except KeyboardInterrupt:
    print("\nExiting...")
    telemetry.stop()