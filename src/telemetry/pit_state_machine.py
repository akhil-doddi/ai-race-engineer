"""
src/telemetry/pit_state_machine.py

Finite State Machine that simulates a pit stop sequence.

WHY A STATE MACHINE:
A pit stop is not a toggle. It has ordered phases with specific durations
and defined transitions. An if/else chain cannot represent this cleanly —
it breaks down when phases are interrupted or entered out of order.
A state machine is the correct abstraction.

STATE SEQUENCE:
    RACING → PIT_ENTRY → PIT_STOP → PIT_EXIT → RACING
      ↑                                             │
      └─────────────────────────────────────────────┘

WHAT EACH STATE DOES:
  RACING    — No overrides. Raw telemetry passes through unchanged.
  PIT_ENTRY — Car entering pit lane. Transition begins after 2 seconds.
              (In real F1 this is the time from pit lane entry to the box.)
  PIT_STOP  — Car stationary. Countdown timer runs. Fresh tyre data applied.
              The raw UDP data keeps flowing but we override tyre fields.
  PIT_EXIT  — Car leaving the box and rejoining. Tyre data stays fresh.
              Transitions back to RACING after 2 seconds.

COMPOUND SELECTION:
  Medium  → Hard   (standard second stint compound)
  Soft    → Medium (fresh intermediate stint)
  Hard    → Medium (rare, but handled)

USAGE:
  Called exclusively by TelemetryController. Do not call directly.
"""

import time


# ---------------------------------------------------------------------------
# State constants
# ---------------------------------------------------------------------------

class PitState:
    RACING    = "RACING"
    PIT_ENTRY = "PIT_ENTRY"
    PIT_STOP  = "PIT_STOP"
    PIT_EXIT  = "PIT_EXIT"


# Standard compound rotation for a two-stop race
NEXT_COMPOUND = {
    "Soft":   "Medium",
    "Medium": "Hard",
    "Hard":   "Medium",   # edge case: Hard → Medium for final stint
}

# Phase durations in real seconds (wall clock, not race laps)
_ENTRY_DURATION = 2.0    # seconds in pit lane before stationary
_STOP_DURATION  = 5.0    # seconds stationary (jacks up, tyres changed, jacks down)
_EXIT_DURATION  = 2.0    # seconds rejoining before racing state resumes


class PitStateMachine:
    """
    Tracks the state of a pit stop and provides field overrides to apply
    to the raw telemetry snapshot during each phase.

    The machine is idle (RACING) until trigger_pit() is called.
    tick() must be called frequently (every snapshot poll) to advance phases.
    get_overrides() returns the dict of fields to apply on top of raw data.
    """

    def __init__(self):
        self.state            = PitState.RACING
        self._new_compound    = "Hard"
        self._entered_at      = 0.0      # wall-clock time when current state began
        self._last_print_sec  = -1       # avoids spamming countdown every tick

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def trigger_pit(self, current_compound: str) -> bool:
        """
        Start the pit stop sequence.

        Selects the next compound automatically based on the current one.
        Returns True if the trigger was accepted, False if already pitting.

        Args:
            current_compound: The compound currently fitted (e.g. "Medium").
        """
        if self.state != PitState.RACING:
            return False   # already in pit sequence — ignore duplicate triggers

        self._new_compound = NEXT_COMPOUND.get(current_compound, "Hard")
        self.state         = PitState.PIT_ENTRY
        self._entered_at   = time.time()

        print(
            f"\n🔴 PIT STOP — entering pit lane  "
            f"(current: {current_compound}  →  new: {self._new_compound})"
        )
        return True

    def tick(self):
        """
        Advance the state machine based on elapsed wall-clock time.
        Call this on every telemetry poll cycle.
        """
        if self.state == PitState.RACING:
            return   # nothing to do

        now     = time.time()
        elapsed = now - self._entered_at

        if self.state == PitState.PIT_ENTRY:
            if elapsed >= _ENTRY_DURATION:
                self.state       = PitState.PIT_STOP
                self._entered_at = now
                print(
                    f"🔧 PIT STOP — stationary, tyres being changed  "
                    f"({_STOP_DURATION:.0f}s)"
                )

        elif self.state == PitState.PIT_STOP:
            remaining = _STOP_DURATION - elapsed
            # Print countdown once per second to avoid terminal spam
            current_sec = int(remaining)
            if current_sec != self._last_print_sec and remaining > 0:
                self._last_print_sec = current_sec
                print(f"   ⏱  {remaining:.0f}s  ·  "
                      f"fitting {self._new_compound} tyres...")
            if remaining <= 0:
                self.state       = PitState.PIT_EXIT
                self._entered_at = now
                print(
                    f"✅ TYRES FITTED — {self._new_compound} at 100% life  "
                    f"| exiting pit lane"
                )

        elif self.state == PitState.PIT_EXIT:
            if elapsed >= _EXIT_DURATION:
                self.state = PitState.RACING
                print(
                    f"🟢 PIT EXIT COMPLETE — rejoining on fresh "
                    f"{self._new_compound} tyres\n"
                )

    def get_overrides(self) -> dict:
        """
        Return the telemetry fields to override for the current state.

        In RACING: empty dict — raw data passes through unchanged.
        In PIT_ENTRY: empty dict — no tyre change yet, car still on track.
        In PIT_STOP / PIT_EXIT: fresh tyre data applied.
        """
        if self.state in (PitState.RACING, PitState.PIT_ENTRY):
            return {}

        # PIT_STOP and PIT_EXIT both show fresh tyre state
        return {
            "tire_wear":      100.0,
            "tire_age_laps":  0,
            "tire_compound":  self._new_compound,
        }

    @property
    def is_pitting(self) -> bool:
        """True when the car is in any non-racing pit phase."""
        return self.state != PitState.RACING

    @property
    def new_compound(self) -> str:
        """The compound being fitted (valid after trigger_pit())."""
        return self._new_compound
