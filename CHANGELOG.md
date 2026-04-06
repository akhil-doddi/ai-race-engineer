# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [0.3.0] — 2026-04-06 — Phase 2 Complete: Live UDP Telemetry

### Added
- `src/telemetry/udp_listener.py` — live Codemasters F1 24 UDP packet parser on port 20777.
  Parses PacketLapData, PacketCarStatusData, PacketCarTelemetryData, PacketSessionData.
  Decodes tyre compound, wear, fuel, gaps, position, and track status (safety car via weather byte).
- `src/telemetry/udp_sender.py` — standalone race simulation sender for testing without a PS5.
  Full 53-lap race simulation with cliff tyre model, safety car periods, realistic fuel burn.
- `src/telemetry/pit_state_machine.py` — finite state machine for interactive pit stop simulation.
  States: RACING → PIT_ENTRY (2s) → PIT_STOP (5s) → PIT_EXIT (2s) → RACING.
  Applies compound rotation (Soft→Medium, Medium→Hard, Hard→Medium) and fresh tyre overrides.
- `src/telemetry/telemetry_controller.py` — transparent wrapper over any telemetry source.
  Applies pit stop overrides during active pit phases; maintains post-pit persistent tyre
  overrides so the AI sees fresh rubber data after every stop, not stale UDP data.
- `src/strategy/strategy_tracker.py` — proactive pit trigger engine.
  Triggers: INITIAL_BRIEF, PLAN_CHANGED, PIT_APPROACHING, PIT_NOW, SC_OPPORTUNITY.
  Safety car logic: one prompt per SC period, all normal pit triggers blocked during SC.
  Guards: `_pit_called`, `_approaching_called`, `_sc_pit_called` flags prevent duplicate calls.

### Changed
- `src/main.py` — full rewrite as dual-thread orchestrator.
  Thread A: proactive monitor polling at 1.0s intervals.
  Thread B: reactive driver input loop (main thread).
  `TelemetryController` wraps raw source — one-line swap between simulator and UDP.
  Auto-pit triggers at 50% tyre life; resets correctly between stints via shared `_auto_pit_state` dict.
  Urgency-change and strategy tracker evaluation both guarded by `is_pitting` check — no duplicate
  BOX calls while car is in the pit lane.
- `src/voice/tts_engine.py` — replaced pyttsx3 with macOS `say` subprocess.
  pyttsx3 raises `RuntimeError: run loop already started` when called from a non-main thread on macOS.
  `say` is a separate process, fully thread-safe, and supports the same macOS voice names.
- `src/events/event_detector.py` — safety car pit detection, SC_MIN_TYRE_AGE constant (5 laps).
  Removed all undercut-based pit recommendations.
- `docs/roadmap.md` — Phase 2 marked complete, Phase 3 marked as next.
- `docs/decisions.md` — added ADR-008 through ADR-012 for Phase 2 architectural decisions.

### Removed
- `UNDERCUT_URGENT` trigger — car-behind-based BOX calls removed per driver preference.
- `UNDERCUT_OPPORTUNITY` trigger — gap-ahead-based offensive BOX calls removed.

---

## [0.2.0] — 2026-04-03 — Repository Professionalisation

### Added
- Full layered architecture: `src/telemetry/`, `src/race_state/`, `src/events/`, `src/strategy/`, `src/communication/`, `src/voice/`
- `config/settings.py` — centralised configuration with documented constants
- `src/race_state/state_manager.py` — clean race_state abstraction layer (Phase 2 integration boundary)
- `src/telemetry/udp_listener.py` — Phase 2 placeholder with full documentation and commented implementation scaffold
- `docs/architecture.md` — data flow diagram and layer responsibilities
- `docs/decisions.md` — architecture decision records explaining WHY
- `docs/roadmap.md` — 8-phase development plan
- `docs/ai_context.md` — context file for AI assistants
- `CHANGELOG.md` — this file
- Docstrings and inline WHY comments across all source files

### Changed
- Renamed `app/` → `src/` with subdirectories per architectural layer
- Renamed `app/config.py` → `config/settings.py`
- Renamed `app/telemetry.py` → `src/telemetry/simulator.py` (class: `TelemetrySimulator`)
- Renamed `app/pit_strategy.py` → `src/events/event_detector.py` (functions: `get_event`, `format_alert`)
- Renamed `app/response_generator.py` → `src/communication/response_generator.py` (function: `ask_engineer`)
- Renamed `app/tts_engine.py` → `src/voice/tts_engine.py`
- Renamed `app/voice_input.py` → `src/voice/voice_input.py` (function: `listen`)
- Updated all import paths to match new structure
- `requirements.txt` — pinned package versions, added comments

### Removed
- `backup/` folder — prototype files removed from version control
- `.DS_Store` files — excluded from repository

---

## [0.1.0] — 2026-04-03 — Phase 1 Complete

### Added
- Working AI race engineer with simulated telemetry
- GPT-4o-mini integration with conversation memory (rolling 20-message window)
- Realistic telemetry simulator: tyre wear, fuel burn, lap time physics
- Proactive pit window alerts (green / yellow / red urgency)
- Voice output via macOS pyttsx3 with speech cleaning (P14 → Position 14)
- Voice input via Google Speech Recognition
- Text mode fallback for testing without microphone
- Undercut threat detection
- Fuel and tyre critical warnings with cooldown protection
