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

**Decision:** A dedicated `StrategyTracker` class evaluates telemetry on every lap and returns a list of trigger names when the engineer should speak without being asked. Triggers are: `INITIAL_BRIEF`, `PLAN_CHANGED`, `PIT_APPROACHING`, `PIT_NOW`, `SC_OPPORTUNITY`, `ENDGAME_MANAGE`.

**Why:** A chatbot only speaks when asked. A real race engineer speaks when the situation demands it. Without this layer, the driver would need to ask "should I pit?" at exactly the right moment. The StrategyTracker removes that burden by watching every lap and calling proactively. State flags (`_pit_called`, `_approaching_called`, `_sc_pit_called`, `_endgame_manage_called`) prevent duplicate calls. A `last_spoken_lap` guard ensures at most one proactive message per lap. During a safety car, `return []` (not `pass`) immediately exits the evaluation, blocking all normal triggers — the SC window is time-critical and must not be diluted by lower-priority calls firing on the same lap.

---

## ADR-013: Endgame Race Phase Logic

**Decision:** In the final `ENDGAME_LAP_THRESHOLD = 10` laps, all pit recommendations are suppressed by a phase-aware override in `event_detector.get_event()`, unless the tyre is critically worn (< 15% life) or a safety car is deployed. The override sets a new `endgame_override` flag in the event dict. The strategy tracker fires a new `ENDGAME_MANAGE` trigger in response, producing a survival-mode radio brief instead of a box call. Two autonomous pit paths in `main.py` (auto-pit at 50%, urgency-change handler) also respect this flag.

**Why the override lives in `event_detector`, not `strategy_tracker`:** The event detector is the single authoritative source for `should_pit`. If the override lived in the strategy tracker, the urgency-change handler and auto-pit path in `main.py` would each need their own independent endgame checks — three scattered places instead of one. By setting `endgame_override = True` in the event dict, every downstream consumer gets a single flag to check.

**Why safety car is excluded:** An SC window neutralises the 22-second pit stop time loss. A safety car pit is strategically valid even at 9 laps remaining. Suppressing it would actively harm the driver's race result.

**Why tyre < 15% is excluded:** Below this threshold the car is at physical risk (tyre blow-out, loss of control). Track position cannot outweigh safety.

**Why `ENDGAME_MANAGE` fires only when tyre life < 40%:** The trigger is informative — it tells the driver we are staying out intentionally on worn tyres. If tyre life is 80% with 10 laps left there is nothing to communicate; the car is fine. The 40% threshold ensures the message only fires when the driver would otherwise expect a pit call.

**Alternative considered:** A dedicated `RacePhaseManager` class that wraps the event dict and applies overrides. Rejected as over-engineering — the override is three lines in `get_event()` and the event dict already carries the result to all consumers.

---

## ADR-014: Cooldown Applied at Speak Decision, Not at Event Detection

**Decision:** Gap alert cooldowns are enforced in `proactive_monitor` inside `main.py`, at the point where the engineer decides to speak — not inside `get_event()` in `event_detector.py`.

**Why:** The proactive monitor uses urgency-change detection: it only speaks when `event["urgency"] != last_urgency`. If the cooldown were inside `get_event()`, it would return `urgency = "green"` during suppressed laps. This resets `last_urgency` to green on the next poll. When the cooldown expires, urgency transitions green→yellow again — creating a new trigger. The net result is alerts firing *more* frequently (every cooldown period), not less. By keeping the cooldown at the speak decision only, `last_urgency` correctly tracks the true urgency throughout the entire gap window. No new transition fires until the gap genuinely clears past the threshold.

**The invariant this creates:** `last_urgency` always reflects the true current urgency. The cooldown only suppresses the speak call — it never suppresses the urgency reading.

**Alternative considered:** Returning a suppressed urgency from `get_event()` and using a separate `raw_urgency` field for tracking. Rejected because it required every consumer to understand two urgency fields, and still required the same "update last_urgency before the speak block" discipline.

---

## ADR-015: VSC and Full SC — Shared Detection, Branched Decision

**Decision:** VSC and full SC share a single detection path (`track_status in ("safety_car", "virtual_safety_car")`) and the same entry point in `strategy_tracker.evaluate()`. The branch happens at the strategic decision point — which trigger to fire and what `should_pit` means — not at detection.

**Why the split is here and not earlier:** Guards like "are we in endgame?", "have we already called a pit this period?", "is tyre age above SC_MIN_TYRE_AGE?" apply equally to both SC and VSC. Duplicating them across separate code paths would mean maintaining two copies of the same logic. By entering the same block, both types benefit from the same guards automatically.

**VSC pit conditions:** VSC reduces but does not eliminate pit stop time loss. The field does not compress behind a VSC, so a pit stop still costs track position. The rule: recommend a pit only if `tire_age >= expected_stint - 2` (close to the natural stop anyway) OR `tire_life < 35%` (tyre in danger zone). Below both thresholds the time loss outweighs the benefit — stay out.

**Full SC pit conditions:** The field compresses fully behind a safety car. Pit stop time loss is neutralised. Always recommend a pit if minimum tyre age is met and laps remain.

**VSC_OPPORTUNITY never auto-pits:** Full SC triggers `controller.trigger_pit()` automatically via `speak_proactive()`. VSC does not. VSC delivers a verbal advisory; the driver decides. This prevents the system from automatically pitting on a VSC where staying out is the correct call.

**Why ADR-011 is now superseded:** ADR-011 used the weather byte to detect SC status. This was a PS5-incompatible hack — the real PS5 game repurposes the weather byte for weather, not SC state. Phase 3 #5 fixed this by reading `safetyCarStatus` from its correct position in `PacketSessionData` (offset 124 bytes after the packet header, absolute byte 153 from packet start). Values: 0=green, 1=full SC, 2=VSC, 3=formation lap.

---

## ADR-016: Push Mode — 3-Lap Rolling Gap Buffer in StrategyTracker

**Decision:** Push mode detection lives in `strategy_tracker.py` as a rolling 3-lap buffer of `gap_ahead` values, not in `event_detector.py`. The trigger fires when all 3 readings are monotonically decreasing and the total closure is >= 0.3 seconds.

**Why strategy_tracker and not event_detector:** The event detector is stateless within a single call — it evaluates one snapshot and returns a verdict. Push mode requires cross-lap state (a buffer that accumulates over multiple calls). StrategyTracker already holds cross-lap state for the pit plan, position tracking, and DRS detection. Adding the gap buffer here follows the established pattern.

**Why 3 laps, not 2 or 5:** 2 laps is too short — a single DRS pass or traffic effect can produce two shrinking readings that don't represent genuine pace advantage. 5 laps is too slow — by the time the engineer calls push mode after 5 laps of closing, the driver may have already passed or the gap may have stabilised. 3 laps is the Goldilocks threshold: filters single-lap noise while remaining responsive.

**Why 0.3s minimum closure:** Without a floor, micro-oscillations (1.52 → 1.51 → 1.50) satisfy the "all decreasing" check but don't represent meaningful pace advantage. 0.3s over 3 laps (~0.1s/lap closing rate) is the minimum that represents actionable information — the driver would reach DRS range within a few more laps at that rate.

**SC buffer flush:** Safety car compresses gaps artificially. When track goes green, gaps jump unpredictably as cars warm tyres and find their pace. Any gap readings from SC laps would corrupt the closing-rate calculation. The `_gap_buffer_sc_tainted` flag marks the buffer as dirty under SC; when green resumes, the entire buffer is cleared so the first 3 green-flag laps build a clean picture.

**Alternative considered:** Storing the gap delta (change per lap) instead of raw gap values. Rejected because raw values are simpler to reason about and allow the build_prompt to show "gap 3 laps ago was X, now it's Y" — which is more useful to the driver than an abstract delta number.

---

## ADR-017: FastF1 as a Third Telemetry Source — Same Interface, Different Origin

**Decision:** `FastF1Replay` in `src/telemetry/fastf1_replay.py` implements the same `start()` / `stop()` / `get_snapshot()` interface as `udp_listener.py` and `simulator.py`. It is treated by every layer above it as just another telemetry source. No upstream layer was modified to accommodate it.

**Why the same interface matters:** The Race State Builder (`state_manager.py`) receives a raw dict and does not know — or care — which source produced it. The TelemetryController wraps any source with the same proxy logic. This is the payoff of ADR-003: adding a third data source is a single new file + a menu option in `main.py`. Zero changes to event_detector, strategy_tracker, or the AI layer.

**Why FastF1 and not a CSV export or manual data:** FastF1 wraps the official Ergast / OpenF1 / Jolyon Palmer datasets and provides a typed DataFrame API per session. It handles authentication, caching (`.fastf1_cache/`), and session structure automatically. A manual CSV approach would require maintaining data files and custom parsers. FastF1 gives us every race from 2018 with one `pip install`.

**Why lap-by-lap not telemetry-by-telemetry:** FastF1's per-lap granularity (one row per driver per lap) matches what the strategy engine consumes. The strategy tracker evaluates once per lap; sub-lap telemetry from the 60Hz UDP stream is redundant for lap-level decisions. Lap-level replay is simpler, more deterministic for testing, and directly maps to the trigger cadence.

**Gap computation approach:** FastF1 provides a cumulative `Time` column (elapsed seconds from race start to lap end for each driver). The gap between two adjacent-position cars is their `Time` delta on the same lap number. This is accurate to ~0.5s — sufficient for "should we push?" or "are we under threat?" decisions. The alternative (using official gap data from gap_to_leader columns) was considered but those columns are less reliably populated in FastF1 across all seasons.

**`pit_this_lap` flag design:** Real historical pit stops don't need the `PitStateMachine` animation — the car already pitted; we just need the strategy tracker to reset for the new stint. The `pit_this_lap` flag (set `True` for exactly one lap when TyreLife drops) gives `main.py` a clean hook: `tracker.reset_pit()` + suppress auto-pit. The FSM stays dormant. This keeps the interactive and replay paths separate without branching logic in the monitor loop.

**`session_fastest_lap` as Phase 3 #7 foundation:** The trigger for "driver can challenge for fastest lap" requires knowing the current session fastest from all 20 cars. That number changes every lap as teams push. FastF1 provides per-driver lap times for the full grid, so `_compute_running_fastest_lap()` is a natural O(n) pass over the dataset. It flows through `state_manager.py` as a pass-through field (defaults `None` for simulator and UDP sources, which don't have full-grid data). Phase 3 #7 reads it directly from `race_state["session_fastest_lap"]` without any new data plumbing.

**Alternative considered:** Building a separate `FastF1DataBroker` class that all layers query directly rather than funnelling through `get_snapshot()`. Rejected because it would break the single-source-of-truth contract — two paths to the same data creates synchronisation bugs. The telemetry interface is the right abstraction boundary.
