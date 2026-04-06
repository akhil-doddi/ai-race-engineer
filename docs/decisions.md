# Architecture Decision Records

This document explains WHY key design decisions were made.
Understanding the reasoning helps future contributors maintain the system's integrity.

---

## ADR-001: Event-Driven AI Triggering

**Decision:** The AI is only called when the event detector flags a change, or when the driver explicitly asks a question.

**Why:** Calling GPT on every telemetry update (60Hz in Phase 2) would cost hundreds of dollars per race and introduce 500ms+ latency on every frame. An F1 engineer does not speak every second — they speak when something matters. The event layer is the gatekeeper.

**Alternative considered:** Streaming GPT responses continuously. Rejected due to latency, cost, and the fact that constant talking destroys the realism of the experience.

---

## ADR-002: Hybrid Rule + AI Model

**Decision:** Deterministic rules detect events. AI generates the language to communicate them.

**Why:** Rules are fast (microseconds), free, and reliable. AI is slow (500ms+), costly, and occasionally wrong. By separating the two, we get the best of both: precise, trustworthy event detection with natural, intelligent communication.

**Example:** The event detector calculates that tyre life is critical using a mathematical formula. It does not ask GPT whether the tyres are critical. GPT is only asked to *say* that the tyres are critical in a natural, context-aware way.

---

## ADR-003: Race State Abstraction Layer

**Decision:** A dedicated `state_manager.py` sits between telemetry and all other layers. No layer above it ever touches raw telemetry directly.

**Why:** In Phase 1, telemetry comes from a Python simulator. In Phase 2, it comes from binary UDP packets from the F1 game. The structure of these two sources is different. The state manager normalises both into the same clean dictionary. This means the Phase 1 → Phase 2 migration changes exactly one file (the telemetry source) and nothing else.

---

## ADR-004: PS5 UDP Telemetry Integration

**Decision:** The F1 game's built-in UDP telemetry broadcast is the chosen data source for Phase 2, rather than screen capture or third-party APIs.

**Why:** The official F1 game (Codemasters/EA) broadcasts structured telemetry packets at up to 60Hz via UDP on port 20777. This is the same data source used by professional F1 teams and sim racing teams globally. It is well-documented, low-latency, and requires no additional hardware — just a network configuration change in the game settings.

**How it works:** PS5 broadcasts UDP packets to the local network. The PC listens on the same network, receives packets, and parses them. No cable required — WiFi on the same router is sufficient.

---

## ADR-005: Persistent Conversation Memory

**Decision:** Every exchange (driver question + engineer reply) is stored in a rolling history list and passed to GPT on every subsequent call.

**Why:** Without memory, the engineer cannot say "as I mentioned earlier" or reference a pit stop from three laps ago. This would make the AI feel like a chatbot rather than a race engineer who has been with you the whole race.

**Cost control:** History is capped at 20 messages (10 exchanges) to prevent unbounded context growth. Older context is dropped — recent race events are what matter most.

---

## ADR-006: Communication Modes

**Decision:** The engineer's tone adapts to urgency level. (Partially implemented in Phase 1 via system prompt; fully formalised in Phase 4.)

**Why:** A real F1 engineer does not speak the same way when discussing lap delta and when screaming "box box box." Calm analysis requires different language to an urgent command. The communication mode (analytical / alert / command) is determined before the AI is called, and included in the prompt so GPT matches the appropriate register.

---

## ADR-007: Voice Selection

**Decision:** macOS pyttsx3 with a British English male voice for Phase 1. ElevenLabs or Azure TTS considered for Phase 6.

**Why:** pyttsx3 is zero-latency (offline, no API call) and free. For Phase 1, this is the right tradeoff. In Phase 6, a cloud TTS provider would give much higher voice quality at the cost of ~200ms additional latency per response, which may be acceptable at that stage.

---

## ADR-008: TelemetryController Wrapper Pattern

**Decision:** A `TelemetryController` sits between the raw telemetry source and all higher layers. It wraps any source implementing `start()/stop()/get_snapshot()` and applies overrides on top of the raw data without modifying the source.

**Why:** The raw telemetry source (UDP listener or simulator) is a read-only stream — it has no knowledge of driver decisions, pit calls, or tyre changes made outside the game. Rather than modify every consumer of telemetry data when overrides are needed, a single wrapper intercepts `get_snapshot()` and layers changes on top. The rest of the system cannot tell the difference. This pattern also makes the source swappable with one line change in `main.py`.

**Alternative considered:** Injecting overrides directly into the UDP listener or simulator. Rejected because it would tightly couple the telemetry source to strategy decisions — the wrong architectural boundary.

---

## ADR-009: PitStateMachine FSM for Pit Simulation

**Decision:** Pit stop simulation is implemented as an explicit Finite State Machine with four states: `RACING → PIT_ENTRY → PIT_STOP → PIT_EXIT → RACING`.

**Why:** A pit stop is not a toggle — it is a timed sequence with ordered phases. An if/else chain breaks down when phases need distinct durations, distinct telemetry overrides, and clear transition rules. The FSM makes each phase's behaviour and its exit condition explicit. It also prevents invalid transitions (e.g. triggering a pit while already in PIT_STOP returns False immediately).

**Alternative considered:** A simple boolean flag `is_pitting`. Rejected because it cannot represent which phase the car is in, making it impossible to apply the right overrides per phase (e.g. showing old tyres during PIT_ENTRY vs. fresh tyres during PIT_STOP).

---

## ADR-010: pyttsx3 Replaced by macOS `say` Subprocess

**Decision:** Phase 2 replaces `pyttsx3` with `subprocess.run(["say", ...])` on macOS.

**Why:** `pyttsx3.runAndWait()` raises `RuntimeError: run loop already started` when called from any thread other than the main thread on macOS, due to a conflict with the Core Audio event loop. Phase 2 introduces a dual-thread architecture (proactive monitor + reactive main thread), so `pyttsx3` would crash on every proactive voice call. The macOS `say` command runs as a fully separate operating system process — it is thread-safe by definition. It supports the same voice names, blocks until speech completes, and requires no third-party library.

**Alternative considered:** Running all TTS calls on the main thread via a queue. Rejected because it adds coordination complexity and adds latency to proactive calls (the proactive thread would have to wait for the main thread to be idle before speaking).

---

## ADR-011: Safety Car State via Weather Byte in Session Packet

**Decision:** Safety car detection reads the `weather` field of the Codemasters F1 24 `PacketSessionData` UDP packet. A value of `1` indicates safety car; `0` indicates green flag conditions.

**Why:** The F1 24 UDP specification does not expose a dedicated `safety_car_deployed` flag. The weather byte in the session packet carries this state in practice — a value of 1 is consistently set when the safety car is deployed. This was verified against the Codemasters F1 2024 UDP documentation and confirmed to match the `udp_sender.py` simulation output. The field is already parsed in `udp_listener.py`, making this the lowest-cost integration point.

**Alternative considered:** Detecting SC indirectly via speed anomalies (all cars slowing simultaneously). Rejected as brittle — it would produce false positives and require cross-car telemetry that is difficult to parse reliably.

---

## ADR-012: StrategyTracker Proactive Trigger System

**Decision:** A dedicated `StrategyTracker` class evaluates telemetry on every lap and returns a list of trigger names when the engineer should speak without being asked. Triggers are: `INITIAL_BRIEF`, `PLAN_CHANGED`, `PIT_APPROACHING`, `PIT_NOW`, `SC_OPPORTUNITY`.

**Why:** A chatbot only speaks when asked. A real race engineer speaks when the situation demands it. Without this layer, the driver would need to ask "should I pit?" at exactly the right moment. The StrategyTracker removes that burden by watching every lap and calling proactively. State flags (`_pit_called`, `_approaching_called`, `_sc_pit_called`) prevent duplicate calls. A `last_spoken_lap` guard ensures at most one proactive message per lap. During a safety car, `return []` (not `pass`) immediately exits the evaluation, blocking all normal triggers — the SC window is time-critical and must not be diluted by lower-priority calls firing on the same lap.
