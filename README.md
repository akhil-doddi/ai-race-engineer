# 🏎️ AI Race Engineer

A real-time AI-powered Formula 1 race engineer that speaks, listens, and thinks like a pit wall engineer — running on your PC while you race on PS5.

---

## Project Vision

Most racing games give you a static strategy screen. This project replaces that with a **live, conversational AI engineer** that monitors your telemetry, detects critical race events, and communicates with you the way a real F1 engineer would on the radio — calm when things are fine, decisive when they aren't.

The system is **event-driven**, not chatbot-driven. The engineer stays silent until something worth saying happens, or until you ask.

---

## Current Status

**Phase 2 complete** — Fully working with live PS5 UDP telemetry.

- Live Codemasters F1 24 UDP telemetry on port 20777
- Interactive pit stop simulation — AI decisions change telemetry state in real time
- Proactive strategy engine: race briefs, pit window alerts, safety car calls
- Safety car logic: one pit prompt per SC period, all normal triggers blocked during SC
- Auto-pit trigger at 50% tyre life with post-pit tyre wear tracking
- British male voice output via macOS `say` (thread-safe, no pyttsx3 dependency)
- Dual-thread architecture: proactive monitor + reactive driver input
- Full simulator sender for testing without a PS5

---

## System Architecture

```
PS5 (F1 Game)  ──or──  udp_sender.py (test mode)
    │
    │  UDP Broadcast  port 20777
    ▼
src/telemetry/udp_listener.py     ← parses binary Codemasters F1 24 packets
    │
    ▼
src/telemetry/telemetry_controller.py   ← wraps source; applies pit stop overrides
    │   └── pit_state_machine.py        ← FSM: RACING→PIT_ENTRY→PIT_STOP→PIT_EXIT
    │
    ▼
src/race_state/state_manager.py   ← normalises raw data into clean race_state dict
    │
    ▼
src/events/event_detector.py      ← green / yellow / red urgency rules
    │
    ▼
src/strategy/strategy_tracker.py  ← proactive trigger engine (lap-by-lap)
    │
    ├──────────────────────────────────┐
    ▼                                  ▼
src/communication/             src/voice/
    response_generator.py          tts_engine.py   (macOS `say`)
    (GPT-4o-mini)                  voice_input.py
    │                                  │
    └──────────────┬───────────────────┘
                   ▼
              src/main.py  (dual-thread orchestrator)
```

**Data flow:** Telemetry → Controller → Race State → Events → Strategy Triggers → AI → Voice

---

## How to Run

### Requirements
- Python 3.11+
- macOS (for built-in `say` TTS)
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

### Run with PS5

**Terminal 1** — start the main engineer:
```bash
python3 -m src.main
# → choose 'u' for UDP, then 't' for text or 'v' for voice
```

**PS5 setup:**
1. F1 Game → Settings → Telemetry → UDP Telemetry: ON
2. Broadcast IP = your PC's local IP
3. Port: 20777

### Run without PS5 (test/demo mode)

**Terminal 1:**
```bash
python3 -m src.telemetry.udp_sender
```

**Terminal 2:**
```bash
python3 -m src.main   # → choose 'u' then 't'
```

---

## Project Structure

```
ai-race-engineer/
├── src/
│   ├── telemetry/
│   │   ├── simulator.py            Simulated telemetry (Phase 1)
│   │   ├── udp_listener.py         Live PS5 UDP telemetry parser
│   │   ├── udp_sender.py           Race simulation sender (test mode)
│   │   ├── telemetry_controller.py Pit stop override wrapper
│   │   └── pit_state_machine.py    Pit stop FSM
│   ├── race_state/
│   │   └── state_manager.py        Raw → clean race_state normalisation
│   ├── events/
│   │   └── event_detector.py       Urgency rules and safety car detection
│   ├── strategy/
│   │   └── strategy_tracker.py     Proactive trigger engine
│   ├── communication/
│   │   └── response_generator.py   GPT-4o-mini AI layer
│   ├── voice/
│   │   ├── tts_engine.py           macOS say / pyttsx3 fallback
│   │   └── voice_input.py          Google Speech Recognition
│   └── main.py                     Dual-thread orchestrator
├── config/
│   └── settings.py                 All configuration constants
├── docs/
│   ├── architecture.md             System design and data flow
│   ├── decisions.md                Architecture decision records (ADRs)
│   ├── roadmap.md                  Phased development plan
│   └── ai_context.md               Context file for AI assistants
├── tests/                          Test suite (pytest)
├── .env                            API keys (not committed)
├── requirements.txt                Pinned dependencies
└── CHANGELOG.md                    Version history
```

---

## Proactive Triggers

The strategy engine speaks without being asked when any of these conditions are met:

| Trigger | When it fires |
|---------|--------------|
| `INITIAL_BRIEF` | Lap 2 — one-time race start summary |
| `PLAN_CHANGED` | Pit window shifts by 3+ laps |
| `PIT_APPROACHING` | 3 laps before planned box lap |
| `PIT_NOW` | At planned pit lap with tyre confirmation |
| `SC_OPPORTUNITY` | Safety car deployed, tyres 5+ laps old — once per SC period |

---

## Development Roadmap

| Phase | Status | Goal |
|-------|--------|------|
| 1 | ✅ Complete | Simulated telemetry, working AI engineer |
| 2 | ✅ Complete | Live UDP telemetry, pit simulation, proactive strategy |
| 3 | 🔜 Next | Full event detection (VSC, DRS, position changes, fuel save) |
| 4 | Planned | Adaptive communication modes (analytical / alert / command) |
| 5 | Planned | Persistent race memory across full race session |
| 6 | Planned | Voice optimisation — push-to-talk, cloud TTS |
| 7 | Future | Dockerization |
| 8 | Future | CI/CD and cloud deployment |

---

## Author

Built by Akhil — F1 fan, Python learner, and aspiring AI engineer.
