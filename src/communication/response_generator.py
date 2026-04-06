"""
src/communication/response_generator.py

Manages all communication with the GPT language model.

WHY THIS EXISTS:
This layer is the only part of the system that talks to OpenAI.
It owns two responsibilities:
  1. Prompt construction — translating race_state + driver input into a
     structured prompt the AI can reason about correctly.
  2. Conversation memory — maintaining session history so the engineer
     can reference earlier events naturally ("as I mentioned earlier...").

DESIGN DECISIONS:
- History is capped at 20 messages (10 exchanges) to control API cost and
  latency. Older context is dropped — recent race events matter most.
- The system message defines the engineer's personality and strict rules
  about what it does and doesn't know. This prevents hallucination.
- Raw telemetry is NEVER sent to GPT. Only the clean race_state produced
  by RaceStateManager is used to build prompts.
"""

import openai
from config.settings import OPENAI_API_KEY, OPENAI_MODEL

# Single client instance — created once at module load, reused for all calls.
# Creating a new client per request would add unnecessary overhead.
client = openai.OpenAI(api_key=OPENAI_API_KEY)

# The engineer's fixed identity and behavioural rules.
# Sent with every API call as the first message in the conversation.
SYSTEM_MESSAGE = {
    "role": "system",
    "content": (
        "You are a calm, precise British F1 race engineer on the radio. "
        "You have memory of the entire conversation this session. "

        "RESPONSE LENGTH RULES — follow these strictly: "
        "- Simple factual questions (position, gap, lap time, laps remaining, fuel, fuel burn): answer in ONE short sentence only. No extra context. "
        "- Analysis or strategy questions (should I pit, can I attack, undercut risk): use 2-3 sentences. "
        "- Never volunteer data the driver did not ask for. If they ask for position, give position only. "

        "TELEMETRY: You have full data on YOUR OWN CAR — position, lap times, tyre wear, fuel, gaps, speed, gear. "
        "Always answer questions about the driver's own car confidently using this data. "
        "IMPORTANT DISTINCTION: "
        "'Lap' or 'what lap are we on' means the current lap NUMBER (e.g. Lap 12 of 50). "
        "'Lap time' or 'last lap' means the TIME of the last completed lap (e.g. 1:32.456). "
        "Never confuse these two — answer exactly what was asked. "

        "GOOD OR BAD QUESTIONS: When the driver asks if something is 'good' or 'bad' or 'normal', "
        "answer directly in racing context. "
        "Examples: 'Is fuel burn good?' → 'Yes, 2.1 kg/lap is on target.' or 'Slightly high, push fuel save mode.' "
        "Never redirect to an unrelated topic. Answer the specific thing asked. "

        "CONSISTENCY: Once you give a pit lap estimate (e.g. 'box around Lap 24'), "
        "stick to it unless tyre wear or fuel changes significantly. "
        "If you revise the estimate, tell the driver why. "

        "PROACTIVE ALERTS: When the driver asks you to 'remind' or 'let me know' about a condition "
        "(e.g. gap < 1s, time to pit), acknowledge it and confirm the system will alert automatically. "
        "Say something like: 'Confirmed, I'll call it when the time comes.' "
        "Do NOT promise to monitor things manually — the alert system handles this. "

        "SAFETY CAR: When track_status is 'safety_car', the safety car is deployed. "
        "In this situation: tyre wear is very low, gaps are compressing, no overtaking. "
        "This is a key strategy window — consider boxing to take fresh tyres. "
        "Tell the driver clearly: 'Safety car is out. This is your pit window.' "
        "When track_status is 'green', racing is normal. "

        "NO DATA: You do not have data on rival cars' internals (their lap times, speed, telemetry), "
        "weather, or other drivers' race incidents. Say 'No data on that' and move on. "
        "You DO have gap data — gap ahead and gap behind in seconds. "
        "Use this to answer questions about how close the cars around you are. "

        "STRATEGY: Race strategy is your domain. You set the plan. "
        "Use tyre compound, wear, fuel and laps remaining to give confident guidance. "
        "Plan A = one-stop, Plan B = two-stop. "

        "RACE FINISH: If laps_remaining is 0, the race is over. "
        "Do NOT discuss strategy, pit stops, or tyre wear. "
        "Instead: confirm the finishing position (e.g. 'P7, race complete. Well done.') "
        "and answer any post-race questions naturally. "
        "If the driver asks about pit stops or strategy after the race, remind them the race is finished."
    ),
}


def build_user_message(driver_input: str, race_state: dict) -> dict:
    """
    Construct a structured user message combining driver input with race context.

    The telemetry header gives GPT full situational awareness before it reads
    the driver's question. This means every response is grounded in current
    race data, not GPT's general racing knowledge.

    Args:
        driver_input: Transcribed speech or typed text from the driver.
        race_state:   Clean race_state dict from RaceStateManager.

    Returns:
        OpenAI-format message dict with role='user'.
    """
    s = race_state
    drs_status = "ON" if s.get("drs") else "OFF"
    track_status = s.get("track_status", "green").upper()

    content = (
        f"[Telemetry — Lap {s['lap']}/{s['total_laps']} | {s['laps_remaining']} laps remaining | Track: {track_status}]\n"
        f"Position: P{s['position']} | "
        f"Gap ahead: {s['gap_ahead']}s | Gap behind: {s['gap_behind']}s\n"
        f"Last lap: {s['last_lap_time']} (delta: {s['lap_delta']} vs best {s['best_lap_time']})\n"
        f"Tyres: {s['tire_compound']} | Life remaining: {s['tire_wear']:.1f}% | Age: {s['tire_age_laps']} laps\n"
        f"Fuel: {s['fuel']:.1f}kg | Burn rate: {s['fuel_per_lap']}kg/lap\n"
        f"Speed: {s['speed']} km/h | Gear: {s['gear']} | DRS: {drs_status}\n\n"
        f"Driver: {driver_input}"
    )
    return {"role": "user", "content": content}


def ask_engineer(driver_input: str, race_state: dict, history: list) -> tuple[str, list]:
    """
    Send driver input + race context to GPT and return the engineer's reply.

    Maintains rolling conversation history for memory across the session.
    History is capped at 20 messages to control latency and API cost.

    Args:
        driver_input: What the driver said or typed.
        race_state:   Current clean race_state snapshot.
        history:      Running list of past message dicts (grows each call).

    Returns:
        Tuple of (reply_text, updated_history).
        Caller must store the returned history and pass it back next call.
    """
    # Build and append the new driver message to history
    user_message = build_user_message(driver_input, race_state)
    history.append(user_message)

    # Sliding window — keep only the most recent 20 messages (10 exchanges)
    # This prevents the context window growing unbounded across a full race
    recent_history = history[-20:]

    # Call GPT: system identity first, then conversation history
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[SYSTEM_MESSAGE] + recent_history,
    )

    reply = response.choices[0].message.content.strip()

    # Store engineer reply in history so next call has full context
    history.append({"role": "assistant", "content": reply})

    return reply, history
