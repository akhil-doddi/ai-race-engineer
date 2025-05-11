import time
import random
import openai
import speech_recognition as sr
import pyttsx3

# Initialize OpenAI API
openai.api_key = "your-api-key"

# Telemetry class
class Telemetry:
    def __init__(self):
        self.telemetry_data = {
            "speed": 0,
            "gear": 1,
            "lap": 0,
            "tire_wear": 100.0,
            "position": 1,
            "fuel": 100.0
        }

    def start(self):
        self.running = True
        self.update_telemetry()

    def stop(self):
        self.running = False

    def update_telemetry(self):
        while self.running:
            # Simulate updating telemetry
            self.telemetry_data["speed"] = random.randint(180, 320)
            self.telemetry_data["gear"] = random.randint(1, 8)
            self.telemetry_data["lap"] = random.randint(1, 50)
            self.telemetry_data["tire_wear"] = round(random.uniform(60, 100), 2)
            self.telemetry_data["position"] = random.randint(1, 20)
            self.telemetry_data["fuel"] = round(random.uniform(50, 100), 2)
            time.sleep(1)  # Update every second

    def get_data(self):
        return self.telemetry_data

# Function to display telemetry
def display_telemetry(telemetry_data):
    print("\n--- Telemetry Update ---")
    print(f"Current Lap: {telemetry_data['lap']}")
    print(f"Speed: {telemetry_data['speed']} km/h")
    print(f"Gear: {telemetry_data['gear']}")
    print(f"Tire Wear: {telemetry_data['tire_wear']}%")
    print(f"Fuel: {telemetry_data['fuel']}%")
    print(f"Position: {telemetry_data['position']}")
    print("------------------------")

# Initialize voice engine
engine = pyttsx3.init()
engine.setProperty('rate', 175)  # Speed
engine.setProperty('voice', 'com.apple.speech.synthesis.voice.daniel')  # British voice

# Initialize recognizer
r = sr.Recognizer()

# Main loop
if __name__ == "__main__":
    telemetry = Telemetry()
    telemetry.start()

    try:
        while True:
            telemetry_data = telemetry.get_data()  # Get latest telemetry data
            display_telemetry(telemetry_data)  # Display telemetry in readable format

            # Voice recognition and OpenAI API calls (for engineer response)
            with sr.Microphone() as source:
                print("\n🎙️ Speak...")
                audio = r.listen(source)

            try:
                user_input = r.recognize_google(audio)
                print(f"You said: {user_input}")

                # Create chat with OpenAI for race engineer response
                response = openai.Completion.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": "You are an F1 race engineer. Be calm, British, and precise."},
                        {"role": "user", "content": user_input}
                    ]
                )

                reply = response['choices'][0]['message']['content']
                print(f"Engineer: {reply}")

                # Speak the reply
                engine.say(reply)
                engine.runAndWait()

            except sr.UnknownValueError:
                print("Didn't catch that.")
            except Exception as e:
                print(f"Error: {e}")
            
            time.sleep(1)  # Add delay between loops to avoid overload

    except KeyboardInterrupt:
        telemetry.stop()
        print("Telemetry stopped.")