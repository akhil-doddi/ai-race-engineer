# AI Race Engineer

A real-time AI-powered Formula 1 race engineer — built to monitor live PS5 telemetry, reason about race strategy, and communicate decisions by voice the way a real pit wall engineer would.

---

## Overview

Most racing games give you a static pit timer and a generic message. This project replaces that entirely with a **live, event-driven AI engineer** that monitors your telemetry, detects meaningful race events, and speaks to you through your speakers — calm when things are nominal, decisive when they aren't.

The system connects directly to the Codemasters F1 24 UDP telemetry broadcast from a PS5 on the same local network. It parses binary packets in real time, builds a clean race state, evaluates strategy conditions every second, and calls GPT-4o-mini only when something worth saying has happened. The engineer stays silent unless the situation warrants it.

**This is an engineering project, not a chatbot wrapper.** The AI generates the language. Deterministic rules decide when and why.

---

## Key Features

- **Live PS5 telemetry** — binary Codemasters F1 24 UDP packets parsed at runtime on port 20777
- **Proactive strategy engine** — engineer speaks unprompted at race start, pit windows, plan changes, and safety car events
- **Pit stop simulation** — a Finite State Machine models the full pit sequence; tyre data updates automatically throughout the stop and across the new stint
- **Safety car logic** — fires exactly one pit call per SC period; all normal strategy triggers are blocked while the safety car is deployed
- **Post-pit tyre tracking** — after rejoining, tyre wear is computed from laps elapsed rather than relying on the raw UDP stream, which has no knowledge of pit stop decisions
- **Dual-thread architecture** — a proactive monitor polls telemetry every second independently of the driver's voice/text input loop
- **Voice I/O** — British male voice output via macOS `say` (thread-safe); Google Speech Recognition for driver input
- **Simulator sender** — `udp_sender.py` broadcasts a realistic race simulation without a PS5, enabling full development and testing offline

---

## System Architecture

The system is split into two independent processes connected by a UDP socket, and two threads within the main process. Understanding these boundaries is the key to understanding the design.

```
PROCESS 1: Telemetry Source
────────────────────────────
PS5 (Codemasters F1 24)              ← live race
       OR
src/telemetry/udp_sender.py          ← simulated race (no PS5 required)

Both broadcast structured UDP packets to 127.0.0.1:20777


PROCESS 2: AI Race Engineer
────────────────────────────
                ┌─────────────────────────────────────────┐
THREAD 1        │  ProactiveMonitor (1-second poll loop)  │
(background)    │                                          │
                │  UDPListener → Controller → RaceState   │
                │        → EventDetector → Tracker        │
                │              → AI → speak()             │
                └────────────────────┬────────────────────┘
                                     │ shared state
                ┌────────────────────▼────────────────────┐
THREAD 2        │  Main loop (driver interaction)         │
(main)          │                                          │
                │  listen() → ask_engineer() → speak()    │
                └─────────────────────────────────────────┘
```

Data flow within the engineer process:

```
UDP Packets
     │
     ▼
UDPTelemetryListener        ← binary parser (PacketCarTelemetry, PacketSession)
     │
     ▼
TelemetryController         ← proxy layer; applies active pit overrides on get_snapshot()
     │   └── PitStateMachine    ← FSM: RACING → PIT_ENTRY → PIT_STOP → PIT_EXIT → RACING
     │
     ▼
build_race_state()          ← normalises raw dict into typed, validated race_state
     │
     ▼
get_event()                 ← urgency rules (green / yellow / red), SC detection
     │
     ▼
StrategyTracker.evaluate()  ← lap-by-lap trigger engine; fires INITIAL_BRIEF,
     │                          PLAN_CHANGED, PIT_APPROACHING, PIT_NOW, SC_OPPORTUNITY
     │
     ▼
ask_engineer()              ← GPT-4o-mini with rolling conversation history
     │
     ▼
speak()                     ← macOS `say` subprocess
```

**Command feedback path:** When the AI confirms a pit stop, `trigger_pit()` is called on the TelemetryController. This starts the PitStateMachine, which overrides the tyre fields returned by every subsequent `get_snapshot()` call until the pit sequence completes.

---

## Architecture Diagram

See [`docs/architecture.md`](docs/architecture.md) for the full annotated ASCII diagram.

---

## Component Breakdown

### Telemetry Source
`src/telemetry/udp_listener.py` | `src/telemetry/udp_sender.py` | `src/telemetry/simulator.py`

Responsible for producing raw telemetry data. In production, `UDPTelemetryListener` opens a UDP socket and parses Codemasters F1 24 binary packets into Python dicts. `udp_sender.py` is a self-contained alternative that broadcasts realistic lap-by-lap race data for offline development. `simulator.py` (Phase 1) is still available as a lightweight test source with no networking at all.

All three implement the same interface: `start()`, `stop()`, `get_snapshot()`.

---

### Telemetry Controller
`src/telemetry/telemetry_controller.py` | `src/telemetry/pit_state_machine.py`

A transparent proxy that wraps any telemetry source and layers dynamic overrides on top. When no pit stop is active, raw data passes through unchanged. When a pit is triggered, the embedded `PitStateMachine` takes over and returns appropriate field values for each phase.

After the pit sequence completes and the car rejoins the track, the controller maintains a `_post_pit` record and computes fresh tyre wear (`100% - laps_since_pit × 3.5%`) on every subsequent snapshot call. This is necessary because the raw UDP stream has no awareness of decisions made outside the game.

Thread-safe: `trigger_pit()` and `get_snapshot()` share a `threading.Lock()`.

---

### Race State Builder
`src/race_state/state_manager.py`

Normalises the raw telemetry dict from any source into a clean, typed, consistently shaped `race_state`. Applies defaults for missing keys, casts types, and ensures all downstream layers receive the same structure regardless of which telemetry source is active. This is the ADR-003 abstraction that made the Phase 1 → Phase 2 migration a single-line change.

---

### Event Detector
`src/events/event_detector.py`

A fast, deterministic rule engine that takes a `race_state` dict and returns an `event` dict with an urgency level (`green` / `yellow` / `red`), a `should_pit` boolean, a reason string, and a `safety_car` flag. No AI involved here — all decisions are mathematical.

Key rules: tyre life thresholds (<15% red, <30% yellow), fuel projection, pit window by stint age, SC opportunity (requires `tire_age >= SC_MIN_TYRE_AGE = 5` to prevent boxing immediately after a recent stop), gap alerts, and an end-of-race override that cancels pit recommendations with 3 laps remaining.

---

### Strategy Engine
`src/strategy/strategy_tracker.py`

The module that transforms the system from a reactive chatbot into a proactive race engineer. Called every lap by the proactive monitor, it maintains state across laps (pit plan, previous estimates, flags) and returns a list of trigger names when the engineer should speak.

Planned pit lap is recalculated each lap from `current_lap + laps_left_on_tyre` and compared against the previous estimate. A shift of 3+ laps fires `PLAN_CHANGED`. A set of guard flags (`_pit_called`, `_approaching_called`, `_sc_pit_called`, `_last_spoken_lap`) prevents any trigger from firing more than once under the correct conditions.

---

### AI Engineer
`src/communication/response_generator.py`

Calls GPT-4o-mini with a structured system prompt that establishes the engineer persona, plus a rolling history of up to 20 messages for conversational context. The `build_prompt()` method in `StrategyTracker` constructs tightly scoped instructions for each trigger type — the AI is told *what to communicate*, not asked to decide *whether* to communicate. This keeps responses consistent, radio-appropriate, and latency-bounded.

---

### Voice Interface
`src/voice/tts_engine.py` | `src/voice/voice_input.py`

`speak()` passes text through `clean_for_speech()` (which expands abbreviations like `P14 → Position 14` and `DRS → D R S`) and calls the macOS `say` subprocess with the configured voice and rate. This is a deliberate departure from pyttsx3, which raises `RuntimeError: run loop already started` when called from a background thread on macOS.

`listen()` wraps Google Speech Recognition with error handling — returning an empty string (not raising an exception) when the microphone fails, allowing the main loop to continue.

---

## Engineering Challenges Solved

### Modifying Read-Only Telemetry Dynamically
The PS5 game's UDP stream is a read-only broadcast. It has no concept of a pit stop triggered by our system — it continues sending the old tyre compound and wear values after the driver pits. The solution is the TelemetryController proxy: all consumers call `get_snapshot()` on the controller, not on the raw listener. The controller intercepts the call and applies current overrides, making the field modification invisible to the rest of the system.

### Dual-Thread Synchronisation
The proactive monitor thread and the main driver-input thread both call `get_snapshot()` concurrently. The PitStateMachine's `tick()` advances its internal state on every call, meaning concurrent calls without locking would corrupt state. A single `threading.Lock()` in the controller serialises all state machine access. The lock is held only for the state machine operations, not for the downstream processing, to minimise contention.

### Proactive AI Decision Model
Standard LLM integrations are reactive: user sends a message, AI responds. Race engineering requires the opposite — the AI should interrupt when conditions warrant it. The StrategyTracker solves this by decoupling the trigger decision from the AI call. Deterministic rules fire the trigger; the AI only receives a briefing prompt after the trigger is confirmed. This keeps trigger latency near zero and AI latency contained to the response generation step.

### Pit Stop Finite State Machine
A boolean `is_pitting` flag cannot represent the ordered phases of a pit stop, each with different duration and different telemetry behaviour. The FSM makes each phase explicit and prevents invalid transitions — a second `trigger_pit()` call while already in `PIT_STOP` returns `False` immediately. The phase state determines which fields are overridden and for how long.

### Safety Car Event Handling
The safety car window is time-critical and must not trigger more than once per SC period, and must not allow normal strategy triggers to "leak through" while the SC is deployed. The `_sc_pit_called` flag enforces the once-per-period rule. The `return []` statement at the end of the SC block — not `pass`, not `continue` — ensures the function exits immediately, blocking `PLAN_CHANGED`, `PIT_NOW`, and `PIT_APPROACHING` from evaluating on the same lap.

---

## Demo

> **Note:** Full demo GIF and video recording coming in a future update.

### Terminal Output Example

```
[LAP 2]  🟢  Engineer: "You're running P8 on Medium tyres, fuel looking good.
                         Planning to box around lap 24. Focus on tyre management
                         through sector two."

[LAP 21] 🟡  Engineer: "Heads up — pit stop in 3 laps. Box lap 24, we're going
                         onto Hard tyres."

[LAP 24] 🔴  Engineer: "Box box box. Tyres at 28 percent. Come in this lap,
                         Hard tyres ready."

🔴 PIT STOP — entering pit lane  (current: Medium  →  new: Hard)
🔧 PIT STOP — stationary, tyres being changed  (5s)
   ⏱  4s  ·  fitting Hard tyres...
   ⏱  3s  ·  fitting Hard tyres...
✅ TYRES FITTED — Hard at 100% life  |  exiting pit lane
🟢 PIT EXIT COMPLETE — rejoining on fresh Hard tyres

[LAP 38] SC Engineer: "Safety car on lap 38, this is a free pit window.
                         Box box box — we're going onto Soft tyres,
                         14 laps remaining, use them."
```

### Voice Interaction Example

```
Driver:   "How are the tyres looking?"
Engineer: "Tyres at sixty-two percent, Medium, about eleven laps left on
           this set before we're into the pit window. You're looking good
           for now — just watch the rear left through Sector 3."

Driver:   "Is the guy behind a threat?"
Engineer: "Car behind is 1.8 seconds, not a threat yet. If we keep this
           pace through the next two sectors, we maintain the gap."
```

### Running in Test Mode (No PS5 Required)

```bash
# Terminal 1 — start the telemetry simulation
python3 -m src.telemetry.udp_sender

# Terminal 2 — start the AI engineer
python3 -m src.main
# → choose 'u' (UDP) then 't' (text mode)
```

---

## Running the Project

### Requirements

- Python 3.11+
- macOS (for `say` TTS — Linux/Windows fall back to pyttsx3)
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

Voice and rate can be adjusted in `config/settings.py`.

### PS5 Setup

1. F1 Game → Settings → Telemetry Settings → **UDP Telemetry: ON**
2. Set **Broadcast IP** to your PC's local IP address (e.g. `192.168.1.50`)
3. Set **UDP Port: 20777**
4. PC and PS5 must be on the same local network

### Run with Live PS5 Telemetry

**Terminal 1:**
```bash
python3 -m src.main
# → select 'u' for UDP telemetry
# → select 'v' for voice input or 't' for text input
```

Start your race on the PS5. The engineer will brief you on lap 2.

### Run in Simulator Mode (No PS5)

**Terminal 1:**
```bash
python3 -m src.telemetry.udp_sender
```

**Terminal 2:**
```bash
python3 -m src.main
# → select 'u' then 't'
```

### Run Tests

```bash
pytest tests/ -v
```

---

## Project Structure

```
ai-race-engineer/
├── src/
│   ├── telemetry/
│   │   ├── udp_listener.py         Live PS5 UDP telemetry parser
│   │   ├── udp_sender.py           Race simulation sender (test / demo mode)
│   │   ├── simulator.py            Simulated telemetry (Phase 1, no networking)
│   │   ├── telemetry_controller.py Proxy wrapper — applies pit overrides transparently
│   │   └── pit_state_machine.py    FSM: RACING → PIT_ENTRY → PIT_STOP → PIT_EXIT
│   ├── race_state/
│   │   └── state_manager.py        Raw dict → typed race_state normalisation
│   ├── events/
│   │   └── event_detector.py       Urgency rules, SC detection, pit window logic
│   ├── strategy/
│   │   └── strategy_tracker.py     Proactive trigger engine (lap-by-lap evaluation)
│   ├── communication/
│   │   └── response_generator.py   GPT-4o-mini integration with rolling history
│   ├── voice/
│   │   ├── tts_engine.py           macOS `say` / pyttsx3 fallback TTS
│   │   └── voice_input.py          Google Speech Recognition wrapper
│   └── main.py                     Dual-thread orchestrator
├── config/
│   └── settings.py                 Voice, rate, model, and port configuration
├── docs/
│   ├── architecture.md             Annotated system architecture diagram
│   ├── decisions.md                Architecture Decision Records (ADR-001 – ADR-012)
│   ├── roadmap.md                  Phased development plan
│   └── ai_context.md               Context file for AI development assistants
├── tests/
│   ├── test_telemetry.py           Simulator, state manager, event detector, FSM, controller
│   ├── test_voice_input.py         TTS cleaning, speak(), listen() — all mocked
│   └── test_strategy.py            StrategyTracker trigger logic and SC behaviour
├── CHANGELOG.md                    Version history
├── requirements.txt                Pinned dependencies
└── .env                            API key (not committed)
```

---

## Proactive Triggers

The strategy engine speaks without being asked when any of these conditions are met:

| Trigger | Condition | Fires |
|---|---|---|
| `INITIAL_BRIEF` | Lap 2 reached | Once per race |
| `PLAN_CHANGED` | Pit window shifts by 3+ laps | Once per shift |
| `PIT_APPROACHING` | 3 laps before planned box lap | Once per stint |
| `PIT_NOW` | At planned pit lap, `should_pit = True` | Once per stint |
| `SC_OPPORTUNITY` | SC deployed, tyres ≥ 5 laps old | Once per SC period |

---

## Future Improvements

| Phase | Status | Goal |
|---|---|---|
| 1 | ✅ Complete | Simulated telemetry, GPT-4o-mini engineer, voice I/O |
| 2 | ✅ Complete | Live UDP telemetry, pit FSM, proactive strategy engine |
| 3 | 🔜 Next | Full event detection: VSC, DRS zones, position changes, fuel save mode |
| 4 | Planned | Adaptive communication modes (analytical / alert / command) |
| 5 | Planned | Persistent race memory across full session |
| 6 | Planned | Push-to-talk voice, cloud TTS evaluation (ElevenLabs / Azure) |
| 7 | Future | Dockerization and container networking for UDP |
| 8 | Future | GitHub Actions CI/CD with test coverage reporting |

Full phase detail in [`docs/roadmap.md`](docs/roadmap.md).

---

## Architecture Decisions

Key design choices are documented with full reasoning in [`docs/decisions.md`](docs/decisions.md). This covers: event-driven triggering, the hybrid rule + AI model, the abstraction layer approach, why pyttsx3 was replaced, how the FSM was chosen over a boolean flag, and more (ADR-001 through ADR-012).

---

## Author

Built by Akhil — F1 fan and Python developer learning systems engineering by building something real.
