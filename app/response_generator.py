### app/response_generator.py
import openai
from app.config import OPENAI_API_KEY, OPENAI_MODEL

openai.api_key = OPENAI_API_KEY

def generate_prompt(user_input, telemetry):
    return (
        f"You are an F1 race engineer. Use a calm, precise British tone.\n"
        f"Telemetry data: Speed: {telemetry['speed']} km/h, Gear: {telemetry['gear']}, "
        f"Lap: {telemetry['lap']}, Position: {telemetry['position']}, "
        f"Tire wear: {telemetry['tire_wear']:.1f}%, Fuel: {telemetry['fuel']:.1f}%\n\n"
        f"Driver says: \"{user_input}\"\n\n"
        f"Respond as a race engineer."
    )

def ask_gpt(prompt):
    response = openai.ChatCompletion.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": "You are a calm British F1 race engineer."},
            {"role": "user", "content": prompt}
        ]
    )
    return response.choices[0].message.content.strip()