"""
src/strategy/strategy_tracker.py

Converts the AI from a reactive chatbot into a proactive race engineer.

THE CORE PROBLEM THIS SOLVES:
A chatbot only speaks when asked. A real race engineer speaks when the SITUATION
demands it. This module is the "brain" that watches telemetry every lap and
decides when the engineer should speak WITHOUT the driver asking anything.

HOW IT WORKS:
Every lap, evaluate() is called with the latest race_state and event data.
It maintains state across laps (pit plan, previous tyre estimate, etc.) and
returns a list of TRIGGER names when something worth saying has happened.
main.py then calls the AI with a specific briefing prompt for each trigger.

THE STATE MACHINE:
    OPEN  →  PLANNED  →  APPROACHING  →  PIT_NOW  →  DONE
     ↑            ↑
     └── plan changes update the planned lap and fire PLAN_CHANGED trigger

TRIGGER TYPES (in priority order):
    INITIAL_BRIEF     — Lap 2: "You are P8 on Medium, planning to box lap 25."
    SC_OPPORTUNITY    — Full safety car, free pit window: "Safety car, box box box."
    VSC_OPPORTUNITY   — Virtual SC, advisory only: "VSC out, stay out / consider boxing."
    ENDGAME_MANAGE    — Final 10 laps, pit suppressed: "Manage tyres and finish the race."
    FINISH_RACE       — Planned pit window arrives in endgame: "No stop, bring it home."
    PLAN_CHANGED      — Tyre data shifted pit window by 3+ laps: "New box window lap 28."
    PIT_APPROACHING   — 3 laps before box: "Pit stop in 3 laps, prepare."
    PIT_NOW           — At or past planned pit lap + should_pit: "Box box box."
    FUEL_SAVE         — Projected fuel short of race end: "Lift and coast."
    PUSH_MODE         — Gap closing for 3 laps straight: "Push now, you have pace."
    POSITION_GAINED   — Driver overtook: "Up to P7, gap ahead 0.8s."
    POSITION_LOST     — Driver was overtaken: "Dropped to P9, defend."
    DRS_ENABLED       — DRS first available this stint: "DRS active, use it."

ANTI-SPAM PROTECTION:
    - Each trigger fires ONCE per lap (last_spoken_lap guard)
    - PIT_NOW only fires once (_pit_called flag)
    - PLAN_CHANGED requires a 3-lap shift to avoid noise from lap-to-lap variance
    - ENDGAME_MANAGE fires once per stint (_endgame_manage_called flag)
"""


class StrategyTracker:
    """
    Watches telemetry lap-by-lap and fires proactive engineer triggers.

    Usage in main.py:
        tracker = StrategyTracker()
        ...
        triggers = tracker.evaluate(race_state, event)
        for trigger in triggers:
            prompt = tracker.build_prompt(trigger, race_state, event)
            reply, history = ask_engineer(prompt, race_state, history)
            speak(reply)
    """

    def __init__(self):
        # The lap we are currently targeting for the pit stop.
        # Recalculated every lap from: current_lap + laps_left_on_tyre.
        self.planned_pit_lap: int | None = None

        # Tracks the previous plan so we can detect meaningful shifts.
        self._prev_planned_pit_lap: int | None = None

        # Prevents the same trigger firing more than once per lap.
        self._last_spoken_lap: int = -1

        # Prevents the "pit now" call from repeating every loop once triggered.
        self._pit_called: bool = False

        # Prevents the initial brief from firing more than once.
        self._initial_brief_done: bool = False

        # Prevents the SC pit call from repeating during a single SC period.
        # Reset to False when track_status returns to "green" so future SC
        # periods are handled correctly.
        # Named with both aliases for clarity — they refer to the same flag.
        self._sc_pit_called: bool = False          # internal name
        self.pit_prompted_during_sc = self._sc_pit_called  # spec alias (kept in sync below)

        # Prevents the VSC advisory from repeating during a single VSC period.
        # Like _sc_pit_called, resets when track goes green so a later VSC
        # period (if any) fires its own fresh advisory.
        self._vsc_called: bool = False

        # Tracks whether a safety car is currently active.
        # Set each evaluate() call from the event dict — used for clear
        # per-lap suppression logic: "if safety_car_active and pit_prompted_during_sc: skip"
        self.safety_car_active: bool = False

        # Prevents PIT_APPROACHING from firing more than once per stint.
        # Without this, every time PLAN_CHANGED shifts planned_pit_lap,
        # a new "3 laps before" threshold appears and PIT_APPROACHING fires again.
        self._approaching_called: bool = False

        # Prevents ENDGAME_MANAGE from repeating every lap in the final stint.
        # Resets in reset_pit() so it fires correctly after a mid-race stop that
        # leads into a new endgame (e.g. second stint ends in the final 10 laps).
        self._endgame_manage_called: bool = False

        # Prevents FINISH_RACE from repeating once a planned pit has been
        # cancelled due to race phase. Unlike ENDGAME_MANAGE (which fires
        # on tyre wear), FINISH_RACE fires when a pre-planned pit window
        # arrives but the race is too far gone to benefit from stopping.
        self._finish_race_called: bool = False

        # Tracks race position across laps so we can detect overtakes.
        # None until the first lap is recorded — prevents a false trigger
        # on lap 1 where the "previous" position is unknown.
        # _prev_position stores the position before the change so build_prompt()
        # can say "P8 → P7" without needing it passed in separately.
        self._last_position: int | None = None
        self._prev_position: int | None = None

        # DRS state tracking.
        # _last_drs: the DRS bool from the previous evaluate() call.
        #   Used to detect a False → True transition.
        # _drs_enabled_announced: prevents firing DRS_ENABLED every lap.
        #   DRS turns on/off multiple times per lap as the car passes through
        #   DRS zones. Without this flag, the engineer would say "DRS available"
        #   on every single lap. We fire once per stint — the first time DRS
        #   becomes available after a new stint starts. Resets in reset_pit()
        #   so the fresh-tyre stint gets its own DRS announcement.
        self._last_drs: bool = False
        self._drs_enabled_announced: bool = False

        # Fuel save mode — fires once when projected fuel falls short of the
        # race distance. NOT reset in reset_pit() because F1 cars do not
        # refuel during pit stops; once fuel is tight it stays tight.
        self._fuel_save_called: bool = False

        # Push mode — rolling 3-lap gap_ahead buffer for closing-rate detection.
        #
        # HOW IT WORKS:
        # Each lap, gap_ahead is appended to _gap_buffer. When the buffer has
        # 3+ entries, we check if the gap has been shrinking consistently
        # (each entry smaller than the previous). If so, the driver is closing
        # on the car ahead and the engineer calls a push lap.
        #
        # SC GUARD: _gap_buffer_sc_tainted is set True when any lap in the
        # buffer was recorded under safety car. When track goes green we flush
        # the buffer entirely — SC restarts cause artificial gap jumps that
        # would produce false push calls.
        #
        # FIRES ONCE PER STINT: _push_mode_called prevents repeating every lap
        # while the gap keeps shrinking. Resets in reset_pit() so a fresh stint
        # on new rubber can fire its own push call.
        self._gap_buffer: list[float] = []
        self._gap_buffer_sc_tainted: bool = False
        self._push_mode_called: bool = False

    def evaluate(self, race_state: dict, event: dict) -> list[str]:
        """
        Evaluate the current race situation and return a list of trigger names.
        Call this every loop BEFORE asking for driver input.

        Returns [] when the engineer should stay silent.
        Returns one or more trigger names when the engineer should speak.

        Args:
            race_state: Clean race_state from build_race_state()
            event:      Event dict from get_event()

        Returns:
            List of trigger name strings. Usually 0 or 1. Rarely more.
        """
        current_lap      = race_state["lap"]
        current_position = race_state["position"]
        laps_left        = race_state["laps_remaining"]
        gap_behind       = race_state["gap_behind"]
        laps_on_tyre     = event["laps_left_on_tyre"]
        should_pit       = event["should_pit"]

        # --- Recalculate planned pit lap from telemetry ---
        # This is pure maths, not AI guesswork.
        # planned_pit_lap = what lap will we run out of useful tyre life.
        if laps_on_tyre > 0 and laps_left > 3:
            new_plan = current_lap + laps_on_tyre
            # Cap at total laps - 1 (no point pitting on the last lap)
            new_plan = min(new_plan, race_state["lap"] + laps_left - 1)
        else:
            new_plan = self.planned_pit_lap  # no change if data is unreliable

        triggers = []

        # ── GUARD: only one proactive call per lap ──────────────────────────
        # Without this, the same trigger fires every 2 seconds (every loop).
        # We still update _last_position here so the baseline stays accurate
        # even on laps where we already spoke — otherwise position could appear
        # to jump by 2 on the next lap.
        if current_lap == self._last_spoken_lap:
            self.planned_pit_lap = new_plan
            self._last_position  = current_position
            self._last_drs       = race_state.get("drs", False)
            return []

        # ── TRIGGER 1: Initial strategy brief ───────────────────────────────
        # Fire once on lap 2 so we have at least one lap of wear data.
        # Lap 1 data is unreliable (fresh tyres, no wear rate established yet).
        if not self._initial_brief_done and current_lap >= 2:
            self._initial_brief_done = True
            self.planned_pit_lap = new_plan
            self._last_spoken_lap = current_lap
            return ["INITIAL_BRIEF"]

        # ── TRIGGER 1b: Safety car pit opportunity ───────────────────────────
        # This trigger is independent of tyre life calculations.
        # It fires the moment a safety car is confirmed and we haven't already
        # called the driver in under this SC period.
        #
        # WHY IT HAS PRIORITY OVER PLAN_CHANGED AND PIT_APPROACHING:
        # A safety car window is time-critical — it lasts only a few laps.
        # Missing it by waiting for the normal pit lap to arrive costs positions.
        # We return immediately here to avoid the SC call being swamped by
        # lower-priority triggers firing on the same lap.
        #
        # Reset logic: _sc_pit_called is reset to False when track goes green,
        # so a second SC period later in the race is handled correctly.
        #
        # SUPPRESSION RULE (from spec):
        #   if safety_car_active and pit_prompted_during_sc: skip BOX prompt
        sc_active = event.get("safety_car", False)
        self.safety_car_active = sc_active            # expose for external inspection

        if not sc_active:
            # Green flag — reset so we're ready for any future SC/VSC period.
            self._sc_pit_called = False
            self._vsc_called    = False
        self.pit_prompted_during_sc = self._sc_pit_called  # keep alias in sync

        # SC/VSC ACTIVE — all normal pit logic (PLAN_CHANGED, PIT_NOW) is
        # only valid under green flag. We return immediately after this block.
        #
        # FULL SC path: fire SC_OPPORTUNITY once (pit call), then go silent.
        # VSC path: fire VSC_OPPORTUNITY once (always advisory), then go silent.
        #   VSC_OPPORTUNITY passes event["should_pit"] to build_prompt so the
        #   AI knows whether to recommend boxing or holding position.
        if sc_active:
            track_status_now = race_state.get("track_status", "safety_car")

            if track_status_now == "virtual_safety_car":
                # VSC — fire advisory once, regardless of should_pit value.
                # build_prompt reads event["should_pit"] to decide the message tone.
                if (not self._vsc_called
                        and current_lap != self._last_spoken_lap):
                    self._vsc_called = True
                    self._last_spoken_lap = current_lap
                    return ["VSC_OPPORTUNITY"]
                # Already briefed this VSC period — stay silent.
                return []
            else:
                # Full SC — fire pit call once if conditions allow.
                if (not self._sc_pit_called
                        and not self._pit_called
                        and event.get("should_pit", False)  # enforces tire_age >= SC_MIN_TYRE_AGE
                        and current_lap > 5
                        and laps_left > 8):
                    self._sc_pit_called = True
                    self.pit_prompted_during_sc = True
                    self._last_spoken_lap = current_lap
                    return ["SC_OPPORTUNITY"]
                # Already called, or conditions not met — return silently.
                return []


        # ── TRIGGER 1c: Endgame tyre management ─────────────────────────────
        # Fires once when we enter the final ENDGAME_LAP_THRESHOLD laps with
        # worn tyres and event_detector has suppressed the pit recommendation.
        #
        # WHY THIS IS SEPARATE FROM PLAN_CHANGED:
        # PLAN_CHANGED communicates a shift in the pit window. ENDGAME_MANAGE
        # communicates the cancellation of the pit plan entirely — a different
        # message requiring a different tone. "Pit window moved to lap 32" is
        # strategy talk. "No stop, manage tyres, bring it home" is survival talk.
        #
        # WHY WE CHECK tyre_wear < 40%:
        # ENDGAME_MANAGE is only worth saying when the tyres are actually
        # degraded. If tyre life is 75% with 10 laps left there is nothing
        # meaningful to communicate — the car is fine. The threshold of 40%
        # ensures we speak when the driver would otherwise expect a pit call.
        #
        # WHY endgame_override GATE:
        # event_detector sets endgame_override=True only when a pit was actively
        # suppressed due to race phase. Without this gate, ENDGAME_MANAGE would
        # fire purely based on laps remaining, producing a false alert on laps
        # where tyres are still healthy and no pit was ever going to be called.
        if (event.get("endgame_override", False)
                and not self._endgame_manage_called
                and race_state["tire_wear"] < 40.0
                and current_lap != self._last_spoken_lap):
            self._endgame_manage_called = True
            self._last_spoken_lap = current_lap
            return ["ENDGAME_MANAGE"]

        # ── TRIGGER 1d: Finish race — planned pit cancelled ──────────────────
        # Fires when a pre-planned pit stop window arrives but we are in
        # endgame phase (laps_remaining <= ENDGAME_LAP_THRESHOLD).
        #
        # WHY THIS IS NEEDED (the specific bug this fixes):
        # PIT_APPROACHING fires purely on lap arithmetic:
        #   current_lap == planned_pit_lap - 3
        # It does not check race phase or should_pit. So when planned_pit_lap
        # is lap 52 and we reach lap 49 with only 9 laps remaining, it fires
        # "Pit in 3 laps" — even though the pit is strategically worthless.
        #
        # WHY NOT JUST GUARD PIT_APPROACHING:
        # Suppressing PIT_APPROACHING without replacing it leaves the driver
        # with no communication at a moment they would expect one. FINISH_RACE
        # takes over the communication slot and delivers the correct message:
        # "The planned stop is cancelled — bring it home."
        #
        # WHY SEPARATE FROM ENDGAME_MANAGE:
        # ENDGAME_MANAGE fires reactively to tyre degradation (endgame_override=True,
        # tyre < 40%). FINISH_RACE fires proactively when a scheduled pit window
        # arrives during endgame — regardless of tyre wear. Both can be relevant
        # in the same race but cover different scenarios:
        #   - Car finishes SC pit on fresh rubber, planned pit at lap 52 arrives
        #     with tyres still at 55% → ENDGAME_MANAGE won't fire (no override,
        #     tyre healthy) but FINISH_RACE will (lap 52 approaching in endgame).
        #   - Car on worn tyres, no planned pit nearby, laps_remaining = 8
        #     → ENDGAME_MANAGE fires (endgame_override=True, tyre < 40%).
        #
        # CONDITION:
        #   race_phase == "endgame"  — final ENDGAME_LAP_THRESHOLD laps
        #   planned_pit_lap exists   — there is a pit plan to cancel
        #   current_lap >= planned_pit_lap - 3  — pit window has arrived or passed
        #   not _finish_race_called  — don't repeat
        #   not _pit_called          — don't fire if pit already happened this stint
        if (event.get("race_phase", "mid") == "endgame"
                and self.planned_pit_lap is not None
                and current_lap >= self.planned_pit_lap - 3
                and not self._finish_race_called
                and not self._pit_called
                and current_lap != self._last_spoken_lap):
            self._finish_race_called = True
            self._last_spoken_lap = current_lap
            return ["FINISH_RACE"]

        # ── TRIGGER 2: Plan changed significantly ───────────────────────────
        # Fire when the tyre estimate shifts the pit window by 3+ laps.
        # 3-lap threshold prevents noise from lap-to-lap calculation variance.
        # Only relevant when there's still time to act on the new plan.
        # Suppressed in endgame — plan changes have no strategic value when
        # the race is nearly over.
        if (self.planned_pit_lap is not None
                and new_plan is not None
                and abs(new_plan - self.planned_pit_lap) >= 3
                and laps_left > 5
                and event.get("race_phase", "mid") != "endgame"):
            self._prev_planned_pit_lap = self.planned_pit_lap
            self.planned_pit_lap = new_plan
            self._last_spoken_lap = current_lap
            triggers.append("PLAN_CHANGED")

        # ── TRIGGER 3: Pit approaching warning ──────────────────────────────
        # Warn the driver 3 laps before the box call.
        # Gives the driver time to prepare mentally and finish a fast sector.
        # _approaching_called prevents this from re-firing when PLAN_CHANGED
        # shifts planned_pit_lap to a new value (which creates a new -3 threshold).
        #
        # ENDGAME GUARD: suppressed when race_phase == "endgame".
        # In endgame, FINISH_RACE (above) intercepts the pit-approaching moment
        # and delivers the correct "no stop" message. PIT_APPROACHING must not
        # fire on the same lap or any later lap in the same endgame period.
        if (self.planned_pit_lap is not None
                and current_lap == self.planned_pit_lap - 3
                and laps_left > 3
                and not self._pit_called
                and not self._approaching_called
                and event.get("race_phase", "mid") != "endgame"):
            if "PLAN_CHANGED" not in triggers:  # don't double-speak same lap
                self._approaching_called = True
                triggers.append("PIT_APPROACHING")
                self._last_spoken_lap = current_lap

        # ── TRIGGER 4: Box now ──────────────────────────────────────────────
        # Fire when we reach the planned pit lap AND the event system agrees.
        # should_pit=True means tyre life or stint age confirms it's time.
        #
        # ENDGAME GUARD: suppressed when race_phase == "endgame".
        # In normal endgame, event_detector already sets should_pit=False via
        # endgame_override, so this guard is redundant for most cases. It is
        # kept explicit here as a safety net for edge cases where the two layers
        # might disagree (e.g. a plan shift puts planned_pit_lap into endgame
        # after event_detector has already run for this lap).
        if (not self._pit_called
                and self.planned_pit_lap is not None
                and current_lap >= self.planned_pit_lap
                and should_pit
                and laps_left > 2
                and event.get("race_phase", "mid") != "endgame"):
            self._pit_called = True
            self._last_spoken_lap = current_lap
            triggers.append("PIT_NOW")

        # ── TRIGGER 5: Fuel save mode ────────────────────────────────────────
        # Fires once when projected fuel falls short of the remaining race
        # distance — telling the driver to start lifting and coasting.
        #
        # CONDITION:
        #   fuel_laps_remaining < laps_left + 2
        #     → at current burn rate we run out 2+ laps before the flag.
        #     → the +2 buffer gives the driver warning before it's critical.
        #   fuel_laps_remaining > 8
        #     → not already in the fuel_critical zone (< 5 laps) that
        #       event_detector handles via the red urgency path. We speak
        #       about fuel saving, not fuel emergency.
        #   laps_left > ENDGAME_LAP_THRESHOLD (effectively: not endgame)
        #     → in endgame the FINISH_RACE / ENDGAME_MANAGE triggers already
        #       hold the communication slot. Fuel framing at that point adds
        #       noise rather than actionable information.
        #   not _fuel_save_called
        #     → fires exactly once per race. F1 cars do not refuel during pit
        #       stops, so once fuel is short it stays short. Not in reset_pit().
        #
        # PRIORITY — above position/DRS (strategic instruction vs. information):
        # If fuel is short AND a position change happened on the same lap,
        # the fuel message is more actionable. Position can wait one lap.
        fuel_laps_rem = event.get("fuel_laps_remaining", 99.0)
        if (not triggers
                and not self._fuel_save_called
                and fuel_laps_rem < laps_left + 2
                and fuel_laps_rem > 8
                and laps_left > 10
                and current_lap >= 5           # fuel burn rate unreliable on laps 1-4
                and event.get("race_phase", "mid") != "endgame"
                and current_lap != self._last_spoken_lap):
            self._fuel_save_called = True
            self._last_spoken_lap  = current_lap
            triggers.append("FUEL_SAVE")

        # ── TRIGGER 5b: Push mode — closing rate detection ─────────────────
        # Fires once per stint when the gap to the car ahead has been
        # shrinking consistently over the last 3 laps.
        #
        # WHY 3 LAPS:
        # A single lap of gap reduction could be traffic, DRS, or noise.
        # 3 consecutive laps of closing means the driver genuinely has pace
        # advantage. It's the minimum window that filters noise while still
        # being responsive enough to call a push at the right time.
        #
        # SC BUFFER FLUSH:
        # Any gap readings taken under SC/VSC are invalid — the field is
        # artificially compressed. When SC ends (_gap_buffer_sc_tainted)
        # we clear the entire buffer so the first 3 green-flag laps build
        # a fresh closing-rate picture.
        #
        # WHY ONCE PER STINT:
        # Once the engineer calls "push now", repeating it every lap while
        # the gap keeps closing adds no information. The driver knows.
        # _push_mode_called resets in reset_pit() so a fresh stint on new
        # rubber gets its own push opportunity.
        gap_ahead_now = race_state.get("gap_ahead", 0.0)

        # SC taint: mark buffer as dirty if current lap is under SC/VSC.
        if sc_active:
            self._gap_buffer_sc_tainted = True

        # Flush on SC→green transition: discard all SC-era readings.
        if not sc_active and self._gap_buffer_sc_tainted:
            self._gap_buffer.clear()
            self._gap_buffer_sc_tainted = False

        # Record gap if under green flag and gap is valid (> 0 means a car exists ahead).
        if not sc_active and gap_ahead_now > 0.0 and current_lap >= 3:
            self._gap_buffer.append(gap_ahead_now)
            # Keep only the last 3 entries — we only need a 3-lap window.
            if len(self._gap_buffer) > 3:
                self._gap_buffer = self._gap_buffer[-3:]

        # Check for consistent closing: each entry < the previous.
        if (not triggers
                and not self._push_mode_called
                and len(self._gap_buffer) >= 3
                and not sc_active
                and current_lap >= 5
                and current_lap != self._last_spoken_lap
                and gap_ahead_now <= 3.0):   # only call push when within realistic striking distance
            g = self._gap_buffer
            closing = g[-1] < g[-2] < g[-3]
            # Minimum total closure: at least 0.3s over the 3-lap window.
            # This filters micro-oscillations (e.g. 1.52 → 1.51 → 1.50) that
            # technically close but don't represent real pace advantage.
            total_closure = g[-3] - g[-1]
            if closing and total_closure >= 0.3:
                self._push_mode_called = True
                self._last_spoken_lap  = current_lap
                triggers.append("PUSH_MODE")

        # ── TRIGGER 6: Position gained or lost ──────────────────────────────
        # Fires when race position changes vs. the previous lap baseline.
        #
        # WHY AT THE END (lowest priority):
        # Position changes are informational, not strategic. If PLAN_CHANGED,
        # PIT_APPROACHING, or PIT_NOW already fired this lap, those messages
        # matter more. We only speak about position when the lap is otherwise
        # quiet — `not triggers` ensures we don't double-speak.
        #
        # WHY lap >= 3:
        # Race start is chaotic. Lap 1 positions are grid order, not race order.
        # By lap 3 the field has sorted itself and gaps are meaningful.
        #
        # WHY not sc_active:
        # Safety car bunches the field and creates artificial position swaps.
        # Announcing "you've gained a position" under SC is misleading — it
        # doesn't reflect real racing pace.
        #
        # WHY not self._pit_called:
        # After a BOX call the car exits the pit lane at a different position.
        # That position change is expected, already handled by the pit sequence
        # messages, and should not fire a "you dropped to P12" alert.
        # reset_pit() clears _last_position so the new stint baseline is clean.
        if (not triggers
                and self._last_position is not None
                and current_position != self._last_position
                and current_lap >= 3
                and not sc_active
                and not self._pit_called):
            self._prev_position = self._last_position
            self._last_spoken_lap = current_lap
            if current_position < self._last_position:   # lower number = better
                triggers.append("POSITION_GAINED")
            else:
                triggers.append("POSITION_LOST")

        # ── TRIGGER 6: DRS available ─────────────────────────────────────────
        # Fires once per stint the first time DRS turns on after being off.
        #
        # WHY ONCE PER STINT AND NOT EVERY LAP:
        # DRS flips on and off multiple times per lap as the car enters and
        # exits each DRS zone. Firing on every False→True transition would
        # produce a "DRS available" message on every single racing lap, which
        # is noise. The useful moment is when DRS BECOMES available for the
        # first time in a stint — either at race start, after a safety car
        # period, or on the out-lap after a pit stop (fresh rubber, DRS on).
        # _drs_enabled_announced ensures it fires exactly once per stint.
        # reset_pit() clears it so the new stint gets its own announcement.
        #
        # WHY not sc_active:
        # DRS is always disabled under safety car. Announcing "DRS available"
        # during a safety car period would be incorrect and confusing.
        #
        # NOTE: In simulator mode drs is always False — this trigger is
        # PS5/UDP only. That's by design; the simulator has no DRS zones.
        current_drs = race_state.get("drs", False)
        if (not triggers
                and not sc_active
                and current_lap >= 3
                and current_drs
                and not self._last_drs
                and not self._drs_enabled_announced):
            self._drs_enabled_announced = True
            self._last_spoken_lap = current_lap
            triggers.append("DRS_ENABLED")

        # Update stored plan, position baseline, and DRS state for next call.
        self.planned_pit_lap = new_plan
        self._last_position  = current_position
        self._last_drs       = current_drs

        return triggers

    def mark_sc_pit_used(self):
        """
        Notify the tracker that a pit stop has been triggered under the
        current safety car session, regardless of which code path called it.

        WHY THIS METHOD EXISTS:
        There are two independent paths that can trigger a pit stop:
          1. tracker.evaluate() returning SC_OPPORTUNITY — sets _sc_pit_called=True
             inside evaluate() itself, so no external call is needed.
          2. proactive_monitor's urgency-change handler — reacts to event urgency
             changing to yellow/red with should_pit=True. This path calls
             controller.trigger_pit() directly and does NOT go through evaluate(),
             so _sc_pit_called is never set.

        Without this method, path 2 would leave _sc_pit_called=False. Five laps
        later when tire_age crosses SC_MIN_TYRE_AGE again, urgency changes once
        more and a second pit call fires under the same SC session.

        main.py calls this immediately after any SC pit via the urgency-change path.
        After this call, evaluate() correctly returns [] for the rest of the SC session.
        """
        self._sc_pit_called = True
        self.pit_prompted_during_sc = True

    def reset_pit(self):
        """
        Call this after a pit stop is confirmed, so the tracker can
        monitor the next stint. Clears the pit-called flag and plan.
        """
        self._pit_called = False
        self._approaching_called = False    # fresh stint gets its own warning
        self._endgame_manage_called = False # new stint may also end in endgame
        self._finish_race_called = False    # new stint may also reach endgame
        # Position baseline is cleared so the pit exit position doesn't
        # register as a "position lost" alert on the first lap of the new stint.
        # It re-establishes on the next evaluate() call automatically.
        self._last_position = None
        self._prev_position = None
        # DRS: reset so the new stint gets a fresh DRS announcement when
        # the car first enters a DRS zone on the out-lap.
        self._drs_enabled_announced = False
        self._last_drs = False
        # Push mode: fresh stint starts with an empty gap buffer so the
        # out-lap gap (artificially large after pit exit) doesn't poison
        # the closing-rate calculation. _push_mode_called resets so the
        # new stint can fire its own push call when closing resumes.
        self._gap_buffer.clear()
        self._gap_buffer_sc_tainted = False
        self._push_mode_called = False
        # NOTE: _sc_pit_called is NOT reset here.
        # It resets only when track goes green (end of SC period), handled in evaluate().
        # If reset_pit() cleared it, a second SC_OPPORTUNITY would fire for the same
        # SC period immediately after the first call triggered the reset.
        self._initial_brief_done = True   # keep True — brief already given
        self.planned_pit_lap = None
        self._prev_planned_pit_lap = None
        self._last_spoken_lap = -1

    # -----------------------------------------------------------------------
    # Prompt builders — these are the instructions sent to the AI for each
    # proactive trigger. They are tight, specific, and radio-style.
    # The AI is told WHAT to say, not asked to decide whether to say it.
    # -----------------------------------------------------------------------

    def build_prompt(self, trigger: str, race_state: dict, event: dict) -> str:
        """
        Build the briefing prompt for a given trigger.

        This is NOT a question for the AI to answer freely.
        It is an instruction: "Here is the situation — brief the driver now."
        The AI's job is to deliver the message clearly in 1-2 sentences.

        Args:
            trigger:    One of the TRIGGER TYPE constants above.
            race_state: Current clean race_state.
            event:      Current event dict.

        Returns:
            A prompt string to pass as driver_input to ask_engineer().
        """
        lap      = race_state["lap"]
        pos      = race_state["position"]
        compound = race_state["tire_compound"]
        life     = race_state["tire_wear"]
        gap_ahe  = race_state.get("gap_ahead", 0.0)
        gap_beh  = race_state["gap_behind"]
        laps_rem = race_state["laps_remaining"]
        pit_lap  = self.planned_pit_lap
        prev_lap = self._prev_planned_pit_lap

        if trigger == "INITIAL_BRIEF":
            return (
                f"[ENGINEER BRIEFING] Give the driver a one-sentence race start brief. "
                f"We are P{pos} on {compound} tyres. "
                f"Planned pit window is around lap {pit_lap}. "
                f"Radio style. No questions back to driver."
            )

        elif trigger == "PLAN_CHANGED":
            direction = "later" if pit_lap > prev_lap else "earlier"
            return (
                f"[ENGINEER BRIEFING] Inform the driver the pit plan has changed. "
                f"Previous plan was lap {prev_lap}. New target is lap {pit_lap} ({direction}). "
                f"Tyres currently at {life:.0f}% on {compound}. "
                f"1-2 sentences. Explain the reason briefly. Radio style."
            )

        elif trigger == "PIT_APPROACHING":
            return (
                f"[ENGINEER BRIEFING] Warn the driver: pit stop in 3 laps. "
                f"Current lap: {lap}. Box lap: {pit_lap}. "
                f"One sentence. Prepare the driver. Radio style."
            )

        elif trigger == "PIT_NOW":
            return (
                f"[ENGINEER BRIEFING] Call the driver into the pits NOW. "
                f"We are on lap {lap}. Tyres at {life:.0f}%. "
                f"Say box box box. Confirm new tyre compound ({compound} → fresh set). "
                f"1-2 sentences. Urgent radio style."
            )


        elif trigger == "SC_OPPORTUNITY":
            return (
                f"[ENGINEER BRIEFING] Safety car is deployed on lap {lap}. "
                f"This is a free pit window — the time loss is neutralised by the SC. "
                f"We are P{pos} on {compound} at {life:.0f}% life, {race_state['laps_remaining']} laps remaining. "
                f"Call the driver in: say 'safety car, box box box'. "
                f"Tell them what compound we're going onto. "
                f"2 sentences maximum. Urgent but calm radio style."
            )

        elif trigger == "VSC_OPPORTUNITY":
            tyre_age = race_state.get("tire_age_laps", 0)
            should_pit = event.get("should_pit", False)
            if should_pit:
                pit_advice = (
                    f"Tyre age is {tyre_age} laps on {compound} at {life:.0f}% life — "
                    f"conditions favour a stop. Recommend boxing this lap. "
                    f"Say 'virtual safety car, box box box' and confirm the tyre choice."
                )
            else:
                pit_advice = (
                    f"Tyre age is {tyre_age} laps on {compound} at {life:.0f}% life — "
                    f"tyres are in good shape, pit stop not warranted. "
                    f"Tell the driver: stay out, maintain the delta, hold position."
                )
            return (
                f"[ENGINEER BRIEFING] Virtual safety car deployed on lap {lap}. "
                f"VSC reduces but does not eliminate pit stop time loss — it is a smaller "
                f"opportunity than a full safety car. "
                f"{pit_advice} "
                f"2 sentences maximum. Calm, measured radio style."
            )

        elif trigger == "ENDGAME_MANAGE":
            return (
                f"[ENGINEER BRIEFING] We are in the final {laps_rem} laps of the race. "
                f"Tyres are at {life:.0f}% on {compound}. "
                f"We are NOT pitting. Track position is more valuable than fresh rubber now. "
                f"Tell the driver: no stop, manage tyre temperatures, protect the car, "
                f"bring it home. Switch mindset from strategy mode to survival mode. "
                f"2 sentences, calm and authoritative radio style."
            )

        elif trigger == "FINISH_RACE":
            return (
                f"[ENGINEER BRIEFING] We are P{pos} with {laps_rem} laps to go. "
                f"The planned pit stop is cancelled — not enough laps remaining to benefit. "
                f"Tyres are at {life:.0f}% on {compound}. They will get us to the flag. "
                f"Tell the driver clearly: pit stop not required, maintain position, "
                f"bring the car home. No mention of laps until the stop. "
                f"1-2 sentences, calm and decisive radio style."
            )

        elif trigger == "FUEL_SAVE":
            fuel_laps = round(event.get("fuel_laps_remaining", 0.0), 1)
            shortfall = round(laps_rem - fuel_laps, 1)
            return (
                f"[ENGINEER BRIEFING] Fuel projection is tight. "
                f"At current burn rate we have {fuel_laps} laps of fuel but "
                f"{laps_rem} laps remaining — approximately {shortfall} laps short. "
                f"Instruct the driver to start lifting and coasting to save fuel. "
                f"Tell them the shortfall and what to do. "
                f"2 sentences max. Calm, clear radio style."
            )

        elif trigger == "PUSH_MODE":
            # Calculate closing rate from the buffer for the prompt.
            buf = self._gap_buffer
            if len(buf) >= 3:
                closing_rate = round((buf[-3] - buf[-1]) / 2, 2)  # avg per lap
                gap_3_ago    = round(buf[-3], 1)
            else:
                closing_rate = 0.0
                gap_3_ago    = gap_ahe
            return (
                f"[ENGINEER BRIEFING] Gap to car ahead is closing consistently. "
                f"3 laps ago the gap was {gap_3_ago}s, now it's {gap_ahe:.1f}s — "
                f"closing at approximately {closing_rate}s per lap. "
                f"We are P{pos} on {compound} at {life:.0f}% life. "
                f"Tell the driver they have pace advantage, push now, and aim to "
                f"get within DRS range. Mention the gap and closing rate. "
                f"1-2 sentences. Energetic, motivating radio style."
            )

        elif trigger == "DRS_ENABLED":
            return (
                f"[ENGINEER BRIEFING] DRS is now available on this stint. "
                f"We are P{pos} with {gap_ahe:.1f}s to the car ahead. "
                f"1 sentence. Tell the driver DRS is active and to use it if "
                f"within range. Short, sharp radio style."
            )

        elif trigger == "POSITION_GAINED":
            prev_pos = self._prev_position if self._prev_position is not None else pos + 1
            return (
                f"[ENGINEER BRIEFING] Driver just gained a position on track. "
                f"We are now P{pos}, up from P{prev_pos}. "
                f"Gap to car ahead is {gap_ahe:.1f}s, car behind {gap_beh:.1f}s. "
                f"1 sentence. Positive, encouraging radio style. "
                f"Mention the new position and stay focused."
            )

        elif trigger == "POSITION_LOST":
            prev_pos = self._prev_position if self._prev_position is not None else pos - 1
            return (
                f"[ENGINEER BRIEFING] Driver just lost a position on track. "
                f"We dropped from P{prev_pos} to P{pos}. "
                f"Car behind now {gap_beh:.1f}s. "
                f"1 sentence. Acknowledge it briefly, keep focus forward, radio style."
            )

        return f"[ENGINEER BRIEFING] Brief the driver on current race situation. Lap {lap}, P{pos}."
