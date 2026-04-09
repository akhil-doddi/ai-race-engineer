# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [0.3.6] — 2026-04-09 — FastF1 Historical Race Replay Telemetry Source

### Added
- `src/telemetry/fastf1_replay.py` — new telemetry source that replays any completed F1 race
  from the FastF1 historical dataset (~2018 onwards). Provides the same `start()` / `stop()` /
  `get_snapshot()` interface as `udp_listener.py` and `simulator.py` — zero changes required
  in any layer above it.
- `FastF1Replay.load_session(year, event, driver_abbr, lap_interval)` — downloads and caches
  a race session via FastF1, builds a lap-by-lap snapshot list for the chosen driver. Caches
  locally at `.fastf1_cache/` to avoid repeated downloads.
- `list_drivers(year, event)` — returns all 20 grid drivers with name and team for selection
  UI in `main.py`.
- Gap computation from FastF1 cumulative `Time` column — delta between adjacent-position
  drivers on the same lap, accurate to ~0.5s. Sufficient for strategy decisions.
- `_compute_running_fastest_lap()` — rolling best lap time from all 20 drivers up to the
  current lap. Stored as `session_fastest_lap` in each snapshot. Foundation for Phase 3 #7.
- `_estimate_tire_wear()` — two-phase cliff model (linear decay + acceleration after 70% of
  max stint) matching `event_detector.py`'s wear model for consistency.
- `pit_this_lap` flag — detects when FastF1 `TyreLife` resets (pit stop occurred). Set
  `True` for one lap only; tells `main.py` to reset the strategy tracker without running
  the `PitStateMachine` animation (historical pits don't need the interactive animation).
- `src/race_state/state_manager.py`: `pit_this_lap` and `session_fastest_lap` pass-through
  fields — both default to `False` / `None` when using simulator or UDP, so no breaking
  changes to existing telemetry paths.
- `src/main.py`: `'f'` option in telemetry source prompt — triggers `_setup_fastf1_replay()`
  interactive setup (year → event → driver list → driver abbreviation → replay speed).
- `src/main.py`: FastF1 pit detection handler in `proactive_monitor` — calls
  `tracker.reset_pit()` and sets `auto_pit_state["triggered"] = True` when `pit_this_lap`
  is `True`, suppressing the 50% auto-pit and updating stint tracking for the new set.
- `requirements.txt`: `fastf1>=3.3.0` dependency added.
- `.gitignore`: `.fastf1_cache/` excluded — cache can reach 100MB+ per season, regenerated
  on demand.

### Notes
- FastF1 must be installed manually on first use: `pip install fastf1`
- Sessions are cached after the first download; subsequent loads are instant
- `session_fastest_lap` flowing through state_manager is the data foundation for
  Phase 3 #7 (fastest lap opportunity trigger) — that trigger is built next

---

## [0.3.5] — 2026-04-08 — Phase 3 #6: Push Mode — Gap Closing Rate Detection

### Added
- `src/strategy/strategy_tracker.py`: `PUSH_MODE` trigger — fires once per stint when `gap_ahead`
  has been shrinking consistently over the last 3 laps. Requires all 3 readings to be decreasing
  AND total closure >= 0.3s (filters micro-oscillations).
- `src/strategy/strategy_tracker.py`: Rolling `_gap_buffer` (last 3 gap_ahead values), with
  `_gap_buffer_sc_tainted` flag. Buffer is flushed on SC→green transition to prevent false push
  calls from artificial gap compression during safety car periods.
- `src/strategy/strategy_tracker.py`: `_push_mode_called` flag — fires once per stint.
  Resets in `reset_pit()` so a fresh stint on new tyres gets its own push opportunity.
- `src/strategy/strategy_tracker.py`: `PUSH_MODE` build_prompt — includes gap 3 laps ago,
  current gap, and per-lap closing rate. Energetic, motivating radio style.
- `src/main.py`: `"PUSH_MODE": "🏎️  PUSH — CLOSING ON CAR AHEAD"` label in `speak_proactive()`.

### Changed
- `src/strategy/strategy_tracker.py`: Trigger docstring updated to list all 13 triggers
  including `PUSH_MODE` and `VSC_OPPORTUNITY`.

---

## [0.3.4] — 2026-04-07 — Phase 3 #5: VSC/SC Behavior Differentiation

### Added
- `src/telemetry/udp_sender.py`: Separate deployment windows for VSC and full SC.
  VSC deploys randomly in laps 3–9 (tyres fresh, no pit warranted).
  Full SC deploys randomly in laps 33–43 (tyres old, free pit is correct strategy).
  Each type picks its own random lap within its window on every run so no two sessions are identical.
- `src/events/event_detector.py`: VSC conditional pit logic.
  Full SC always sets `should_pit = True` (field compressed, pit cost neutralised).
  VSC sets `should_pit = True` only if `tire_age >= expected_stint - 2` OR `tire_life < 35%`.
  Below both thresholds VSC sets `should_pit = False` — stay out, hold delta.
- `src/strategy/strategy_tracker.py`: `VSC_OPPORTUNITY` trigger — fires once per VSC period
  regardless of `should_pit`. Always delivers a radio advisory. `build_prompt` branches on
  `should_pit` to produce either a "box this lap" or "maintain delta, stay out" brief.
- `src/strategy/strategy_tracker.py`: `_vsc_called` flag — prevents `VSC_OPPORTUNITY` from
  repeating during a single VSC period. Resets to False when track returns to green (same
  lifecycle as `_sc_pit_called`).
- `src/main.py`: `"VSC_OPPORTUNITY": "🟡 VIRTUAL SAFETY CAR — ADVISORY"` label in `speak_proactive()`.
- `src/main.py`: `is_vsc` guard in the urgency-change handler — blocks auto-pit via the
  urgency path when `track_status == "virtual_safety_car"`, even if `should_pit = True`.
  VSC decisions are advisory; auto-pit only fires for full SC and tyre-critical alerts.

### Changed
- `src/strategy/strategy_tracker.py`: SC block in `evaluate()` now branches on `track_status_now`:
  VSC → fires `VSC_OPPORTUNITY` then returns `[]`.
  Full SC → fires `SC_OPPORTUNITY` if conditions met then returns `[]`.
  Structural principle: shared detection infrastructure, branched decision.
- `src/strategy/strategy_tracker.py`: `SC_OPPORTUNITY` prompt simplified — VSC-specific
  wording removed now that `VSC_OPPORTUNITY` handles it separately.
- `src/main.py`: `speak_proactive()` pit trigger guard remains `("PIT_NOW", "SC_OPPORTUNITY")` —
  `VSC_OPPORTUNITY` is intentionally excluded. VSC advisory never auto-pits the car.

---

## [0.3.3] — 2026-04-07 — Phase 3 #4: Per-Event Gap Alert Cooldown

### Added
- `src/events/event_detector.py`: Cooldown constants `COOLDOWN_GAP_ALERT = 3` and
  `COOLDOWN_PIT_WINDOW = 3`. Module-level `_cooldowns` dict with helpers
  `_on_cooldown(key, lap, n)` and `_start_cooldown(key, lap)`.
- `src/events/event_detector.py`: `reset_cooldowns()` — clears all cooldown state.
  Called at race start in `main.py` so prior session state does not carry over.
- `src/main.py`: Gap alert cooldown applied at the SPEAK decision point in the urgency-change
  handler. Detects gap alerts by checking the reason string for `"attack window"` or
  `"car behind closing"`. Suppresses the speak call if the same alert fired within the
  last 3 laps, but leaves `last_urgency` tracking the true urgency throughout.

### Fixed
- **Critical bug:** Initial cooldown implementation placed inside `get_event()`, returning
  `urgency = "green"` during cooldown periods. This reset `last_urgency` to green on every
  suppressed poll, creating a new green→yellow transition when the cooldown expired — making
  gap alerts fire MORE frequently, not less. Fix: moved cooldown to the SPEAK decision in
  `proactive_monitor`; `last_urgency` is now updated before the speak block so it tracks
  true urgency throughout cooldown windows.

---

## [0.3.2] — 2026-04-07 — FINISH_RACE: Suppress Planned Pit Windows in Endgame

### Added
- `strategy_tracker.py`: `FINISH_RACE` trigger — fires when a pre-planned pit stop window
  arrives during endgame phase (`race_phase == "endgame"`). Delivers a "pit stop not required,
  maintain position, bring it home" message instead of a pit warning.
- `strategy_tracker.py`: `_finish_race_called` flag — prevents `FINISH_RACE` from repeating
  per stint. Resets in `reset_pit()` so a new stint that also ends in endgame is handled.
- `strategy_tracker.py`: Endgame guards on `PIT_APPROACHING`, `PIT_NOW`, and `PLAN_CHANGED` —
  all three now include `event.get("race_phase", "mid") != "endgame"` conditions.
  `FINISH_RACE` takes over the communication slot; the pit triggers must be silent.
- `main.py`: `"FINISH_RACE": "🏁 FINISH — NO MORE STOPS"` label entry in `speak_proactive()`.

### Fixed
- **Bug:** `PIT_APPROACHING` fired with 9 laps remaining, producing "Pit stop in 3 laps; we'll
  box on Lap 52" despite the race being almost over. Root cause: `PIT_APPROACHING` evaluated
  purely on lap arithmetic (`current_lap == planned_pit_lap - 3`) with no race phase awareness.
  Because tyre wear was reported as fuel-critical (not a `should_pit` path), `endgame_override`
  was never set, so `ENDGAME_MANAGE` never fired as a replacement. The fix adds explicit
  endgame guards to `PIT_APPROACHING`, `PIT_NOW`, and `PLAN_CHANGED`, and introduces
  `FINISH_RACE` as the dedicated trigger for this scenario.

---

## [0.3.1] — 2026-04-07 — Endgame Race Strategy Logic

### Added
- `event_detector.py`: `_get_race_phase()` — classifies race into `"early"`, `"mid"`, or `"endgame"`
  based on laps remaining. Endgame activates at `ENDGAME_LAP_THRESHOLD = 10` laps remaining.
- `event_detector.py`: Endgame override block — when in endgame phase and tyre life is above
  `ENDGAME_CRITICAL_TYRE = 15%`, all pit recommendations are suppressed and replaced with tyre
  management guidance. SC pits are excluded (free stop remains valid). Tyre below 15% is excluded
  (safety concern outweighs track position).
- `event_detector.py`: Two new keys in the returned event dict: `race_phase` and `endgame_override`.
  `endgame_override = True` signals downstream layers that a pit was suppressed by race phase.
- `strategy_tracker.py`: `ENDGAME_MANAGE` trigger — fires once per stint when `endgame_override = True`
  and tyre life is below 40%. Produces survival-mode radio brief instead of pit call.
- `strategy_tracker.py`: `_endgame_manage_called` flag prevents ENDGAME_MANAGE from repeating.
  Resets in `reset_pit()` so a second stint ending in endgame is handled correctly.
- `strategy_tracker.py`: `laps_rem` variable in `build_prompt()` for use in ENDGAME_MANAGE prompt.
- `main.py`: `ENDGAME_MANAGE` entry in label dict (`🏁 ENDGAME — TYRE MANAGEMENT`).

### Changed
- `event_detector.py`: `get_event()` now returns `race_phase` and `endgame_override` keys.
- `main.py`: Auto-pit threshold guard changed from `laps_remaining > 3` to
  `laps_remaining > ENDGAME_LAP_THRESHOLD`. Prevents 50%-tyre auto-pit in the final 10 laps.
- `main.py`: Urgency-change box handler now checks `not event.get("endgame_override", False)`
  before calling `trigger_pit()`. Endgame tyre alerts speak without boxing the car.
- `main.py`: Imports `ENDGAME_LAP_THRESHOLD` from `event_detector` (single source of truth).

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
