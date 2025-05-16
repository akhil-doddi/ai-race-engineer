import speech_recognition as sr

r = sr.Recognizer()

with sr.Microphone() as source:
    print("🎙️ Say something...")
    audio = r.listen(source)

try:
    print("You said: " + r.recognize_google(audio))
except Exception as e:
    print("Recognition error:", e)
