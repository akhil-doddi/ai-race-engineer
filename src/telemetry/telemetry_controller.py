"""
src/telemetry/telemetry_controller.py

Intercepts raw telemetry snapshots and applies dynamic state overrides.

WHY THIS LAYER EXISTS:
The raw telemetry source (simulator or UDP listener) is a read-only stream.
It does not know about pit stops, compound changes, or driver decisions.
This controller wraps the source and acts as a gatekeeper on get_snapshot():
  - Fields not being overridden pass through from the raw source unchanged.
  - Fields covered by the current PitStateMachine state are replaced.

The raw telemetry generator keeps running underneath completely unmodified.
Only the controller's output (what the rest of the system sees) changes.

POST-PIT PERSISTENT OVERRIDES:
After a pit stop completes and we return to RACING, the raw UDP sender still
has no idea a pit happened — it keeps broadcasting the old compound and wear.
To fix this, we maintain a _post_pit dict that tracks:
  - compound: the newly fitted tyre
  - at_lap:   the lap number when we rejoined the track
  - wear_rate: % per lap to degrade the fresh tyre (3.5% default)
On every get_snapshot() call we compute current wear from laps elapsed and
keep overriding the tyre fields until the next pit stop is triggered.

INTERFACE CONTRACT:
TelemetryController exposes the same start()/stop()/get_snapshot() interface
as TelemetrySimulator and UDPTelemetryListener. Drop-in replacement with
one line change in main.py.

THREAD SAFETY:
trigger_pit() can be called from the reactive (main) thread while
get_snapshot() is called from the proactive monitor thread.
A threading.Lock() protects the PitStateMachine from concurrent access.
"""

import threading
from src.telemetry.pit_state_machine import PitStateMachine

# Wear rate used for post-pit stint simulation.
# 3.5 % per lap matches the sender's normal (pre-cliff) pace.
_POST_PIT_WEAR_RATE = 3.5


class TelemetryController:
    """
    Wraps any telemetry source and layers dynamic pit stop overrides on top.

    Usage:
        raw_source = UDPTelemetryListener()   # or TelemetrySimulator()
        telemetry  = TelemetryController(raw_source)
        telemetry.start()

        # From proactive monitor or reactive thread:
        telemetry.trigger_pit(current_compound="Medium")

        # get_snapshot() returns raw data + pit overrides applied:
        snap = telemetry.get_snapshot()
    """

    def __init__(self, source):
        """
        Args:
            source: Any telemetry source implementing start()/stop()/get_snapshot().
                    Typically TelemetrySimulator or UDPTelemetryListener.
        """
        self._source      = source
        self._pit         = PitStateMachine()
        self._lock        = threading.Lock()

        # Callback invoked when pit sequence finishes (RACING resumes).
        # Set from main.py to reset the StrategyTracker for the new stint.
        self.on_pit_complete = None

        # Track previous pit state so we can fire on_pit_complete exactly once.
        self._was_pitting = False

        # Post-pit persistent override state.
        # Set when a pit stop completes; cleared when the next pit is triggered.
        # Structure: {"compound": str, "at_lap": int, "wear_rate": float}
        # None means no pit has occurred yet — raw tyre data passes through.
        self._post_pit = None

    # -----------------------------------------------------------------------
    # Pass-through interface
    # -----------------------------------------------------------------------

    def start(self):
        """Start the underlying telemetry source."""
        self._source.start()

    def stop(self):
        """Stop the underlying telemetry source."""
        self._source.stop()

    # -----------------------------------------------------------------------
    # Core method — called every proactive monitor poll
    # -----------------------------------------------------------------------

    def get_snapshot(self) -> dict:
        """
        Return the current race state with pit overrides applied.

        Flow:
          1. Get raw snapshot from the underlying source.
          2. Advance the PitStateMachine clock (tick).
          3. If a pit sequence just finished, record post-pit state & fire callback.
          4. Apply pit-phase overrides (PIT_ENTRY / PIT_STOP / PIT_EXIT).
          5. Apply post-pit persistent overrides (RACING with fresh tyres).
          6. Return modified snapshot — callers cannot tell the difference.
        """
        # Step 1: raw data from the actual telemetry source
        raw = self._source.get_snapshot()

        with self._lock:
            # Step 2: advance state machine
            self._pit.tick()

            # Step 3: detect pit-complete transition (pitting → racing)
            currently_pitting = self._pit.is_pitting
            if self._was_pitting and not currently_pitting:
                # Pit sequence just finished — store post-pit state so we can
                # keep overriding tyre fields throughout the new stint.
                self._post_pit = {
                    "compound":  self._pit.new_compound,
                    "at_lap":    raw.get("lap", 0),
                    "wear_rate": _POST_PIT_WEAR_RATE,
                }
                if callable(self.on_pit_complete):
                    threading.Thread(
                        target=self.on_pit_complete,
                        daemon=True,
                        name="PitCompleteCallback",
                    ).start()
            self._was_pitting = currently_pitting

            # Step 4: active pit-phase overrides (fresh tyres during stop/exit)
            overrides = self._pit.get_overrides()

            # Step 5: post-pit persistent overrides (after rejoining track)
            # Only applied when NOT in an active pit phase (avoids double-write)
            post_pit = self._post_pit if not currently_pitting else None

        # Apply active pit-phase overrides first (highest priority)
        if overrides:
            raw.update(overrides)
        elif post_pit:
            # Compute how many laps have passed since we rejoined
            laps_since_pit = max(0, raw.get("lap", 0) - post_pit["at_lap"])
            # Degrade from 100% at the fitted wear rate
            current_wear = max(0.0, 100.0 - laps_since_pit * post_pit["wear_rate"])
            raw["tire_compound"]  = post_pit["compound"]
            raw["tire_wear"]      = round(current_wear, 1)
            raw["tire_age_laps"]  = laps_since_pit

        return raw

    # -----------------------------------------------------------------------
    # Command API — called by AI layer when a pit stop is confirmed
    # -----------------------------------------------------------------------

    def trigger_pit(self, current_compound: str) -> bool:
        """
        Start the pit stop simulation sequence.

        Safe to call from any thread. Ignored if already pitting.

        Also clears any post-pit persistent overrides so the PitStateMachine's
        fresh overrides take over cleanly for the new stop.

        Args:
            current_compound: The tyre compound currently fitted.

        Returns:
            True if pit sequence started, False if already pitting.
        """
        with self._lock:
            result = self._pit.trigger_pit(current_compound)
            if result:
                # Clear post-pit state — pit-phase overrides take over now
                self._post_pit = None
            return result

    # -----------------------------------------------------------------------
    # State queries
    # -----------------------------------------------------------------------

    @property
    def is_pitting(self) -> bool:
        """True while any pit phase is active."""
        return self._pit.is_pitting

    @property
    def new_compound(self) -> str:
        """The compound being fitted. Only meaningful while is_pitting is True."""
        return self._pit.new_compound
