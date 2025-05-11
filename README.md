# AI Race Engineer Project

# 🏎️ AI Race Engineer

**AI Race Engineer** is a voice-interactive assistant for simulating Formula 1-style radio updates, strategy insights, and telemetry analysis. Built for offline use with a local LLM, it can be extended to use OpenAI's GPT-3.5 or GPT-4 for faster and richer responses.

---

## 📦 Features

- 🎙️ Real-time voice input and speech recognition
- 💬 AI-generated race engineer responses
- 🧠 Local LLM with `llama-cpp-python` (Nous Hermes 2 - Mistral model)
- 📈 Prompt understanding of race strategy, lap times, tire wear, and more
- 🧪 Option to switch to GPT-3.5 (paid) for better performance
- 📁 Fully documented and ready to showcase in a GitHub portfolio

---

## 🛠️ Setup

### 1. Clone this repo:
```bash
git clone https://github.com/your-username/ai-race-engineer.git
cd ai-race-engineer
```

### 2. Install dependencies:
```bash
pip install -r requirements.txt
```

### 3. Download the local model (e.g. Nous Hermes 2 - Mistral GGUF)
Place it in:
```bash
./models/nous-hermes-2-mistral.Q4_0.gguf
```

Alternatively, switch to GPT-3.5 by updating `ai_engineer.py` with OpenAI credentials.

### 4. Run the assistant:
```bash
python3 ai_engineer.py
```

---

## 🧠 Model Options

| Option         | Description                         |
|----------------|-------------------------------------|
| Local LLM      | Mistral GGUF with `llama-cpp-python`|
| Cloud API      | GPT-3.5 via OpenAI API (paid tier)  |

To switch to GPT-3.5, update `get_ai_response()` in `ai_engineer.py` to use `openai.ChatCompletion.create(...)`.

---

## 🚀 Example Voice Command

```
You: "Give me a race update"
AI: "Lewis, you're currently in P4 with a last lap of 1:32.4..."
```

---

## 📂 File Structure

```
ai-race-engineer/
├── ai_engineer.py         # Main application loop
├── models/                # Folder for .gguf models
├── requirements.txt       # Dependencies
├── README.md              # This documentation
```

---

## 🧪 Roadmap

- [x] Voice-to-AI prompt loop (done ✅)
- [ ] Telemetry parser
- [ ] Dashboard or command-line summary view
- [ ] GPT-4 mode for richer insights

---

## 👨‍💻 Author
Made with ❤️ by [Akhil], guided by ChatGPT.

---

## 📄 License
MIT License

---

> This is a demo project for learning and showcasing AI + voice integration. Perfect to feature in your resume, portfolio, or GitHub profile.

