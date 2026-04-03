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
