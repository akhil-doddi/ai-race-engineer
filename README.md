# 🏎️ AI Race Engineer

A real-time AI-powered Formula 1 race engineer that speaks, listens, and thinks like a pit wall engineer — running on your PC while you race on PS5.

---

## Project Vision

Most racing games give you a static strategy screen. This project replaces that with a **live, conversational AI engineer** that monitors your telemetry, detects critical race events, and communicates with you the way a real F1 engineer would on the radio — calm when things are fine, decisive when they aren't.

The system is **event-driven**, not chatbot-driven. The engineer stays silent until something worth saying happens, or until you ask.

---

## Current Status

**Phase 1 complete** — Fully working with simulated telemetry.

- Voice and text input modes
- GPT-4o-mini powered race engineer responses
- Realistic simulated telemetry (tyre degradation, fuel burn, lap time physics)
- Proactive pit window alerts (green / yellow / red urgency)
- Conversation memory across the full race session
- British male voice output via macOS TTS

---

## System Architecture

```
PS5 (F1 Game)
    │
    │  UDP Telemetry Broadcast (Phase 2+)
    ▼
src/telemetry/
    simulator.py        ← Phase 1: simulated telemetry
    udp_listener.py     ← Phase 2: live PS5 telemetry (placeholder)
    │
    ▼
src/race_state/
    state_manager.py    ← converts raw data into clean race_state object
    │
    ▼
src/events/
    event_detector.py   ← detects pit windows, undercuts, fuel risk
    │
    ├──────────────────────────────────┐
    ▼                                  ▼
src/communication/             src/voice/
    response_generator.py          tts_engine.py
    (GPT-4o-mini)                  voice_input.py
    │                                  │
    └──────────────┬───────────────────┘
                   ▼
              src/main.py
              (orchestrator)
```

**Data flow:** Telemetry → Race State → Event Detection → AI (only when needed) → Voice

---

## Setup

### Requirements
- Python 3.11+
- macOS (for built-in TTS voices)
- OpenAI API key

### Installation

```bash
git clone https://github.com/akhil-doddi/ai-race-engineer.git
cd ai-race-engineer
pip install -r requirements.txt
```

### Configuration

Create a `.env` file in the project root:

```
OPENAI_API_KEY=your-key-here
```

### Run

```bash
python3 -m src.main
```

---

## Project Structure

```
ai-race-engineer/
├── src/
│   ├── telemetry/          Data ingestion layer
│   │   ├── simulator.py    Phase 1: simulated telemetry
│   │   └── udp_listener.py Phase 2: PS5 UDP telemetry (placeholder)
│   ├── race_state/         State abstraction layer
│   │   └── state_manager.py
│   ├── events/             Event detection layer
│   │   └── event_detector.py
│   ├── communication/      AI reasoning layer
│   │   └── response_generator.py
│   ├── voice/              I/O layer
│   │   ├── tts_engine.py
│   │   └── voice_input.py
│   └── main.py             Runtime orchestrator
├── config/
│   └── settings.py         All configuration constants
├── docs/
│   ├── architecture.md     System design and data flow
│   ├── decisions.md        Why key decisions were made
│   ├── roadmap.md          Phased development plan
│   └── ai_context.md       Context file for AI assistants
├── tests/                  Test suite
├── .env                    API keys (not committed)
├── requirements.txt        Pinned dependencies
└── CHANGELOG.md            Version history
```

---

## Development Roadmap

| Phase | Status | Goal |
|-------|--------|------|
| 1 | ✅ Complete | Simulated telemetry, working AI engineer |
| 2 | 🔜 Next | Live UDP telemetry from PS5 |
| 3 | Planned | Full event detection system |
| 4 | Planned | Adaptive communication modes |
| 5 | Planned | Persistent race memory |
| 6 | Planned | Voice interaction during live gameplay |
| 7 | Future | Dockerization |
| 8 | Future | CI/CD and cloud deployment |

---

## Author

Built by Akhil — F1 fan, Python learner, and aspiring AI engineer.
