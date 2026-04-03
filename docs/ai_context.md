# AI Assistant Context File

This file exists to give any AI assistant instant, accurate context about this project
so it can contribute effectively without needing to re-analyse everything from scratch.

---

## What This Project Is

A real-time AI-powered F1 race engineer that runs on a PC while the developer plays
the F1 game on PS5. The system listens to race telemetry, detects critical events,
and communicates strategy via voice — behaving like a real pit wall engineer.

**This is NOT a chatbot. It is an event-driven real-time AI agent.**

---

## Current Phase

Phase 1 is complete. The system runs fully with simulated telemetry.
Phase 2 is next: replacing the simulator with live UDP telemetry from the PS5.

---

## Architecture Summary

```
Telemetry → Race State → Event Detection → AI (only when needed) → Voice
```

Six layers, each with a single responsibility. No layer skips the chain.
The AI (GPT-4o-mini) is called ONLY when an event triggers or the driver asks.

**Key files:**
- `src/main.py` — orchestrator, entry point
- `src/telemetry/simulator.py` — Phase 1 data source
- `src/telemetry/udp_listener.py` — Phase 2 placeholder (not yet implemented)
- `src/race_state/state_manager.py` — normalises raw data into clean race_state
- `src/events/event_detector.py` — deterministic rules, decides WHEN to speak
- `src/communication/response_generator.py` — GPT prompt builder + memory
- `src/voice/tts_engine.py` — text-to-speech output
- `src/voice/voice_input.py` — microphone input
- `config/settings.py` — all configuration constants

---

## Critical Rules for Any AI Working on This Project

1. **Never call GPT continuously.** AI is invoked only on driver questions or event triggers.
2. **Never let raw telemetry reach the AI.** Only the clean race_state from state_manager passes to the communication layer.
3. **Do not add complexity unnecessarily.** Prefer simple, deterministic logic before reaching for AI.
4. **Maintain layer boundaries.** Each layer imports only from the layer directly below it.
5. **Do not rewrite working logic.** Phase 1 is stable. Extend it, don't replace it.
6. **Explain WHY before changing anything.** Every architectural decision has a reason documented in decisions.md.

---

## Phase 2 Integration

To connect live PS5 telemetry, change exactly two lines in `src/main.py`:

```python
# From:
from src.telemetry.simulator import TelemetrySimulator
telemetry = TelemetrySimulator()

# To:
from src.telemetry.udp_listener import UDPTelemetryListener
telemetry = UDPTelemetryListener()
```

No other file changes required. This is the architectural boundary that was
deliberately built to make this migration clean.

---

## Developer Profile

Intermediate Python beginner. Prefers small incremental improvements with clear
explanations. Understands the code but benefits from guidance on architectural
decisions. Has strong domain knowledge of F1 racing.

Always explain WHY before changing code. Always prefer the smallest safe change.
