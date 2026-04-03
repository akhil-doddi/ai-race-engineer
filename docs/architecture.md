# System Architecture

## Overview

The AI Race Engineer is a layered, event-driven real-time system. Each layer has a single responsibility and communicates only with the layer directly above or below it. No layer skips the chain.

---

## Data Flow

```
[PS5 — F1 Game]
      │
      │  UDP broadcast @ 20777 (Phase 2)
      │  Simulated in-process (Phase 1)
      ▼
┌─────────────────────────────────┐
│  TELEMETRY LAYER                │  src/telemetry/
│  simulator.py / udp_listener.py │
│  Produces: raw telemetry dict   │
└────────────────┬────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│  RACE STATE LAYER               │  src/race_state/
│  state_manager.py               │
│  Produces: clean race_state     │
└────────────────┬────────────────┘
                 │
        ┌────────┴─────────┐
        ▼                  ▼
┌───────────────┐  ┌─────────────────────────────┐
│  EVENT LAYER  │  │  COMMUNICATION LAYER        │
│  event_       │  │  response_generator.py      │
│  detector.py  │  │  (GPT-4o-mini)              │
│               │  │                             │
│  Proactive    │  │  Reactive (driver asks)     │
│  alerts only  │  │  + proactive (event-driven) │
└───────┬───────┘  └──────────────┬──────────────┘
        │                         │
        └──────────┬──────────────┘
                   ▼
┌─────────────────────────────────┐
│  VOICE LAYER                    │  src/voice/
│  tts_engine.py                  │
│  voice_input.py                 │
└────────────────┬────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│  ORCHESTRATOR                   │  src/main.py
│  Coordinates all layers         │
│  Manages session state          │
└─────────────────────────────────┘
```

---

## Layer Responsibilities

### Telemetry Layer (`src/telemetry/`)
- **What:** Produces a raw data dictionary representing the current car state.
- **Phase 1:** `simulator.py` — generates realistic data mathematically.
- **Phase 2:** `udp_listener.py` — receives real packets from the F1 game on PS5.
- **Contract:** Both sources produce the same dictionary structure. Nothing above this layer knows which source is active.

### Race State Layer (`src/race_state/`)
- **What:** Converts raw telemetry into a validated, typed race_state object.
- **Why it exists:** Provides a stable interface boundary. When Phase 2 introduces real UDP data, only this layer changes. All layers above remain untouched.

### Event Layer (`src/events/`)
- **What:** Applies deterministic racing rules to decide if the engineer should speak proactively.
- **Why deterministic:** Fast, zero-latency, zero-cost rule evaluation. AI is only invoked AFTER this layer confirms something meaningful has happened.
- **Output:** urgency level (green/yellow/red), reason string, pit recommendation.

### Communication Layer (`src/communication/`)
- **What:** Constructs prompts and manages the GPT API conversation.
- **Why separate from events:** The event layer decides WHEN to speak. The communication layer decides HOW to speak. These are different responsibilities.
- **Memory:** Rolling 20-message history window keeps the engineer contextually aware throughout the race.

### Voice Layer (`src/voice/`)
- **What:** Handles microphone input and TTS output.
- **Why separate:** I/O concerns are isolated from business logic. TTS provider can be swapped (pyttsx3 → ElevenLabs) without touching any other layer.

### Orchestrator (`src/main.py`)
- **What:** Runs the main loop, coordinates layer calls in the correct sequence.
- **What it does NOT do:** No business logic. No prompt construction. No strategy calculation. Pure coordination.

---

## Real-Time Performance Model

The system is optimised for low latency:

1. **Telemetry updates every 5 seconds** (background thread, non-blocking)
2. **Event detection is synchronous and instant** (pure Python math, no I/O)
3. **AI is called only when needed** (not every loop iteration)
4. **Voice output blocks intentionally** (prevents input prompt appearing mid-speech)

Target end-to-end latency: under 1.5 seconds from event detection to first spoken word.

---

## Phase 2 Integration Point

To connect live PS5 telemetry, only one change is needed in `src/main.py`:

```python
# Current (Phase 1)
from src.telemetry.simulator import TelemetrySimulator
telemetry = TelemetrySimulator()

# Phase 2
from src.telemetry.udp_listener import UDPTelemetryListener
telemetry = UDPTelemetryListener()
```

All other layers remain unchanged.
