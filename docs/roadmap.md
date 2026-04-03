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

## Phase 2 — Live PS5 Telemetry 🔜 Next

**Goal:** Replace the simulator with real telemetry from the F1 game on PS5.

**Tasks:**
- Implement `src/telemetry/udp_listener.py`
- Parse Codemasters F1 UDP packet format (PacketLapData, PacketCarStatusData, PacketCarTelemetryData)
- Validate that race_state structure matches simulator output
- Swap `TelemetrySimulator` for `UDPTelemetryListener` in `src/main.py` (one line change)
- Test live during actual gameplay session

**PS5 Setup:**
1. F1 Game → Settings → Telemetry Settings → UDP Telemetry: ON
2. Set broadcast IP to your PC's local IP address
3. Port: 20777

**Reference:** Codemasters F1 2024 UDP Specification

---

## Phase 3 — Full Event Detection

**Goal:** Expand `event_detector.py` into a comprehensive race event system.

**New events to implement:**
- `safety_car_deployed` — detected via session packet flag
- `virtual_safety_car` — lower urgency version
- `drs_enabled` / `drs_disabled` — per-lap zone tracking
- `position_gained` / `position_lost` — compared to previous lap
- `fastest_lap_opportunity` — when within 0.5s of fastest lap on fresh tyres
- `fuel_save_mode` — project fuel consumption to finish line
- `push_mode` — recommend attack when gap to car ahead is closing

**Cooldown system:** Each event type gets a cooldown timer to prevent repeated alerts on the same condition.

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
