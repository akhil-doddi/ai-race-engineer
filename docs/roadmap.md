# Development Roadmap

---

## Phase 1 — Simulation ✅ Complete

**Goal:** Build a fully working AI race engineer using simulated telemetry.

**Delivered:**
- Realistic telemetry simulator (tyre degradation, fuel burn, lap time physics)
- GPT-4o-mini powered race engineer with conversation memory
- Proactive pit window alerts (green / yellow / red urgency system)
- Voice output (TTS) and voice input (speech recognition)
- Text mode fallback for testing without microphone
- Modular, layered architecture ready for Phase 2 integration

**Run:** `python3 -m src.main`

---

## Phase 2 — Live PS5 Telemetry ✅ Complete

**Goal:** Replace the simulator with real telemetry from the F1 game on PS5.

**Delivered:**
- `src/telemetry/udp_listener.py` — fully implemented, parses Codemasters F1 24 binary UDP packets
- `src/telemetry/udp_sender.py` — race simulation sender for testing without a PS5
- `src/telemetry/pit_state_machine.py` — FSM (RACING → PIT_ENTRY → PIT_STOP → PIT_EXIT → RACING)
- `src/telemetry/telemetry_controller.py` — wrapper that applies pit overrides transparently
- `src/strategy/strategy_tracker.py` — proactive trigger engine (INITIAL_BRIEF, PLAN_CHANGED, PIT_APPROACHING, PIT_NOW, SC_OPPORTUNITY)
- Safety car detection via weather byte in session packet; single prompt per SC period
- Post-pit persistent tyre overrides — AI sees fresh rubber data after every stop
- Auto-pit trigger at 50% tyre life; resets correctly between stints
- macOS TTS fixed — replaced pyttsx3 (thread-unsafe) with `say` subprocess
- Two-thread architecture — proactive monitor (1s poll) + reactive main thread
- Strategy tracker guards — no duplicate BOX calls, no triggers while pitting

**PS5 Setup:**
1. F1 Game → Settings → Telemetry Settings → UDP Telemetry: ON
2. Set broadcast IP to your PC's local IP address
3. Port: 20777

**Reference:** Codemasters F1 2024 UDP Specification

---

## Phase 3 — Full Event Detection 🔄 In Progress

**Goal:** Expand `event_detector.py` into a comprehensive race event system.

**Delivered so far:**
- ✅ **#1 — Endgame race strategy** (v0.3.1) — final 10 laps suppress pit calls; `ENDGAME_MANAGE` trigger
- ✅ **#2 — FINISH_RACE trigger** (v0.3.2) — cancels planned pit windows that arrive in endgame phase
- ✅ **#3 — Position, DRS, Fuel triggers** (v0.3.0) — `POSITION_GAINED`, `POSITION_LOST`, `DRS_ENABLED`, `FUEL_SAVE`
- ✅ **#4 — Gap alert cooldown** (v0.3.3) — 3-lap cooldown on gap alerts; cooldown gates the speak decision, not urgency calculation
- ✅ **#5 — VSC + Real PS5 SC detection** (v0.3.4):
  - `safetyCarStatus` read from correct byte offset in PacketSessionData (not weather byte hack)
  - VSC as distinct state with conditional pit logic (only pit if near window OR tyre < 35%)
  - `VSC_OPPORTUNITY` trigger — always advisory, never auto-pits
  - `SC_OPPORTUNITY` — full pit call, auto-pits the car
  - `udp_sender.py` test windows: VSC laps 3–9, full SC laps 33–43, randomised each run

**Remaining:**
- ✅ **#6 — Push mode** (v0.3.5) — rolling 3-lap gap_ahead buffer in strategy_tracker; fires `PUSH_MODE` when gap closes consistently (all 3 readings decreasing, total >= 0.3s). SC buffer flush prevents false alerts on restarts. Once per stint.
- ✅ **FastF1 historical replay** (v0.3.6) — `src/telemetry/fastf1_replay.py`; any completed race from ~2018, any driver on the grid. Same `get_snapshot()` interface as UDP. Provides `session_fastest_lap` (rolling best from all 20 drivers) and `pit_this_lap` (TyreLife-drop pit detection) — data foundation for #7.
- 🔜 **#7 — Fastest lap opportunity** — detect when driver can set fastest lap (fresh tyres, within range of current FL, last stint). `session_fastest_lap` already flowing through state_manager from FastF1 replay.

---

## Phase 4 — Adaptive Communication Modes

**Goal:** Engineer's tone and language adapt to race urgency.

**Modes:**
- `ANALYTICAL` — low urgency, calm multi-sentence explanation with reasoning
- `ALERT` — medium urgency, concise 1-2 sentence warning
- `COMMAND` — high urgency, short decisive instruction ("Box box box. Now.")

**Implementation:**
- Communication mode determined by event detector
- Mode passed as context in every GPT system prompt
- Prompt templates tuned per mode for consistent tone

---

## Phase 5 — Persistent Race Memory

**Goal:** Engineer remembers the full race history, not just recent exchanges.

**Memory structure to build:**
```python
race_memory = {
    "pit_stops": [...],           # Each pit stop: lap, compound, reason
    "strategy_changes": [...],    # When and why strategy was adjusted
    "warnings_issued": [...],     # Events that triggered alerts
    "tyre_sets_used": [...],      # Compound history across the race
    "position_history": [...],    # Position at each lap
}
```

**Engineer behaviour:** "We extended that stint by 4 laps last time — expect degradation to hit earlier this set."

---

## Phase 6 — Voice Optimisation During Gameplay

**Goal:** Full voice interaction while actively racing on PS5.

**Tasks:**
- Push-to-talk via keyboard shortcut (so mic only opens on button press)
- Suppress mic input during engineer speech (prevents feedback loop)
- Evaluate cloud TTS (ElevenLabs / Azure) for higher voice quality
- Test at racing speed — response latency must stay under 1.5 seconds

---

## Phase 7 — Dockerization

**Goal:** Package the entire system as a Docker container.

**Tasks:**
- Write `Dockerfile` with Python 3.11 base
- Environment variable injection via `.env` or Docker secrets
- Document UDP port forwarding for container networking

---

## Phase 8 — CI/CD and Cloud Deployment

**Goal:** Automated testing and deployment pipeline.

**Tasks:**
- GitHub Actions workflow for automated tests on every push
- Linting (flake8 / ruff) enforced in CI
- Test coverage reporting
- Optional: deploy to cloud VM for remote access during LAN play
