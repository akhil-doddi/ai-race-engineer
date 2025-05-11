import speech_recognition as sr
import pyttsx3
from llama_cpp import Llama

# Load the local model
llm = Llama(model_path="./models/Nous-Hermes-2-Mistral-7B-DPO.Q4_0.gguf")

# Set up speech recognition and voice output
recognizer = sr.Recognizer()
engine = pyttsx3.init()

def speak(text):
    print("Engineer:", text)
    engine.say(text)
    engine.runAndWait()

def get_ai_response(prompt):
    result = llm.create_chat_completion(
        messages=[
            {"role": "system", "content": "You are a Formula 1 race engineer. Respond with short, realistic race updates in a calm, professional tone."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7,
        max_tokens=256
    )
    return result["choices"][0]["message"]["content"]

# Main voice loop
print("Ready. Say something...")

while True:
    with sr.Microphone() as source:
        audio = recognizer.listen(source)
        try:
            print("Recognizing...")
            user_input = recognizer.recognize_google(audio)
            print("You said:", user_input)

            ai_reply = get_ai_response(user_input)
            speak(ai_reply)

        except sr.UnknownValueError:
            print("Sorry, I didn't catch that.")
        except sr.RequestError as e:
            print(f"Recognition error: {e}")
