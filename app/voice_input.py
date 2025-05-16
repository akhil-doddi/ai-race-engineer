### app/voice_input.py
import speech_recognition as sr

recognizer = sr.Recognizer()

def listen_to_driver():
    with sr.Microphone() as source:
        print("\n🎙️ Speak...")
        audio = recognizer.listen(source)
    try:
        return recognizer.recognize_google(audio)
    except sr.UnknownValueError:
        print("Didn't catch that.")
        return ""
    except Exception as e:
        print(f"Error: {e}")
        return ""