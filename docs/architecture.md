# System Architecture

This document describes the full architecture of the AI Race Engineer system as of Phase 2 (v0.3.0). It covers the data flow diagram, component responsibilities, thread model, and the command feedback path that closes the loop between AI decisions and telemetry state.

---

## Full System Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│  PROCESS 1 — Telemetry Source                                           │
│                                                                         │
│   PS5 running Codemasters F1 24          OR    udp_sender.py            │
│   (live game telemetry)                        (simulated race, no PS5) │
│                                                                         │
│   Broadcasts binary UDP packets @ port 20777 on local network           │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │
                                    │  UDP  (60 Hz broadcast)
                                    │  binary Codemasters F1 24 format
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  PROCESS 2 — AI Race Engineer                                           │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  THREAD 1 — ProactiveMonitor  (1-second poll loop)              │   │
│  │                                                                  │   │
│  │  ┌────────────────────────────────────────────────────────────┐ │   │
│  │  │  Telemetry Source                                          │ │   │
│  │  │  src/telemetry/udp_listener.py                            │ │   │
│  │  │                                                            │ │   │
│  │  │  Parses PacketCarTelemetryData and PacketSessionData       │ │   │
│  │  │  from binary UDP stream into raw Python dicts.            │ │   │
│  │  │  Exposes: start() / stop() / get_snapshot()               │ │   │
│  │  └────────────────────────┬───────────────────────────────────┘ │   │
│  │                           │  raw dict                           │   │
│  │                           ▼                                     │   │
│  │  ┌────────────────────────────────────────────────────────────┐ │   │
│  │  │  Telemetry Controller                                      │ │   │
│  │  │  src/telemetry/telemetry_controller.py                    │ │   │
│  │  │                                                            │ │   │
│  │  │  Transparent proxy. Intercepts get_snapshot() and layers  │ │   │
│  │  │  overrides on top of raw data:                            │ │   │
│  │  │    • Active pit phase  → PitStateMachine.get_overrides()  │ │   │
│  │  │    • Post-pit stint    → computed wear from laps elapsed  │ │   │
│  │  │  Thread-safe via threading.Lock()                         │ │   │
│  │  │                                                            │ │   │
│  │  │   ┌──────────────────────────────────────────────────┐   │ │   │
│  │  │   │  PitStateMachine                                 │   │ │   │
│  │  │   │  src/telemetry/pit_state_machine.py             │   │ │   │
│  │  │   │                                                  │   │ │   │
│  │  │   │  RACING → PIT_ENTRY → PIT_STOP → PIT_EXIT       │   │ │   │
│  │  │   │    ↑                                    │        │   │ │   │
│  │  │   │    └────────────────────────────────────┘        │   │ │   │
│  │  │   │                                                  │   │ │   │
│  │  │   │  tick() advances state on every get_snapshot()  │   │ │   │
│  │  │   │  get_overrides() returns tyre field overrides   │   │ │   │
│  │  │   │  per phase. Duplicate trigger_pit() → False.    │   │ │   │
│  │  │   └──────────────────────────────────────────────────┘   │ │   │
│  │  └────────────────────────┬───────────────────────────────────┘ │   │
│  │                           │  raw dict + overrides applied       │   │
│  │                           ▼                                     │   │
│  │  ┌────────────────────────────────────────────────────────────┐ │   │
│  │  │  Race State Builder                                        │ │   │
│  │  │  src/race_state/state_manager.py                          │ │   │
│  │  │                                                            │ │   │
│  │  │  Normalises raw dict into a typed, validated race_state.  │ │   │
│  │  │  Applies defaults. Casts types. Stable interface contract. │ │   │
│  │  │  All downstream layers receive the same shape regardless  │ │   │
│  │  │  of which telemetry source is active.                     │ │   │
│  │  └────────────────────────┬───────────────────────────────────┘ │   │
│  │                           │  race_state dict                    │   │
│  │                           ▼                                     │   │
│  │  ┌────────────────────────────────────────────────────────────┐ │   │
│  │  │  Event Detector                                            │ │   │
│  │  │  src/events/event_detector.py                             │ │   │
│  │  │                                                            │ │   │
│  │  │  Deterministic rule engine. No AI here.                   │ │   │
│  │  │  Evaluates: tyre life, fuel projection, stint age,        │ │   │
│  │  │  gap deltas, safety car flag, end-of-race override.       │ │   │
│  │  │  Returns: urgency (green/yellow/red), should_pit,         │ │   │
│  │  │           reason string, safety_car bool.                 │ │   │
│  │  └────────────────────────┬───────────────────────────────────┘ │   │
│  │                           │  event dict                         │   │
│  │                           ▼                                     │   │
│  │  ┌────────────────────────────────────────────────────────────┐ │   │
│  │  │  Strategy Engine                                           │ │   │
│  │  │  src/strategy/strategy_tracker.py                         │ │   │
│  │  │                                                            │ │   │
│  │  │  Lap-by-lap stateful trigger evaluation.                  │ │   │
│  │  │  Recalculates planned_pit_lap each lap from tyre data.    │ │   │
│  │  │  Fires triggers when conditions are met:                  │ │   │
│  │  │    INITIAL_BRIEF  │ PLAN_CHANGED  │ PIT_APPROACHING       │ │   │
│  │  │    PIT_NOW        │ SC_OPPORTUNITY                        │ │   │
│  │  │  Guard flags prevent duplicate firing:                    │ │   │
│  │  │    _pit_called, _approaching_called, _sc_pit_called,      │ │   │
│  │  │    _last_spoken_lap                                       │ │   │
│  │  └────────────────────────┬───────────────────────────────────┘ │   │
│  │                           │  trigger list  e.g. ["PIT_NOW"]     │   │
│  │                           ▼                                     │   │
│  │  ┌────────────────────────────────────────────────────────────┐ │   │
│  │  │  AI Engineer                                               │ │   │
│  │  │  src/communication/response_generator.py                  │ │   │
│  │  │                                                            │ │   │
│  │  │  GPT-4o-mini with rolling 20-message history window.      │ │   │
│  │  │  Receives: build_prompt(trigger, race_state, event)       │ │   │
│  │  │  The AI generates the words; the trigger determines       │ │   │
│  │  │  what the words communicate. Never decides whether to     │ │   │
│  │  │  speak — only how.                                        │ │   │
│  │  └────────────────────────┬───────────────────────────────────┘ │   │
│  │                           │  response string                    │   │
│  │                           ▼                                     │   │
│  │  ┌────────────────────────────────────────────────────────────┐ │   │
│  │  │  Voice Output                                              │ │   │
│  │  │  src/voice/tts_engine.py                                  │ │   │
│  │  │                                                            │ │   │
│  │  │  clean_for_speech() → P14 = Position 14, DRS = D R S      │ │   │
│  │  │  macOS: subprocess.run(["say", "-v", voice, "-r", rate])  │ │   │
│  │  │  Runs as a separate OS process — fully thread-safe.       │ │   │
│  │  │  Blocks until speech completes (intentional).             │ │   │
│  │  └────────────────────────────────────────────────────────────┘ │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  THREAD 2 — Main Loop  (driver interaction)                     │   │
│  │                                                                  │   │
│  │  listen() → Voice Input (Google Speech Recognition)             │   │
│  │      ↓                                                           │   │
│  │  ask_engineer() → AI Engineer (same GPT session + history)      │   │
│  │      ↓                                                           │   │
│  │  speak() → Voice Output                                          │   │
│  │                                                                  │   │
│  │  Shares: TelemetryController, StrategyTracker, history list     │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Command Feedback Path

When the AI confirms a pit stop, a command flows back through the stack to modify the telemetry state for the current and all future snapshots. This is the only path where information flows upward through the layers.

```
  Strategy Engine
       │
       │  trigger: "PIT_NOW" or "SC_OPPORTUNITY"
       ▼
  AI Engineer
       │
       │  response contains "box" confirmation
       ▼
  src/main.py  (speak_proactive / urgency-change handler)
       │
       │  controller.trigger_pit(current_compound)
       ▼
  Telemetry Controller  ──►  PitStateMachine.trigger_pit()
                                      │
                                      │  State: RACING → PIT_ENTRY
                                      │
                         Every subsequent get_snapshot() call:
                                      │
                              PitStateMachine.tick()
                              PitStateMachine.get_overrides()
                                      │
                                      ▼
                         Tyre fields overridden in snapshot
                         until PIT_EXIT → RACING transition
                                      │
                                      ▼
                         on_pit_complete() callback fired
                                      │
                              StrategyTracker.reset_pit()
                              auto_pit_state["triggered"] = False
```

This feedback path is the reason the TelemetryController exists as a separate layer. Without it, there would be no clean interception point between the raw UDP data and the rest of the system.

---

## Thread Model

```
  Main Process
  ├── Thread 1: ProactiveMonitor (daemon=True)
  │     Polls every 1 second:
  │       get_snapshot() → build_race_state() → get_event()
  │       → tracker.evaluate() → [trigger] → ask_engineer() → speak()
  │     Also runs auto-pit check at 50% tyre life threshold.
  │
  └── Thread 2: Main (interactive)
        Waits for driver input each iteration:
          listen() → ask_engineer() → speak()
        Also handles urgency-change detection between polls.

  Shared resources (both threads access):
    ┌──────────────────────────────────────────────────┐
    │  TelemetryController   (Lock-protected)          │
    │  StrategyTracker       (single writer: Thread 1) │
    │  conversation history  (list, sequential writes) │
    │  auto_pit_state        (dict, reset on callback) │
    └──────────────────────────────────────────────────┘

  Guard: if not controller.is_pitting:
    Both tracker.evaluate() and urgency-change speak() are wrapped
    in this guard. No strategy triggers fire while a pit is active.
```

---

## Layer Responsibilities Summary

| Layer | Module | Input | Output | Notes |
|---|---|---|---|---|
| Telemetry Source | `udp_listener.py` | Binary UDP | raw dict | Same interface as `simulator.py` |
| Telemetry Controller | `telemetry_controller.py` | raw dict | raw dict + overrides | Proxy pattern; thread-safe |
| Pit State Machine | `pit_state_machine.py` | trigger command | field overrides | FSM, 4 states |
| Race State Builder | `state_manager.py` | raw dict | race_state | Typed, defaults applied |
| Event Detector | `event_detector.py` | race_state | event dict | Deterministic rules only |
| Strategy Engine | `strategy_tracker.py` | race_state + event | trigger list | Stateful, lap-by-lap |
| AI Engineer | `response_generator.py` | prompt + history | response string | GPT-4o-mini |
| Voice Output | `tts_engine.py` | response string | audio | macOS `say` subprocess |
| Voice Input | `voice_input.py` | microphone | text string | Google Speech Recognition |
| Orchestrator | `main.py` | all layers | — | Coordinates; no business logic |

---

## Key Design Principles

**Rules decide when. AI decides how.**
The event detector and strategy tracker are deterministic rule engines. They never call GPT. GPT is called after a trigger is confirmed, with a tightly scoped prompt that tells it what to communicate — not whether to communicate.

**Each layer has one output contract.**
Every layer produces a single, well-defined output type. No layer reaches past its immediate neighbour. This is what made the Phase 1 → Phase 2 migration a single-line change.

**The proxy pattern absorbs all override complexity.**
The TelemetryController means no other layer ever needs to know about pit stops, compound changes, or post-pit tyre state. They all call `get_snapshot()` and receive a snapshot that reflects current reality, regardless of what the raw UDP stream says.

**`return []`, not `pass`.**
During a safety car period, the strategy tracker uses `return []` after the SC block — not `pass`. This is the mechanism that prevents normal triggers (`PIT_NOW`, `PLAN_CHANGED`) from evaluating on the same lap as an SC call. A `pass` would fall through.
