"""
src/main.py

Entry point and runtime orchestrator for the AI Race Engineer system.

WHAT THIS FILE DOES:
Coordinates all system layers in the correct order each loop iteration:
  1. Telemetry   → get latest race data
  2. Race State  → convert to clean structured object
  3. Events      → check for proactive alerts (pit window, undercut, etc.)
  4. Driver Input → voice or text from the driver
  5. AI          → generate engineer response
  6. Voice Output → speak the response

WHAT THIS FILE DOES NOT DO:
It contains no business logic. It does not calculate strategy, build prompts,
or process audio. Each of those responsibilities lives in its own module.
This is the conductor — it does not play the instruments.

HOW TO RUN:
    python3 -m src.main
"""

from src.telemetry.simulator import TelemetrySimulator
from src.race_state.state_manager import build_race_state
from src.events.event_detector import get_event, format_alert
from src.communication.response_generator import ask_engineer
from src.voice.tts_engine import speak
from src.voice.voice_input import listen


def get_driver_input(mode: str) -> str:
    """Route input to text or voice depending on selected mode."""
    if mode == "text":
        return input("⌨️  You: ").strip()
    return listen()


def main():
    print("🏎️  AI Race Engineer — starting up...")
    print("─" * 42)

    # Mode selection
    raw_mode = input("Input mode — 'v' for voice, 't' for text: ").strip().lower()
    mode = "voice" if raw_mode == "v" else "text"
    print(f"{'🎙️  Voice' if mode == 'voice' else '⌨️  Text'} mode selected.")
    print("─" * 42)

    # Initialise telemetry source (Phase 1: simulator)
    # Phase 2: swap TelemetrySimulator for UDPTelemetryListener here
    telemetry = TelemetrySimulator()
    telemetry.start()
    print("📡 Telemetry running. Ready for driver input.")
    print("   Press Ctrl+C to end session.\n")

    # Session state
    history: list = []          # Conversation memory — grows each exchange
    last_urgency: str = "green" # Track alert level to prevent repeated alerts

    try:
        while True:
            # --- Layer 1 & 2: Telemetry → Race State ---
            raw = telemetry.get_snapshot()
            race_state = build_race_state(raw)

            # --- Layer 3: Event Detection (Proactive Engineer) ---
            # Check for strategy alerts before waiting for driver input.
            # This ensures the engineer can interrupt with critical calls.
            event = get_event(race_state)
            if event["urgency"] != last_urgency:
                alert_text = format_alert(event)
                if alert_text:
                    print(alert_text)
                    # Clean reason string for natural speech (replace ; and — )
                    spoken = event["reason"].replace(";", ",").replace("  ", " ")
                    if event["should_pit"]:
                        speak(f"Box box box. {spoken}")
                    else:
                        speak(f"Strategy alert. {spoken}")
                last_urgency = event["urgency"]

            # --- Layer 4: Driver Input ---
            driver_input = get_driver_input(mode)
            if not driver_input:
                # Empty input (mishear or blank line) — loop back silently
                continue

            if mode == "voice":
                print(f"🎙️  Driver: {driver_input}")

            # --- Refresh race state immediately before AI call ---
            # A few seconds may have passed during input — get freshest data
            race_state = build_race_state(telemetry.get_snapshot())

            # --- Layer 5: AI Response ---
            print("⏳ Thinking...")
            reply, history = ask_engineer(driver_input, race_state, history)

            # --- Layer 6: Voice Output ---
            print(f"📻 Engineer: {reply}\n")
            speak(reply)

    except KeyboardInterrupt:
        print("\n🛑 Session ended. Shutting down race engineer...")
        telemetry.stop()


if __name__ == "__main__":
    main()
