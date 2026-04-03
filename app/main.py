from app.telemetry import Telemetry
from app.voice_input import listen_to_driver
from app.response_generator import ask_gpt
from app.tts_engine import speak
from app.pit_strategy import get_pit_recommendation, format_pit_alert

def get_input(mode):
    if mode == "text":
        return input("⌨️  You: ").strip()
    else:
        return listen_to_driver()

def main():
    print("🏎️  AI Race Engineer starting up...")
    print("─" * 40)

    mode = input("Choose input mode — type 'v' for voice or 't' for text: ").strip().lower()
    if mode == "v":
        mode = "voice"
        print("🎙️  Voice mode selected.")
    else:
        mode = "text"
        print("⌨️  Text mode selected. Type your questions below.")

    print("─" * 40)

    telemetry = Telemetry()
    telemetry.start()
    print("📡 Telemetry running. Ready for driver input.")
    print("   (Press Ctrl+C to quit)\n")

    history = []
    last_urgency = 'green'  # track last alert level so we only speak on changes

    try:
        while True:
            # --- Proactive pit strategy check ---
            data = telemetry.get_data()
            rec = get_pit_recommendation(data)

            # Only print and speak when urgency level changes (e.g. green → yellow)
            if rec['urgency'] != last_urgency:
                alert = format_pit_alert(rec)
                if alert:
                    print(alert)
                    spoken_reason = rec['reason'].replace(';', ',').replace('  ', ' ')
                    if rec['should_pit']:
                        speak(f"Box box box. {spoken_reason}")
                    else:
                        speak(f"Strategy alert. {spoken_reason}")
                last_urgency = rec['urgency']

            # --- Get driver input ---
            user_input = get_input(mode)
            if not user_input:
                continue

            if mode == "voice":
                print(f"🎙️  Driver: {user_input}")

            # --- Fresh telemetry snapshot for the response ---
            data = telemetry.get_data()

            # --- Ask GPT with full history ---
            print("⏳ Thinking...")
            reply, history = ask_gpt(user_input, data, history)

            # --- Print and speak ---
            print(f"📻 Engineer: {reply}\n")
            speak(reply)

    except KeyboardInterrupt:
        print("\n🛑 Shutting down race engineer...")
        telemetry.stop()

if __name__ == "__main__":
    main()
