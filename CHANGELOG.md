# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

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
