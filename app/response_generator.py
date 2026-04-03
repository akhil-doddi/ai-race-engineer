import openai
from app.config import OPENAI_API_KEY, OPENAI_MODEL

client = openai.OpenAI(api_key=OPENAI_API_KEY)

# This is the engineer's personality — sent with every request
SYSTEM_MESSAGE = {
    "role": "system",
    "content": (
        "You are a calm, precise British F1 race engineer on the radio. "
        "You have memory of the entire conversation this session. "

        "RESPONSE LENGTH RULES — follow these strictly: "
        "- Simple factual questions (position, gap, lap time, laps remaining, fuel): answer in ONE short sentence only. No extra context. "
        "- Analysis or strategy questions (should I pit, can I attack, undercut risk): use 2-3 sentences. "
        "- Never volunteer data the driver did not ask for. If they ask for position, give position only. "

        "TELEMETRY: You have full data on YOUR OWN CAR — position, lap times, tyre wear, fuel, gaps, speed, gear. "
        "Always answer questions about the driver's own car confidently using this data. "
        "IMPORTANT DISTINCTION: "
        "'Lap' or 'what lap are we on' means the current lap NUMBER (e.g. Lap 12 of 50). "
        "'Lap time' or 'last lap' means the TIME of the last completed lap (e.g. 1:32.456). "
        "Never confuse these two — answer exactly what was asked. "

        "NO DATA: You do not have data on rival cars' internals (their lap times, speed, telemetry), "
        "safety car status, weather, or other drivers' race incidents. Say 'No data on that' and move on. "
        "You DO have gap data — gap ahead and gap behind in seconds. "
        "Use this to answer questions about how close the cars around you are. "

        "STRATEGY: Race strategy is your domain. You set the plan. "
        "Use tyre compound, wear, fuel and laps remaining to give confident guidance. "
        "Plan A = one-stop, Plan B = two-stop."
    )
}

def generate_user_message(user_input, telemetry):
    """Formats the driver's question with the latest telemetry snapshot."""
    t = telemetry
    drs = "ON" if t.get('drs') else "OFF"
    content = (
        f"[Telemetry — Lap {t['lap']}/{t['total_laps']} | {t['laps_remaining']} laps remaining]\n"
        f"Position: P{t['position']} | "
        f"Gap ahead: {t['gap_ahead']}s | Gap behind: {t['gap_behind']}s\n"
        f"Last lap: {t['last_lap_time']} (delta: {t['lap_delta']} vs best {t['best_lap_time']})\n"
        f"Tyres: {t['tire_compound']} | Life remaining: {t['tire_wear']:.1f}% | Age: {t['tire_age_laps']} laps\n"
        f"Fuel: {t['fuel']:.1f}kg | Burn rate: {t['fuel_per_lap']}kg/lap\n"
        f"Speed: {t['speed']} km/h | Gear: {t['gear']} | DRS: {drs}\n\n"
        f"Driver: {user_input}"
    )
    return {"role": "user", "content": content}

def ask_gpt(user_input, telemetry, history):
    """
    Sends the full conversation history to GPT and returns the reply
    plus the updated history.

    history is a list of {"role": ..., "content": ...} dicts.
    """
    # Build the new user message with fresh telemetry
    user_message = generate_user_message(user_input, telemetry)

    # Add to history
    history.append(user_message)

    # Cap history to last 20 messages (10 exchanges) to avoid runaway costs
    recent_history = history[-20:]

    # Send system message + recent history to GPT
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[SYSTEM_MESSAGE] + recent_history
    )

    reply = response.choices[0].message.content.strip()

    # Save the engineer's reply into history too
    history.append({"role": "assistant", "content": reply})

    return reply, history
